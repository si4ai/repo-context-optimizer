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
"""
from __future__ import annotations

import json
import posixpath
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Mapping

from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.modules import JS_EXT, dir_of

MANIFEST_NAMES = ("package.json", "go.mod", "Cargo.toml")
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
        elif ch in " \t\n;|&" and depth == 0:
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


def repo_facts(paths: frozenset[str], sources: Mapping[str, bytes]) -> RepoFacts:
    js: dict[str, str] = {}
    mains: dict[str, str] = {}
    go: list[tuple[str, str]] = []
    rs: dict[str, str] = {}
    roots: list[str] = []
    for p in sorted(paths):
        name = PurePosixPath(p).name
        if name not in MANIFEST_NAMES or p not in sources:
            continue
        d = dir_of(p)
        text = sources[p].decode("utf-8-sig", "replace")
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
        else:
            try:
                pkg = tomllib.loads(text).get("package")
            except tomllib.TOMLDecodeError:
                continue
            if isinstance(pkg, dict) and isinstance(pkg.get("name"), str):
                src = "src" if d == "." else f"{d}/src"
                rs.setdefault(pkg["name"].replace("-", "_"), src)
                roots.append(src)
    go.sort(key=lambda x: (-len(x[0]), x[0]))
    roots = sorted(set(roots), key=lambda x: (-len(x), x))
    return RepoFacts(frozenset(paths), js, mains, tuple(go), rs, tuple(roots))


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
    else:
        got = _sh(path, text, f, ps=path.endswith(".ps1"))
    got.discard(path)
    return tuple(sorted(got))
