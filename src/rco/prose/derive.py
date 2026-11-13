"""Derived prose: deterministic text from extracted facts, never from a model.

Used where nothing can be mined (no docstring, no leading comment, no
README). Two levels:

- symbol_doc: one line for a function, method or class with no doc, from
  its name, signature, the in-repo names it calls, and its in-repo callers.
- module_prose: one line for a Forest entry with no mined prose, from the
  names its Tree defines and what it owns.

Every name derived text cites comes from the Tree or the call index, so
the name-in-Tree check holds. Same inputs, same bytes.
"""
from __future__ import annotations

import ast
import re
import warnings
from dataclasses import dataclass, field, replace
from pathlib import PurePosixPath
from typing import Iterable, Mapping

from rco.extract.langs import lang_of
from rco.extract.scan import find_open, match_brace, scan
from rco.forest_model import PROSE_CAP
from rco.model import CONTAINERS, Kind, Node, Tree
from rco.prose.mine import cap

DERIVED_PROMPT_VERSION = "derived/1"
LIST_MAX = 4  # names per list before "+N more"
_FN_KINDS = (Kind.FUNCTION, Kind.METHOD)
_DOC_KINDS = (Kind.CLASS, Kind.FUNCTION, Kind.METHOD)
_KW = {"if", "for", "while", "switch", "catch", "return", "function", "typeof", "new", "await",
       "yield", "super", "this", "sizeof", "match", "loop", "fn", "func", "go", "defer", "select",
       "elif", "case", "do", "else", "in", "of", "delete", "void", "throw", "with", "not", "and",
       "or", "print", "len", "make", "append", "Some", "Ok", "Err", "None", "Box", "Vec", "String",
       "panic", "assert", "echo", "local", "export", "test", "set", "unset", "shift"}


# ------------------------------------------------------------ words and signatures

def name_words(name: str) -> str:
    """`build_map` -> "Build map"; `getJobById` -> "Get job by id"; `Install-Tools` -> "Install tools"."""
    base = name.strip("_$#")
    parts = re.split(r"[_\-\s.]+", base)
    words: list[str] = []
    for p in parts:
        words += [w for w in re.findall(r"[A-Z]+(?![a-z])|[A-Z]?[a-z0-9]+|[A-Z]", p) if w]
    if not words:
        return name
    out = [w if (w.isupper() and len(w) > 1) else w.lower() for w in words]
    out[0] = out[0][:1].upper() + out[0][1:]
    return " ".join(out)


def _split_top(s: str) -> list[str]:
    out, d, cur = [], 0, []
    for ch in s:
        if ch in "([{<":
            d += 1
        elif ch in ")]}>":
            d -= 1
        if ch == "," and d == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if "".join(cur).strip():
        out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def params_and_return(sig: str) -> tuple[list[str], str]:
    """Parameter names and the return type text from a sig, best effort."""
    s = sig.strip()
    if s.startswith("<") or s.startswith("["):  # type parameters
        d = 0
        for i, ch in enumerate(s):
            d += ch in "<[" and 1 or (-1 if ch in ">]" else 0)
            if d == 0:
                s = s[i + 1:].lstrip()
                break
    if not s.startswith("("):
        return [], ""
    d, end = 0, len(s)
    for i, ch in enumerate(s):
        if ch == "(":
            d += 1
        elif ch == ")":
            d -= 1
            if d == 0:
                end = i
                break
    names = []
    for p in _split_top(s[1:end]):
        p = re.sub(r"^\[[^\]]*\]\s*", "", p)  # PowerShell [type]$name
        m = re.match(r"[*&\s]*(?:mut\s+)?\$?([A-Za-z_][\w]*)", p)
        if not m:
            continue
        n = m.group(1)
        if n in ("self", "cls", "this", "mut") or p.strip() in ("*", "/"):
            continue
        names.append(n)
    rest = s[end + 1:].strip()
    rest = re.sub(r"\s+where\s.*$", "", rest)
    ret = ""
    if rest.startswith("->"):
        ret = rest[2:].strip()
    elif rest.startswith(":"):
        ret = rest[1:].strip()
    elif rest and not rest.startswith(("{", "=")):
        ret = rest  # Go: (args) T or (args) (T, error)
    if ret in ("None", "void", "()", "Unit"):
        ret = ""
    return names, ret


def _names(xs: Iterable[str], limit: int = LIST_MAX, keep_order: bool = False) -> str:
    xs = list(dict.fromkeys(xs)) if keep_order else sorted(set(xs))
    shown = ", ".join(xs[:limit])
    return shown + (f" (+{len(xs) - limit} more)" if len(xs) > limit else "")


def _counted(word: str, xs: Iterable[str]) -> str:
    xs = set(xs)
    return f"{word}{'' if len(xs) == 1 else 's'} {_names(xs, 3)}"


def symbol_doc(n: Node, calls: Iterable[str] = (), callers: Iterable[str] = ()) -> str:
    """One deterministic line for a symbol from its name, signature, calls and callers."""
    bits = [name_words(n.name) + "."]
    if n.kind in _FN_KINDS:
        params, ret = params_and_return(n.sig)
        if params:
            bits.append(f"Takes {_names(params, 6, keep_order=True)}.")
        if ret:
            bits.append(f"Returns {ret}.")
    elif n.kind is Kind.CLASS:
        kind = dict(n.attrs).get("kind", "class")
        members = [c.name for c in n.children if c.kind in _FN_KINDS]
        fields = [c.name for c in n.children if c.kind is Kind.FIELD]
        if n.sig and kind in ("class", "struct", "trait", "interface"):
            bases = re.sub(r"^[(<\[].*?[)>\]]\s*", "", n.sig) if n.sig[:1] in "<[" else n.sig
            if bases.strip("()"):
                bits.append(f"{kind.capitalize()} over {bases.strip('()')}.")
        if fields:
            bits.append(f"Fields {_names(fields)}.")
        if members:
            bits.append(f"Methods {_names(members)}.")
    calls = sorted(set(calls) - {n.name})
    if calls:
        bits.append(f"Calls {_names(calls)}.")
    callers = sorted(set(callers))
    if callers:
        bits.append(f"Called by {_names(callers, 3)}.")
    return cap(" ".join(bits), PROSE_CAP)


def module_prose(tree: Tree, commands: Iterable[str] = (), tables: Iterable[str] = (),
                 routes: Iterable[str] = ()) -> str:
    """One deterministic line for a module from what its Tree defines and owns; "" if empty."""
    classes, funcs, consts = set(), set(), set()
    langs: set[str] = set()

    def visit(n: Node) -> None:
        if n.kind in CONTAINERS:
            langs.add(dict(n.attrs).get("lang", "python") if n.kind is Kind.SRCMODULE else "python")
            for c in n.children:
                visit(c)
        elif n.kind is Kind.CLASS:
            classes.add(n.name)
        elif n.kind is Kind.FUNCTION:
            funcs.add(n.name)
        elif n.kind is Kind.CONSTANT:
            consts.add(n.name)

    for top in tree.nodes:
        visit(top)
    bits = []
    if classes:
        bits.append(_counted("type", classes))
    if funcs:
        bits.append(_counted("function", funcs))
    if not bits and consts:
        bits.append(_counted("constant", consts))
    owned = []
    for label, xs in (("command", commands), ("table", tables), ("route", routes)):
        xs = list(xs)
        if xs:
            owned.append(_counted(label, xs))
    if not bits and not owned:
        return ""
    lead = ("Defines " + "; ".join(bits) + ".") if bits else ""
    own = ("Owns " + "; ".join(owned) + ".") if owned else ""
    lang = f" ({', '.join(sorted(langs))})" if len(langs) > 1 else ""
    return cap((lead + lang + " " + own).strip(), PROSE_CAP)


# ------------------------------------------------------------ call index

Sym = tuple[str, str]  # (path, qualname)


@dataclass
class CallIndex:
    """calls[sym] = labels of in-repo symbols it calls; callers[sym] = labels that call it."""

    calls: dict[Sym, set[str]] = field(default_factory=dict)
    callers: dict[Sym, set[str]] = field(default_factory=dict)


def _label(path: str, qn: str) -> str:
    return f"{PurePosixPath(path).name}:{qn}"


def _py_calls(path: str, data: bytes, defs: Mapping[str, set[str]],
              index: Mapping[str, str]) -> dict[str, set[Sym]]:
    """qualname -> resolved in-repo callees, for top-level functions and methods.

    Resolved: a bare name defined in the same file or brought in by
    `from X import name`; `mod.name()` where mod is an imported in-repo
    module; `self.m()` and `cls.m()` within the same class. A method call
    on any other receiver is not resolved, so it is not counted.
    """
    from rco.imports import _package_of
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(data, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {}
    file_names: dict[str, Sym] = {n: (path, n) for n in defs.get(path, set()) if "." not in n}
    file_mods: dict[str, str] = {}

    def bind(st: ast.AST, names: dict[str, Sym], mods: dict[str, str]) -> None:
        if isinstance(st, ast.Import):
            for a in st.names:
                hit = index.get(a.name)
                if hit and (a.asname or "." not in a.name):
                    mods[a.asname or a.name] = hit
            return
        if st.level:
            pkg = _package_of(path)
            up = st.level - 1
            if up > len(pkg):
                return
            base = pkg[: len(pkg) - up] + ((st.module or "").split(".") if st.module else [])
        else:
            base = (st.module or "").split(".")
        for a in st.names:
            local = a.asname or a.name
            sub = index.get(".".join(base + [a.name]))
            if sub:
                mods[local] = sub
                continue
            src = index.get(".".join(base)) if base and base != [""] else None
            if src and a.name in defs.get(src, set()):
                names[local] = (src, a.name)

    for st in tree.body:
        if not isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            for x in ast.walk(st):
                if isinstance(x, (ast.Import, ast.ImportFrom)):
                    bind(x, file_names, file_mods)

    def called(fn: ast.AST, cls: str) -> set[Sym]:
        """One walk: imports inside the function count for its own calls."""
        names, mods = dict(file_names), dict(file_mods)
        calls: list[ast.Call] = []
        for x in ast.walk(fn):
            if isinstance(x, ast.Call):
                calls.append(x)
            elif isinstance(x, (ast.Import, ast.ImportFrom)):
                bind(x, names, mods)
        got: set[Sym] = set()
        for x in calls:
            f = x.func
            if isinstance(f, ast.Name) and f.id in names:
                got.add(names[f.id])
            elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name):
                recv = f.value.id
                if recv in ("self", "cls") and cls and f"{cls}.{f.attr}" in defs.get(path, set()):
                    got.add((path, f"{cls}.{f.attr}"))
                elif recv in mods and f.attr in defs.get(mods[recv], set()):
                    got.add((mods[recv], f.attr))
        return got

    out: dict[str, set[Sym]] = {}
    for st in tree.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out[st.name] = called(st, "")
        elif isinstance(st, ast.ClassDef):
            for m in st.body:
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out[f"{st.name}.{m.name}"] = called(m, st.name)
    return out


_DECL = {  # declaration shapes per language; {n} is the name
    "js": (r"function\s*\*?\s*{n}\s*[(<]",
           r"\b{n}\s*=\s*(?:async\s+)?(?:function\b|\(|[A-Za-z_$][\w$]*\s*=>)",
           r"^[ \t]*(?:(?:static|async|get|set|public|readonly|override)\s+)*{n}\s*[(<]"),
    "go": (r"^func\s*(?:\([^)]*\)\s*)?{n}\s*[\[(]",),
    "rust": (r"\bfn\s+{n}\b",),
    "sh": (r"^[ \t]*function\s+{n}\b", r"^[ \t]*{n}\s*\(\s*\)"),
}


def _alts(lang: str) -> tuple[str, ...]:
    return _DECL[lang]


def _src_calls(path: str, data: bytes, fn_names: Iterable[str]) -> dict[str, set[str]]:
    """qualname -> bare names called in its body (non-Python, best effort)."""
    lang = lang_of(path)
    if not lang:
        return {}
    s = scan(data.decode("utf-8-sig", errors="replace"), lang)
    flags = re.M | (re.I if path.endswith(".ps1") else 0)
    wanted = sorted({qn.rsplit(".", 1)[-1] for qn in fn_names}, key=lambda x: (-len(x), x))
    if not wanted:
        return {}
    alt = "(?P<n>" + "|".join(re.escape(w) for w in wanted) + ")"
    rx = re.compile("|".join(f"(?:{a.replace('{n}', alt.replace('?P<n>', f'?P<n{i}>'))})"
                             for i, a in enumerate(_alts(lang))),
                    flags)
    first: dict[str, int] = {}
    for m in rx.finditer(s.code):
        name = next(v for k, v in m.groupdict().items() if v is not None)
        first.setdefault(name, m.end())
    out: dict[str, set[str]] = {}
    for qn in sorted(set(fn_names)):
        name = qn.rsplit(".", 1)[-1]
        if name not in first:
            continue
        o = find_open(s.code, first[name], stops=";")
        if o < 0:
            continue
        body = s.code[o + 1:match_brace(s.code, o)]
        if lang == "sh":
            got = set(re.findall(r"(?:^|[\s;|&(`])([\w.:-]+)(?=\s|$|;)", body, re.M))
        else:
            got = set(re.findall(r"([A-Za-z_$][\w$]*)\s*(?:::<[^>]*>)?\s*\(", body))
        out[qn] = got - _KW
    return out


def _defs(tree: Tree) -> dict[str, set[str]]:
    """path -> qualnames of CLASS, FUNCTION and METHOD nodes (Class.method)."""
    out: dict[str, set[str]] = {}

    def rec(path: str, prefix: str, n: Node) -> None:
        if n.kind in _DOC_KINDS:
            out.setdefault(path, set()).add(prefix + n.name)
        child = "" if n.kind in CONTAINERS else (prefix + n.name + "." if n.kind is Kind.CLASS else prefix)
        for c in n.children:
            rec(path, child, c)

    for top in tree.nodes:
        rec(top.path, "", top)
    return out


def call_index(trees: Iterable[Tree], sources: Mapping[str, bytes],
               focus: Iterable[Tree] | None = None) -> CallIndex:
    """Calls and callers between the symbols the Trees define, resolved per language.

    With focus, only the calls of the focus Trees' symbols and their callers are
    complete; files that cannot mention a focus name are skipped (same result for
    those symbols, less work).
    """
    from rco.imports import _dotted_index
    defs: dict[str, set[str]] = {}
    for t in trees:
        for path, qns in _defs(t).items():
            defs.setdefault(path, set()).update(qns)
    keep = None
    if focus is not None:
        fdefs: dict[str, set[str]] = {}
        for t in focus:
            for path, qns in _defs(t).items():
                fdefs.setdefault(path, set()).update(qns)
        names = sorted({q.rsplit(".", 1)[-1] for qs in fdefs.values() for q in qs})
        rx = re.compile(b"|".join(re.escape(n.encode()) for n in names)) if names else None
        keep = {p for p in sources
                if p in fdefs or (rx is not None and rx.search(sources[p]) is not None)}
    index = _dotted_index([p for p in sources if p.endswith(".py")])
    # non-Python bare names resolve to the same file first, else to a unique definer
    by_name: dict[str, set[Sym]] = {}
    for path, qns in defs.items():
        if path.endswith(".py"):
            continue
        for qn in qns:
            if "." not in qn:
                by_name.setdefault(qn, set()).add((path, qn))
    idx = CallIndex()
    for path in sorted(sources):
        if path not in defs or (keep is not None and path not in keep):
            continue
        data = sources[path]
        if path.endswith(".py"):
            per = _py_calls(path, data, defs, index)
        else:
            raw = _src_calls(path, data, [q for q in defs[path]])
            per = {}
            for qn, names in raw.items():
                got: set[Sym] = set()
                for nm in names:
                    if nm in defs[path] and nm != qn.rsplit(".", 1)[-1]:
                        got.add((path, nm))
                    elif len(by_name.get(nm, ())) == 1:
                        got |= by_name[nm]
                per[qn] = got
        for qn in sorted(per):
            got = {c for c in per[qn] if c != (path, qn)}
            idx.calls[(path, qn)] = {_label(*c) if c[0] != path else c[1] for c in got}
            for c in got:
                idx.callers.setdefault(c, set()).add(_label(path, qn))
    return idx


def with_derived_docs(t: Tree, idx: CallIndex) -> Tree:
    """Copy of t where every class, function and method with no doc gets a derived one.

    A derived doc is marked with the attr derived_doc=1. Docs already present are kept.
    """
    def rec(path: str, prefix: str, n: Node) -> Node:
        child_prefix = "" if n.kind in CONTAINERS else (
            prefix + n.name + "." if n.kind is Kind.CLASS else prefix)
        kids = tuple(rec(path, child_prefix, c) for c in n.children)
        n = replace(n, children=kids)
        if n.kind in _DOC_KINDS and not n.doc:
            qn = prefix + n.name
            text = symbol_doc(n, idx.calls.get((path, qn), ()), idx.callers.get((path, qn), ()))
            n = replace(n, doc=text, attrs=tuple(sorted(n.attrs + (("derived_doc", "1"),))))
        return n

    return replace(t, nodes=tuple(rec(top.path, "", top) for top in t.nodes))
