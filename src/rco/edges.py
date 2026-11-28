"""Import edges for non-Python files (Forest; README "depends-on and used-by").

Each rule reads one file's bytes at a SHA and returns the in-repo paths it
imports. Only edges that resolve to a file in the repo count; packages from
a registry, the standard library and anything computed at run time are
ignored. Comments and strings are masked first (rco.extract.scan), so a
commented-out import adds no edge.

- JS/TS: `import ... from "x"`, `import "x"`, `export ... from "x"`,
  `require("x")`, `import("x")`. A relative specifier resolves against the
  file's directory, trying the path as is, with each JS/TS extension, and
  as a directory index. A bare specifier resolves when its first segment
  (or `@scope/name`) is the `name` of an in-repo package.json.
- Go: an import path under a go.mod `module` path maps to that directory.
- Rust: `mod x;` maps to x.rs or x/mod.rs beside the file; `use crate::`,
  `use super::` and `use self::` paths map to the longest matching file
  under the crate root; `use other_crate::` maps to an in-repo crate's
  lib.rs, by its Cargo.toml package name.
- Shell: `source x` and `. x` with a literal path, relative to the file's
  directory or the repo root ($(dirname "$0"), $SCRIPT_DIR and similar
  prefixes are read as the file's directory). PowerShell: `. $PSScriptRoot/x`
  and `Import-Module` with a relative path.
- Java: `import a.b.C;` (and `import static a.b.C.m;`, nested `a.b.C.D`) maps
  to the file whose path ends in a/b/C.java, under whatever source root the
  repo uses (src/main/java, src/, a Maven or Gradle module). The importer's
  own `package` line says which root it sits in, and a match under that root
  wins; otherwise the nearest one. `import a.b.*;` maps to the files of that
  package the importer names. A same-package class the importer names needs
  no import and still counts.
- C#: `using N;` (`using static N.T;`, `using A = N.T;`, `global using N;`) and
  the file's own namespace with its parents map to the files that declare a
  type the importer names in that namespace, so no `using` is needed inside
  the namespace. A project's .csproj ProjectReference edges (followed
  transitively) limit which projects a file can see; a file outside every
  project sees all.
- PHP: `use A\\B\\C;` (also group, `function` and `const` uses, aliases) and a
  name the file writes in its own namespace map through composer.json
  autoload psr-4 and autoload-dev (the longest matching prefix, every
  composer.json of the repo, relative to its own folder) to a file; a class
  no prefix finds is looked up by the namespace and name its file declares,
  which also covers classmap directories. `require` and `include` with a
  literal path map directly; a dynamic path adds no edge.
"""
from __future__ import annotations

import json
import posixpath
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Mapping

from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.modules import JS_EXT, dir_of

MANIFEST_NAMES = ("package.json", "go.mod", "Cargo.toml", "composer.json")
MANIFEST_SUFFIXES = (".csproj",)


def is_manifest(path: str) -> bool:
    """A manifest the edge rules read: a named file, or one with a project suffix (.csproj)."""
    name = path.rsplit("/", 1)[-1]
    return name in MANIFEST_NAMES or name.endswith(MANIFEST_SUFFIXES)
_JS_SPEC = re.compile(
    r"""(?:\bimport\s+(?:type\s+)?(?:[\w$*{}\s,]+?\s+from\s+)?|\bexport\s+(?:type\s+)?[\w$*{}\s,]+?\s+from\s+"""
    r"""|\brequire\s*\(\s*|\bimport\s*\(\s*)(["'])(?P<spec>[^"'\n]+)\1""")
_GO_IMPORT_BLOCK = re.compile(r"\bimport\s*\((?P<body>[^)]*)\)", re.S)
_GO_IMPORT_ONE = re.compile(r"\bimport\s+(?:[\w.]+\s+)?\"(?P<path>[^\"]+)\"")
_GO_PATH = re.compile(r"\"([^\"]+)\"")
_RS_MOD = re.compile(r"^\s*(?:pub(?:\s*\([^)]*\))?\s+)?mod\s+(\w+)\s*;", re.M)
_RS_USE = re.compile(r"\buse\s+(?P<path>[\w:]+)")
_SH_SOURCE = re.compile(r"(?:^|[;&|]\s*|\bthen\s+|\bdo\s+)[ \t]*(?:source|\.)[ \t]+(?P<arg>\S)",
                        re.M)
_PS_SOURCE = re.compile(r"(?:^|;)[ \t]*(?:\.[ \t]+|Import-Module[ \t]+(?:-Name[ \t]+)?)(?P<arg>\S)",
                        re.M | re.I)


def _shell_word(text: str, i: int) -> str:
    """The shell word starting at i: quotes and $( ... ) may hold spaces."""
    out, depth, q = [], 0, ""
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text) and not q == "'":
            out.append(text[i:i + 2])
            i += 2
            continue
        if q:
            if ch == q and depth == 0:
                q = ""
            elif text.startswith("$(", i) and q == '"':
                depth += 1
            elif ch == ")" and depth:
                depth -= 1
            out.append(ch)
            i += 1
            continue
        if text.startswith("$(", i):
            depth += 1
        elif ch == ")" and depth:
            depth -= 1
        elif ch in "\"'" and depth == 0:
            q = ch
        elif ch in " \t\r\n;|&" and depth == 0:
            break
        out.append(ch)
        i += 1
    return "".join(out)
_SH_DIR_PREFIX = re.compile(
    r"""^["']?(?:\$\(\s*dirname\s+["']?\$(?:0|\{?BASH_SOURCE(?:\[0\])?\}?)["']?\s*\)|"""
    r"""\$\{BASH_SOURCE(?:\[0\])?%/\*\}|\$\{0%/\*\}|\$\{?(?:SCRIPT_DIR|SCRIPTDIR|HERE|DIR|BASEDIR)\}?|"""
    r"""\$PSScriptRoot|\$\{PSScriptRoot\})["']?[/\\]""", re.I)


@dataclass(frozen=True)
class RepoFacts:
    """What edge rules need beyond one file: every path, plus manifest facts."""

    paths: frozenset[str]
    js_packages: Mapping[str, str]  # package.json name -> its directory
    js_mains: Mapping[str, str]  # package.json name -> its main or module entry, repo path
    go_modules: tuple[tuple[str, str], ...]  # (module path, directory), longest first
    rust_crates: Mapping[str, str]  # crate name (with _) -> crate src root directory
    rust_roots: tuple[str, ...]  # crate src root directories, longest first
    java_stems: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # stem -> paths of stem.java
    java_dirs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # directory -> .java paths
    java_pkg: Mapping[str, str] = field(default_factory=dict)  # .java path -> its package as a path
    java_pkgs: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # package path -> .java paths
    cs_types: Mapping[str, Mapping[str, tuple[str, ...]]] = field(default_factory=dict)  # ns -> type -> files
    cs_global: tuple[str, ...] = ()  # namespaces every file uses (global using)
    cs_projects: tuple[str, ...] = ()  # directories holding a .csproj, longest first
    cs_visible: Mapping[str, frozenset[str]] = field(default_factory=dict)  # project dir -> dirs it sees
    php_psr4: tuple[tuple[str, str], ...] = ()  # (namespace prefix with \\, directory), longest first
    php_decls: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # fq class or function -> files
    php_shorts: frozenset[str] = frozenset()  # the last segment of every php_decls key


def repo_facts(paths: frozenset[str], sources: Mapping[str, bytes]) -> RepoFacts:
    js: dict[str, str] = {}
    mains: dict[str, str] = {}
    go: list[tuple[str, str]] = []
    rs: dict[str, str] = {}
    roots: list[str] = []
    psr4: list[tuple[str, str]] = []
    csproj: dict[str, str] = {}
    for p in sorted(paths):
        name = PurePosixPath(p).name
        if not is_manifest(p) or p not in sources:
            continue
        d = dir_of(p)
        text = sources[p].decode("utf-8-sig", "replace")
        if name == "composer.json":
            psr4 += _composer_psr4(d, text)
            continue
        if name.endswith(".csproj"):
            csproj[p] = text
            continue
        if name == "package.json":
            try:
                obj = json.loads(text)
            except ValueError:
                continue
            if isinstance(obj, dict) and isinstance(obj.get("name"), str):
                js.setdefault(obj["name"], d)
                for key in ("module", "main"):
                    entry = obj.get(key)
                    if isinstance(entry, str) and entry.strip():
                        tgt = _norm(posixpath.join("" if d == "." else d, entry.strip()))
                        if tgt:
                            mains.setdefault(obj["name"], tgt)
                        break
        elif name == "go.mod":
            m = re.search(r"^\s*module\s+(\S+)", text, re.M)
            if m:
                go.append((m.group(1).strip('"'), d))
        elif name == "Cargo.toml":
            try:
                pkg = tomllib.loads(text).get("package")
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(pkg, dict) and isinstance(pkg.get("name"), str):
                src = "src" if d == "." else f"{d}/src"
                rs.setdefault(pkg["name"].replace("-", "_"), src)
                roots.append(src)
    def _go_dir_depth(d: str) -> int:
        return 0 if d == "." else d.count("/") + 1

    # longest module path first; same path prefers the shallower tree (card 1350)
    go.sort(key=lambda x: (-len(x[0]), _go_dir_depth(x[1]), x[0], x[1]))
    roots = sorted(set(roots), key=lambda x: (-len(x), x))
    stems: dict[str, list[str]] = {}
    jdirs: dict[str, list[str]] = {}
    jpkg: dict[str, str] = {}
    jpkgs: dict[str, list[str]] = {}
    decls: dict[str, list[str]] = {}
    for p in sorted(paths):
        if p.endswith(".php") and p in sources:
            for fq in _php_file_facts(sources[p].decode("utf-8-sig", "replace"))[1]:
                decls.setdefault(fq, []).append(p)
        if p.endswith(".java"):
            stems.setdefault(PurePosixPath(p).stem, []).append(p)
            jdirs.setdefault(dir_of(p), []).append(p)
            if p in sources:
                pm = _JAVA_PACKAGE.search(scan(sources[p].decode("utf-8-sig", "replace"), "java").code)
                jpkg[p] = re.sub(r"\s+", "", pm.group("name")).replace(".", "/") if pm else ""
                jpkgs.setdefault(jpkg[p], []).append(p)
    php_decls = {k: tuple(v) for k, v in decls.items()}
    cs_types, cs_global = _cs_index(paths, sources)
    cs_projects, cs_visible = _cs_projects(csproj)
    return RepoFacts(frozenset(paths), js, mains, tuple(go), rs, tuple(roots),
                     {k: tuple(v) for k, v in stems.items()},
                     {k: tuple(v) for k, v in jdirs.items()}, jpkg,
                     {k: tuple(v) for k, v in jpkgs.items()},
                     cs_types, cs_global, cs_projects, cs_visible,
                     tuple(sorted(set(psr4), key=lambda x: (-len(x[0]), x))), php_decls,
                     frozenset(k.rsplit("\\", 1)[-1] for k in php_decls))


_CS_ID = r"[A-Za-z_]\w*"
_CS_NS = re.compile(rf"\bnamespace\s+(?P<name>{_CS_ID}(?:\s*\.\s*{_CS_ID})*)\s*[{{;]")
_CS_TYPE = re.compile(rf"\b(?:class|struct|interface|enum|record(?:\s+(?:class|struct))?)\s+"
                      rf"(?P<name>{_CS_ID})\s*(?=[<:{{(;]|where\b|$)", re.M)
_CS_DELEGATE = re.compile(rf"\bdelegate\b[^;(){{}}]*?\b(?P<name>{_CS_ID})\s*(?:<[^>;(){{}}]*>)?\s*\(")
_CS_USING = re.compile(rf"\b(?P<g>global\s+)?using\s+(?:(?P<static>static\s+)|(?P<alias>{_CS_ID})\s*=\s*)?"
                       rf"(?P<name>{_CS_ID}(?:\s*\.\s*{_CS_ID})*)\s*;")
_CS_REF = re.compile(r"<ProjectReference\s[^>]*?Include\s*=\s*\"(?P<p>[^\"]+)\"", re.I)


def _cs_file_facts(text: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[tuple, ...]]:
    """(namespaces, type names, usings) a C# file declares; usings are (global, kind, alias, name)."""
    def compute():
        code = scan(text, "csharp").code
        nss = tuple(dict.fromkeys(re.sub(r"\s+", "", m.group("name")) for m in _CS_NS.finditer(code)))
        types = {m.group("name") for m in _CS_TYPE.finditer(code)} - {"where", "new", "class", "struct"}
        types |= {m.group("name") for m in _CS_DELEGATE.finditer(code)}
        uses = tuple((bool(m.group("g")), "static" if m.group("static") else ("alias" if m.group("alias") else "ns"),
                      m.group("alias") or "", re.sub(r"\s+", "", m.group("name")))
                     for m in _CS_USING.finditer(code))
        return nss, tuple(sorted(types)), uses
    from rco import memo
    return memo.cached("cs_facts", text, compute)


def _cs_index(paths: frozenset[str], sources: Mapping[str, bytes]):
    types: dict[str, dict[str, list[str]]] = {}
    glob: list[str] = []
    for p in sorted(paths):
        if not p.endswith(".cs") or p not in sources:
            continue
        nss, tnames, uses = _cs_file_facts(sources[p].decode("utf-8-sig", "replace"))
        for ns in nss or ("",):
            bucket = types.setdefault(ns, {})
            for t in tnames:
                bucket.setdefault(t, []).append(p)
        glob += [n for g, k, _a, n in uses if g and k == "ns"]
    return ({ns: {t: tuple(v) for t, v in b.items()} for ns, b in types.items()},
            tuple(sorted(set(glob))))


def _cs_projects(csproj: Mapping[str, str]):
    dirs = sorted({dir_of(p) for p in csproj}, key=lambda x: (-len(x), x))
    refs: dict[str, set[str]] = {d: set() for d in dirs}
    for p, text in csproj.items():
        for m in _CS_REF.finditer(text):
            tgt = _norm(posixpath.join("" if dir_of(p) == "." else dir_of(p), m.group("p").replace("\\", "/")))
            if tgt:
                refs[dir_of(p)].add(dir_of(tgt))
    seen: dict[str, frozenset[str]] = {}
    for d in dirs:  # transitive closure, the project itself included
        todo, got = [d], {d}
        while todo:
            for n in refs.get(todo.pop(), ()):
                if n not in got:
                    got.add(n)
                    todo.append(n)
        seen[d] = frozenset(got)
    return tuple(dirs), seen


def _cs_project_of(path: str, f: RepoFacts) -> str | None:
    return next((d for d in f.cs_projects if d == "." or path.startswith(d + "/")), None)


def _cs(path: str, text: str, f: RepoFacts) -> set[str]:
    if not f.cs_types:
        return set()
    nss, _types, uses = _cs_file_facts(text)
    idents = set(re.findall(_CS_ID, scan(text, "csharp").code))
    me = _cs_project_of(path, f)
    sees = f.cs_visible.get(me) if me is not None else None
    visible: dict[str, None] = {}
    for ns in nss or ("",):  # the file's own namespace and its parents need no using
        parts = ns.split(".") if ns else []
        for i in range(len(parts), -1, -1):
            visible[".".join(parts[:i])] = None
    for ns in f.cs_global:
        visible[ns] = None
    named: list[tuple[str, str]] = []  # (namespace, type) a using names directly
    for _g, kind, _alias, name in uses:
        if kind == "ns":
            visible[name] = None
        else:  # using static N.T; using A = N.T;
            ns, _, t = name.rpartition(".")
            named.append((ns, t))
            visible[name] = None
    hits: set[str] = set()

    def take(files: tuple[str, ...]) -> None:
        keep = [c for c in files if c != path and (sees is None or _cs_project_of(c, f) is None
                                                    or _cs_project_of(c, f) in sees)]
        if not keep:
            return
        def pkey(pr: str) -> tuple:  # own project, then nearest; projects that tie all stay
            return (pr != (me or ""), -_common_dirs(pr, dir_of(path)))
        best = min(pkey(_cs_project_of(c, f) or "") for c in keep)
        hits.update(c for c in keep if pkey(_cs_project_of(c, f) or "") == best)

    for ns in visible:
        bucket = f.cs_types.get(ns)
        if not bucket:
            continue
        for t in idents.intersection(bucket) if len(idents) < len(bucket) else (x for x in bucket if x in idents):
            take(bucket[t])
    for ns, t in named:
        files = f.cs_types.get(ns, {}).get(t)
        if files:
            take(files)
    return hits


def _norm(path: str) -> str | None:
    p = posixpath.normpath(path)
    if p.startswith("../") or p == ".." or p.startswith("/"):
        return None
    return p.removeprefix("./")


def _js_candidates(base: str) -> list[str]:
    out = [base] + [base + e for e in JS_EXT] + [f"{base}/index{e}" for e in JS_EXT]
    stem, ext = posixpath.splitext(base)
    if ext in (".js", ".mjs", ".cjs", ".jsx"):  # TS sources imported with a .js suffix
        out += [stem + e for e in (".ts", ".tsx", ".mts", ".cts")]
    return out


def _js(path: str, text: str, f: RepoFacts) -> set[str]:
    s = scan(text, "js")
    hits: set[str] = set()
    for m in _JS_SPEC.finditer(s.nc):
        if s.code[m.start()] != s.nc[m.start()]:
            continue
        spec = m.group("spec").split("?", 1)[0].split("#", 1)[0]
        if spec.startswith("."):
            base = _norm(posixpath.join(dir_of(path) if dir_of(path) != "." else "", spec))
        else:
            parts = spec.split("/")
            name = "/".join(parts[:2]) if spec.startswith("@") else parts[0]
            root = f.js_packages.get(name)
            if root is None:
                continue
            rest = "/".join(parts[2:] if spec.startswith("@") else parts[1:])
            if not rest:
                bases = ([f.js_mains[name]] if name in f.js_mains else []) + [
                    _norm(posixpath.join("" if root == "." else root, x)) for x in ("index", "src/index")]
                hit = next((c for b in bases if b for c in _js_candidates(b) if c in f.paths), None)
                if hit:
                    hits.add(hit)
                continue
            base = _norm(posixpath.join("" if root == "." else root, rest))
        if base is None:
            continue
        for c in _js_candidates(base):
            if c in f.paths:
                hits.add(c)
                break
    return hits


def _go(path: str, text: str, f: RepoFacts) -> set[str]:
    s = scan(text, "go")
    specs: list[str] = []
    for m in _GO_IMPORT_BLOCK.finditer(s.code):
        specs += _GO_PATH.findall(s.nc[m.start("body"):m.end("body")])
    for m in _GO_IMPORT_ONE.finditer(s.code):
        specs.append(s.nc[m.start("path"):m.end("path")])
    hits: set[str] = set()
    for spec in specs:
        for mod, d in f.go_modules:
            if spec == mod or spec.startswith(mod + "/"):
                rest = spec[len(mod):].lstrip("/")
                target = rest if d == "." else (f"{d}/{rest}" if rest else d)
                target = target or "."
                go_files = sorted(p for p in f.paths if dir_of(p) == target and p.endswith(".go")
                                  and not p.endswith("_test.go"))
                if go_files:
                    hits.add(go_files[0])
                break
    return hits


def _rs_file(base: str, f: RepoFacts) -> str | None:
    for c in (base + ".rs", base + "/mod.rs"):
        if c in f.paths:
            return c
    return None


def _rs(path: str, text: str, f: RepoFacts) -> set[str]:
    s = scan(text, "rust")
    hits: set[str] = set()
    here = dir_of(path)
    stem = PurePosixPath(path).stem
    # a file's child modules live beside it (mod.rs, lib.rs, main.rs) or in a same-named dir
    child_dir = here if stem in ("mod", "lib", "main") else (f"{here}/{stem}" if here != "." else stem)
    for m in _RS_MOD.finditer(s.code):
        t = _rs_file(f"{child_dir}/{m.group(1)}" if child_dir != "." else m.group(1), f)
        if t:
            hits.add(t)
    root = next((r for r in f.rust_roots if path.startswith(r + "/")), None)
    for m in _RS_USE.finditer(s.code):
        parts = [x for x in m.group("path").split("::") if x]
        if not parts:
            continue
        head = parts[0]
        if head == "crate" and root:
            base, rest = root, parts[1:]
        elif head == "super":
            base, rest = dir_of(child_dir) if child_dir != "." else ".", parts[1:]
            while rest and rest[0] == "super":
                base, rest = dir_of(base), rest[1:]
        elif head == "self":
            base, rest = child_dir, parts[1:]
        elif head in f.rust_crates and f.rust_crates[head] != root:
            base, rest = f.rust_crates[head], parts[1:]
            lib = f"{base}/lib.rs"
            if not rest and lib in f.paths:
                hits.add(lib)
                continue
        else:
            continue
        best = None
        for i in range(len(rest), 0, -1):
            cand = _rs_file("/".join(([] if base == "." else [base]) + rest[:i]), f)
            if cand:
                best = cand
                break
        if best is None and head not in ("crate", "super", "self"):
            lib = f"{base}/lib.rs"
            best = lib if lib in f.paths else None
        if best:
            hits.add(best)
    return hits


def _sh(path: str, text: str, f: RepoFacts, ps: bool) -> set[str]:
    s = scan(text, "sh")
    hits: set[str] = set()
    rx = _PS_SOURCE if ps else _SH_SOURCE
    for m in rx.finditer(s.nc):
        if s.code[m.start("arg")] == " ":
            continue
        arg = _shell_word(s.nc, m.start("arg")).rstrip(";")
        rel_to_file = False
        stripped = _SH_DIR_PREFIX.sub("", arg)
        if stripped != arg:
            rel_to_file, arg = True, stripped
        arg = arg.replace('"', "").replace("'", "").replace("\\", "/")
        if not arg or "$" in arg or "`" in arg or arg.startswith("/"):
            continue
        here = dir_of(path)
        cands = [posixpath.join("" if here == "." else here, arg)]
        if not rel_to_file:
            cands.append(arg)
        for c in cands:
            c = _norm(c)
            if c is None:
                continue
            for cand in (c, c + ".sh", c + ".ps1", c + ".psm1"):
                if cand in f.paths:
                    hits.add(cand)
                    break
            else:
                continue
            break
    return hits


_JAVA_ID = r"[A-Za-z_$][\w$]*"
_JAVA_IMPORT = re.compile(rf"\bimport\s+(?:static\s+)?(?P<name>{_JAVA_ID}(?:\s*\.\s*{_JAVA_ID})*)"
                          r"(?P<star>\s*\.\s*\*)?\s*;")
_JAVA_PACKAGE = re.compile(rf"\bpackage\s+(?P<name>{_JAVA_ID}(?:\s*\.\s*{_JAVA_ID})*)\s*;")


def _common_dirs(a: str, b: str) -> int:
    n = 0
    for x, y in zip(a.split("/"), b.split("/")):
        if x != y:
            break
        n += 1
    return n


def _token_overlap(a: str, b: str) -> float:
    """How many of the words (split at / - _ .) two source roots have in common, 0 to 1."""
    x, y = set(re.split(r"[/\-_.]", a)), set(re.split(r"[/\-_.]", b))
    return len(x & y) / len(x | y) if x | y else 0.0


_PHP_ID = r"[A-Za-z_\x80-\uffff][\w\x80-\uffff]*"
_PHP_NS = re.compile(rf"\bnamespace\s+(?P<name>{_PHP_ID}(?:\\{_PHP_ID})*)\s*[;{{]")
_PHP_TYPE = re.compile(rf"(?<![:\w$>\\])(?P<kw>class|interface|trait|enum)\s+(?P<name>{_PHP_ID})")
_PHP_FUNC = re.compile(rf"^[ \t]{{0,4}}function[ \t]+&?[ \t]*(?P<name>{_PHP_ID})[ \t]*\(", re.M)
_PHP_USE = re.compile(rf"^[ \t]*use[ \t]+(?:(?P<kind>function|const)[ \t]+)?(?P<body>[^;]+);", re.M)
_PHP_REQ = re.compile(r"\b(?:require|include)(?:_once)?\b\s*\(?\s*(?:(?P<dir>__DIR__)\s*\.\s*)?(?P<q>['\"])(?P<path>[^'\"$]+)(?P=q)\s*\)?\s*;")
_PHP_NAME = re.compile(rf"\\?{_PHP_ID}(?:\\{_PHP_ID})*")


def _composer_psr4(d: str, text: str) -> list[tuple[str, str]]:
    """(prefix, directory) pairs from a composer.json: autoload and autoload-dev psr-4."""
    try:
        obj = json.loads(text)
    except ValueError:
        return []
    out: list[tuple[str, str]] = []
    if not isinstance(obj, dict):
        return out
    for key in ("autoload", "autoload-dev"):
        sect = obj.get(key)
        maps = sect.get("psr-4") if isinstance(sect, dict) else None
        if not isinstance(maps, dict):
            continue
        for prefix, dirs in maps.items():
            for sub in ([dirs] if isinstance(dirs, str) else dirs if isinstance(dirs, list) else []):
                if isinstance(sub, str) and isinstance(prefix, str):
                    tgt = _norm(posixpath.join("" if d == "." else d, sub))
                    if tgt is not None:
                        out.append((prefix.rstrip("\\") + "\\" if prefix else "", "." if tgt == "." else tgt))
    return out


def _php_file_facts(text: str):
    """(namespace, declared fq names, uses, requires): uses are (kind, alias, fq); requires (dir?, path)."""
    def compute():
        code = scan(text, "php").code
        nss = [(m.start(), m.group("name")) for m in _PHP_NS.finditer(code)]

        def ns_at(pos: int) -> str:
            cur = ""
            for at, nm in nss:
                if at > pos:
                    break
                cur = nm
            return cur

        def fq(pos: int, name: str) -> str:
            ns = ns_at(pos)
            return f"{ns}\\{name}" if ns else name
        decls = [fq(m.start(), m.group("name")) for m in _PHP_TYPE.finditer(code)
                 if m.group("name") not in ("extends", "implements") and not code[:m.start()].rstrip().endswith("new")]
        decls += [fq(m.start(), m.group("name")) + "()" for m in _PHP_FUNC.finditer(code)]
        uses: list[tuple[str, str, str]] = []
        for m in _PHP_USE.finditer(code):
            body = re.sub(r"\s+", " ", m.group("body")).strip()
            if body.startswith("(") or " function" in body[:1] :
                continue
            kind = m.group("kind") or "class"
            gm = re.match(r"(?P<pre>[^{]*?)\\?\s*\{(?P<items>.*)\}\s*$", body)
            items = []
            if gm:
                pre = gm.group("pre").strip().strip("\\")
                for it in gm.group("items").split(","):
                    it = it.strip()
                    k2 = kind
                    mm = re.match(r"(function|const)\s+(.*)", it)
                    if mm:
                        k2, it = mm.group(1), mm.group(2)
                    if it:
                        items.append((k2, pre + "\\" + it.strip("\\")))
            else:
                items = [(kind, it.strip().strip("\\")) for it in body.split(",") if it.strip()]
            for k2, it in items:
                am = re.match(rf"(?P<n>{_PHP_ID}(?:\\{_PHP_ID})*)(?:\s+as\s+(?P<a>{_PHP_ID}))?$", it)
                if am:
                    uses.append((k2, am.group("a") or am.group("n").rsplit("\\", 1)[-1], am.group("n")))
        reqs = tuple((bool(m.group("dir")), m.group("path")) for m in _PHP_REQ.finditer(scan(text, "php").nc))
        first = nss[0][1] if nss else ""
        return first, tuple(dict.fromkeys(decls)), tuple(uses), reqs
    from rco import memo
    return memo.cached("php_facts", text, compute)


def _php_psr4_file(fq: str, f: RepoFacts) -> str | None:
    for prefix, d in f.php_psr4:
        if fq.startswith(prefix) and len(fq) > len(prefix):
            cand = _norm(posixpath.join("" if d == "." else d, fq[len(prefix):].replace("\\", "/") + ".php"))
            if cand in f.paths:
                return cand
    return None


def _php_class(fq: str, f: RepoFacts, here: str) -> list[str]:
    """Files of a class, interface, trait or enum: composer psr-4 first, then the declaring file."""
    got = _php_psr4_file(fq, f)
    if got:
        return [got]
    hits = f.php_decls.get(fq, ())
    if len(hits) <= 1:
        return list(hits)
    best = max(_common_dirs(dir_of(c), here) for c in hits)
    return sorted(c for c in hits if _common_dirs(dir_of(c), here) == best)


def _php(path: str, text: str, f: RepoFacts) -> set[str]:
    here = dir_of(path)
    ns, _decls, uses, reqs = _php_file_facts(text)
    hits: set[str] = set()
    alias: dict[str, str] = {}
    for kind, al, fq in uses:
        if kind == "class":
            alias[al] = fq
            hits.update(_php_class(fq, f, here))
        elif kind == "function":
            hits.update(_php_class(fq + "()", f, here))
    if f.php_shorts:
        code = scan(text, "php").code
        for m in _PHP_NAME.finditer(code):
            tok = m.group(0)
            if tok.rsplit("\\", 1)[-1] not in f.php_shorts:
                continue
            if tok.startswith("\\"):
                fq = tok[1:]
            else:
                first, _, rest = tok.partition("\\")
                if first in alias:
                    fq = alias[first] + ("\\" + rest if rest else "")
                else:
                    fq = f"{ns}\\{tok}" if ns else tok
            hits.update(_php_class(fq, f, here))
    for rel_dir, rel in reqs:  # literal require/include paths
        base = here if rel_dir or not rel.startswith("/") else ""
        tgt = _norm(posixpath.join("" if base == "." else base, rel.lstrip("/") if rel_dir else rel))
        if tgt in f.paths:
            hits.add(tgt)
        elif not rel_dir and _norm(rel) in f.paths:
            hits.add(_norm(rel))
    return hits


def _java_pick(hits: list[str], root: str, here: str, tail: int) -> list[str]:
    """Several files match one name (android/ and guava/, say): own root, then nearest.
    Files that tie on every rule all stay, so path order never decides."""
    if len(hits) == 1:
        return hits

    def key(c: str) -> tuple:
        croot = c[:len(c) - tail].rstrip("/")
        # guava-tests/test sits nearer guava/src than android/ or the gwt super-source
        return (croot != root, -_common_dirs(dir_of(c), here), -_token_overlap(croot, root))
    best = min(key(c) for c in hits)
    return sorted(c for c in hits if key(c) == best)


def _java_pkg_files(f: RepoFacts, pkg: str, here: str | None = None) -> list[str]:
    """Files of one package (a/b for a.b). A file is in it when its own package line says so;
    a directory that only ends with the package path (a/b/util for `package util;`) does not
    count. A file whose package line is unknown (no source read) counts by directory, and the
    importer's own directory (here) counts for a file of the same package or of unknown one."""
    out = list(f.java_pkgs.get(pkg, ()))
    if pkg:
        out += [c for d in f.java_dirs if d == pkg or d.endswith("/" + pkg)
                for c in f.java_dirs[d] if c not in f.java_pkg]
    if here is not None:
        out += [c for c in f.java_dirs.get(here, ()) if f.java_pkg.get(c, pkg) == pkg]
    return sorted(set(out))


def _java_class(fq: str, f: RepoFacts, root: str, here: str) -> list[str]:
    """File of the class a dotted name points at: a.b.C, or a.b.C.member, or a.b.C.D."""
    parts = fq.split(".")
    for i in range(len(parts), 0, -1):
        suffix = "/".join(parts[:i]) + ".java"
        hits = [c for c in f.java_stems.get(parts[i - 1], ()) if c == suffix or c.endswith("/" + suffix)]
        if hits:
            return _java_pick(hits, root, here, len(suffix))
    return []


def _java(path: str, text: str, f: RepoFacts) -> set[str]:
    if not f.java_stems:
        return set()
    s = scan(text, "java")
    here = dir_of(path)
    root = here if here != "." else ""
    pm = _JAVA_PACKAGE.search(s.code)
    pdir = ""
    if pm:
        pdir = re.sub(r"\s+", "", pm.group("name")).replace(".", "/")
        if here == pdir:
            root = ""
        elif here.endswith("/" + pdir):
            root = here[:len(here) - len(pdir) - 1]
    idents = set(re.findall(_JAVA_ID, s.code))
    hits: set[str] = set()
    imported: set[str] = set()
    for m in _JAVA_IMPORT.finditer(s.code):
        fq = re.sub(r"\s+", "", m.group("name"))
        if not m.group("star"):
            imported.add(fq.rsplit(".", 1)[-1])
        t = _java_class(fq, f, root, here)
        if t and (not m.group("star") or fq.rsplit(".", 1)[-1][:1].isupper()):
            hits.update(t)
            continue
        if m.group("star"):  # a package: the classes of it this file names
            pkg = fq.replace(".", "/")
            by_dir: dict[str, list[str]] = {}
            for c in _java_pkg_files(f, pkg):
                if PurePosixPath(c).stem in idents:
                    by_dir.setdefault(dir_of(c), []).append(c)
            if by_dir:  # twin directories: the nearest ones; a tie keeps them all
                def dkey(x: str) -> tuple:
                    return (x != (root + "/" + pkg if root else pkg), -_common_dirs(x, here))
                best = min(dkey(x) for x in by_dir)
                for x in by_dir:
                    if dkey(x) == best:
                        hits.update(by_dir[x])
    # same package: no import needed, and the package may sit under another source root
    # (src/test/java names a class of src/main/java). A file with no package line is in the
    # default package, shared by every other file with none.
    by_stem: dict[str, list[str]] = {}
    for c in _java_pkg_files(f, pdir, here):
        stem = PurePosixPath(c).stem
        if stem in idents and stem not in imported:
            by_stem.setdefault(stem, []).append(c)
    for stem, cands in by_stem.items():
        hits.update(_java_pick(cands, root, here, len(pdir + "/" + stem + ".java") if pdir else len(stem) + 5))
    return hits


def file_edges(path: str, data: bytes, f: RepoFacts) -> tuple[str, ...]:
    """In-repo paths a non-Python file imports, sorted, itself excluded."""
    lang = lang_of(path)
    if not lang:
        return ()
    text = data.decode("utf-8-sig", errors="replace")
    if lang == "js":
        got = _js(path, text, f)
    elif lang == "go":
        got = _go(path, text, f)
    elif lang == "rust":
        got = _rs(path, text, f)
    elif lang == "java":
        got = _java(path, text, f)
    elif lang == "csharp":
        got = _cs(path, text, f)
    elif lang == "php":
        got = _php(path, text, f)
    else:
        got = _sh(path, text, f, ps=path.endswith(".ps1"))
    got.discard(path)
    return tuple(sorted(got))
