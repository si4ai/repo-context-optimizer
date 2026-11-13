"""Qualified references to Tree symbols, as "file:line" (rco diff, triage, notice).

A reference counts only when it resolves to the defining file and
qualname, so two unrelated classes that both have a field `base` never
share callers. Nothing is guessed from a bare attribute name.

Python (ast, one pass per file):
- a name bound to the symbol: defined in the same file, or brought in by
  `from M import name` (any alias);
- `mod.name` where mod is an imported in-repo module;
- members (`Cls.attr`, a method or field): `Cls.attr` and `mod.Cls.attr`;
  `self.attr` and `cls.attr` inside Cls's own methods; `x.attr` where, in
  the same function, x is a parameter or variable annotated `Cls`, or was
  assigned `Cls(...)`; and `Cls(attr=...)` keyword arguments for fields.
- An attribute on any other receiver is not resolved, so it is not
  counted: rco can miss a caller, but never invents one.

JS/TS, Go, Rust, shell and PowerShell (masked comments and strings,
rco.extract.scan): candidate files are the defining file, the files that
import it (rco.imports.file_graph), and for Go the files of the same
package directory. A top-level name counts as a whole-word use; a member
counts as `.name` (or `::name` in Rust). The declaration itself is not a
reference. Calls across languages are not modeled.
"""
from __future__ import annotations

import ast
import re
import warnings
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping

from rco.extract.langs import lang_of
from rco.extract.scan import scan
from rco.model import CONTAINERS, Kind, Map, qualname

SYMBOL_KINDS = frozenset({Kind.CLASS, Kind.FUNCTION, Kind.METHOD, Kind.FIELD, Kind.CONSTANT})


@dataclass(frozen=True, slots=True, order=True)
class Target:
    path: str  # defining file
    qn: str    # qualname within the file: "run", "Store.get"


def defs_of(m: Map) -> dict[str, dict[str, tuple[Kind, str]]]:
    """path -> qualname -> (kind, sig) for every symbol in the Map."""
    out: dict[str, dict[str, tuple[Kind, str]]] = defaultdict(dict)

    def rec(path: str, parent: str, n) -> None:
        qn = qualname(parent, n)
        if n.kind in SYMBOL_KINDS:
            out[path][qn] = (n.kind, n.sig)
        for c in n.children:
            rec(path, "" if n.kind in CONTAINERS else qn, c)

    for t in m.trees:
        for top in t.nodes:
            rec(top.path, "", top)
    return out


_BARE_TYPE = re.compile(r"""^['"]?([A-Za-z_]\w*)['"]?(?:\s*\|\s*None)?$""")


def _types(defs: Mapping[str, Mapping[str, tuple[Kind, str]]]) -> dict[Target, Target]:
    """A field's declared class, and a function's or method's return class.

    Read from the Tree sigs; only a class defined in the same file counts
    (`base: Affected`, `def make() -> Affected`), so no name is guessed.
    """
    from rco.prose.derive import params_and_return
    out: dict[Target, Target] = {}
    for path, qns in defs.items():
        classes = {q for q, (k, _) in qns.items() if k is Kind.CLASS}
        for q, (k, sig) in qns.items():
            if k in (Kind.FIELD, Kind.CONSTANT):
                text = sig.split(" [", 1)[0].strip()
            elif k in (Kind.FUNCTION, Kind.METHOD):
                text = params_and_return(sig)[1]
            else:
                continue
            m = _BARE_TYPE.match(text or "")
            if m and m.group(1) in classes:
                out[Target(path, q)] = Target(path, m.group(1))
    return out


# ------------------------------------------------------------------ python

class _PyFile:
    def __init__(self, path: str, tree: ast.Module, defs, index, types, want) -> None:
        self.path, self.defs, self.types, self.want = path, defs, types, want
        self.names: dict[str, Target] = {q: Target(path, q) for q in defs.get(path, {})
                                         if "." not in q}
        self.mods: dict[str, str] = {}
        self.hits: dict[Target, set[int]] = defaultdict(set)
        self.index = index

    def _bind(self, imports: list[ast.AST]) -> None:
        from rco.imports import _package_of
        index = self.index
        for st in imports:  # imports anywhere in the file bind file-wide
            if isinstance(st, ast.Import):
                for a in st.names:
                    hit = index.get(a.name)
                    if hit and (a.asname or "." not in a.name):
                        self.mods[a.asname or a.name] = hit
            elif isinstance(st, ast.ImportFrom):
                if st.level:
                    pkg = _package_of(self.path)
                    up = st.level - 1
                    if up > len(pkg):
                        continue
                    base = pkg[: len(pkg) - up] + (st.module.split(".") if st.module else [])
                else:
                    base = (st.module or "").split(".")
                for a in st.names:
                    local = a.asname or a.name
                    sub = index.get(".".join(base + [a.name]))
                    if sub:
                        self.mods[local] = sub
                        continue
                    src = index.get(".".join(base)) if base and base != [""] else None
                    if src and a.name in self.defs.get(src, {}):
                        self.names[local] = Target(src, a.name)

    # -- resolution
    def kind(self, t: Target | None) -> Kind | None:
        e = self.defs.get(t.path, {}).get(t.qn) if t else None
        return e[0] if e else None

    def symbol(self, x: ast.AST) -> Target | None:
        """The in-repo symbol an expression names (not a value's type)."""
        if isinstance(x, ast.Name):
            return self.names.get(x.id)
        if isinstance(x, ast.Attribute) and isinstance(x.value, ast.Name) and x.value.id in self.mods:
            p = self.mods[x.value.id]
            return Target(p, x.attr) if x.attr in self.defs.get(p, {}) else None
        return None

    def annotation(self, a: ast.AST | None) -> Target | None:
        if a is None:
            return None
        if isinstance(a, ast.Constant) and isinstance(a.value, str):
            try:
                a = ast.parse(a.value, mode="eval").body
            except SyntaxError:
                return None
        if isinstance(a, ast.BinOp) and isinstance(a.op, ast.BitOr):  # Cls | None
            return self.annotation(a.left) or self.annotation(a.right)
        t = self.symbol(a)
        return t if self.kind(t) is Kind.CLASS else None

    def type_of(self, x: ast.AST, typed: Mapping[str, Target], cls: Target | None) -> Target | None:
        """The in-repo class of a value, when it can be read off the code."""
        if isinstance(x, ast.Name):
            if x.id in ("self", "cls"):
                return cls
            if x.id in typed:
                return typed[x.id]
            t = self.symbol(x)
            return t if self.kind(t) is Kind.CLASS else None  # the class object itself
        if isinstance(x, ast.Call):
            f = self.symbol(x.func)
            if self.kind(f) is Kind.CLASS:
                return f
            if f is not None:
                return self.types.get(f)
            if isinstance(x.func, ast.Attribute):
                owner = self.type_of(x.func.value, typed, cls)
                if owner:
                    return self.types.get(Target(owner.path, f"{owner.qn}.{x.func.attr}"))
            return None
        if isinstance(x, ast.Attribute):
            t = self.symbol(x)
            if self.kind(t) is Kind.CLASS:
                return t
            owner = self.type_of(x.value, typed, cls)
            if owner:
                return self.types.get(Target(owner.path, f"{owner.qn}.{x.attr}"))
        return None

    def add(self, t: Target | None, line: int) -> None:
        if t is not None and t in self.want:
            self.hits[t].add(line)

    def visit(self, fn: ast.AST | None, nodes: list[ast.AST], cls: Target | None) -> None:
        typed: dict[str, Target] = {}
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            a = fn.args
            for arg in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]:
                if arg is not None:
                    c = self.annotation(arg.annotation)
                    if c:
                        typed[arg.arg] = c
        for _ in range(2):  # two passes, so x = y.attr after y = Cls() resolves
            for x in nodes:
                if isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name):
                    c = self.annotation(x.annotation)
                    if c:
                        typed[x.target.id] = c
                elif isinstance(x, (ast.Assign, ast.NamedExpr)):
                    tgts = x.targets if isinstance(x, ast.Assign) else [x.target]
                    c = self.type_of(x.value, typed, cls)
                    if c:
                        for tg in tgts:
                            if isinstance(tg, ast.Name):
                                typed[tg.id] = c
        for x in nodes:
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load):
                self.add(self.names.get(x.id), x.lineno)
            elif isinstance(x, ast.Attribute):
                t = self.symbol(x)
                if t is not None:
                    self.add(t, x.lineno)
                else:
                    owner = self.type_of(x.value, typed, cls)
                    if owner:
                        self.add(Target(owner.path, f"{owner.qn}.{x.attr}"), x.lineno)
            if isinstance(x, ast.Call):
                c = self.symbol(x.func)
                if self.kind(c) is Kind.CLASS:
                    for kw in x.keywords:
                        if kw.arg:
                            self.add(Target(c.path, f"{c.qn}.{kw.arg}"), kw.value.lineno)

    def run(self, tree: ast.Module) -> None:
        """One pass: split the file into scopes (module, each function, each class body)."""
        scopes: list[tuple[ast.AST | None, list[ast.AST], Target | None]] = [(None, [], None)]
        imports: list[ast.AST] = []
        stack: list[tuple[ast.AST, int, Target | None]] = [
            (c, 0, None) for c in reversed(list(ast.iter_child_nodes(tree)))]
        while stack:
            x, si, cls = stack.pop()
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)):
                a = x.args
                outer = [*x.decorator_list, *a.defaults, *(d for d in a.kw_defaults if d),
                         *(g.annotation for g in [*a.posonlyargs, *a.args, *a.kwonlyargs,
                                                  a.vararg, a.kwarg] if g and g.annotation)]
                if x.returns is not None:
                    outer.append(x.returns)
                stack.extend((d, si, cls) for d in outer)
                scopes.append((x, [], cls))
                stack.extend((c, len(scopes) - 1, cls) for c in reversed(x.body))
                continue
            if isinstance(x, ast.ClassDef):
                stack.extend((d, si, cls) for d in [*x.decorator_list, *x.bases,
                                                    *(k.value for k in x.keywords)])
                here = Target(self.path, f"{cls.qn}.{x.name}" if cls else x.name)
                scopes.append((None, [], here))
                stack.extend((c, len(scopes) - 1, here) for c in reversed(x.body))
                continue
            scopes[si][1].append(x)
            if isinstance(x, (ast.Import, ast.ImportFrom)):
                imports.append(x)
            stack.extend((c, si, cls) for c in ast.iter_child_nodes(x))
        self._bind(imports)
        for fn, nodes, cls in scopes:
            if nodes or fn is not None:
                self.visit(fn, nodes, cls)


def _py_refs(path: str, data: bytes, defs, index, types, want: set[Target]) -> dict[Target, set[int]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(data, filename=path)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return {}
    f = _PyFile(path, tree, defs, index, types, want)
    f.run(tree)
    return f.hits


# --------------------------------------------------------------- other langs

_DECL_BEFORE = re.compile(
    r"(?:\bfunction\s*\*?\s*|\bfn\s+|\bfunc\s+(?:\([^)]*\)\s*)?|\b(?:class|struct|interface|type|"
    r"enum|trait|mod|union)\s+|\b(?:const|let|var|static)\s+(?:mut\s+)?)$")


def _line_of(starts: list[int], pos: int) -> int:
    import bisect
    return bisect.bisect_right(starts, pos)


def _src_refs(path: str, data: bytes, targets: Iterable[Target]) -> dict[Target, set[int]]:
    lang = lang_of(path)
    text = data.decode("utf-8", errors="replace")
    code = scan(text, lang).code
    starts = [0] + [i + 1 for i, ch in enumerate(text) if ch == "\n"]
    flags = re.I if path.lower().endswith((".ps1", ".psm1")) else 0
    out: dict[Target, set[int]] = defaultdict(set)
    for t in targets:
        if "." in t.qn:
            attr = re.escape(t.qn.rsplit(".", 1)[1])
            sep = r"(?:\.|::)" if lang == "rust" else r"\."
            rx = re.compile(rf"{sep}\s*{attr}(?![\w$])", flags)
        else:
            dot = "" if lang == "go" and path != t.path else "."  # Go: pkg.Name from importers
            rx = re.compile(rf"(?<![\w${dot}-]){re.escape(t.qn)}(?![\w$-])", flags)
        for m in rx.finditer(code):
            ls = text.rfind("\n", 0, m.start()) + 1
            before = code[ls:m.start()]
            if path == t.path and "." not in t.qn:
                if _DECL_BEFORE.search(before):
                    continue
                if lang == "sh" and re.fullmatch(r"\s*", before) and \
                        re.match(r"\s*\(\s*\)", code[m.end():]):
                    continue  # name() { ... }: a shell function declaration
            out[t].add(_line_of(starts, m.start()))
    return out


# ---------------------------------------------------------------- public api

def references(targets: Iterable[Target], m: Map, sources: Mapping[str, bytes],
               in_files: Iterable[str] | None = None,
               graph: Mapping[str, tuple[str, ...]] | None = None) -> dict[Target, tuple[str, ...]]:
    """Target -> sorted unique "file:line" of qualified references in sources.

    m is the Map the sources belong to; targets that m no longer defines
    (a removed symbol) still resolve through `from M import name`.
    in_files limits which files are searched (default: all of sources).
    """
    from rco.imports import _dotted_index, file_graph
    want = set(targets)
    if not want:
        return {}
    defs = defs_of(m)
    types = _types(defs)
    for t in want:  # a removed symbol still resolves through its old imports
        defs.setdefault(t.path, {}).setdefault(t.qn, (Kind.FUNCTION, ""))
    files = sorted(set(in_files) if in_files is not None else set(sources))
    hits: dict[Target, set[tuple[str, int]]] = defaultdict(set)
    py_t = {t for t in want if t.path.endswith(".py")}
    src_t = {t for t in want if lang_of(t.path)}
    if py_t:
        index = _dotted_index([p for p in sources if p.endswith(".py")])
        needles = {t.qn.rsplit(".", 1)[-1].encode() for t in py_t}
        for p in files:  # a file that never spells a target's name cannot reference it
            if p.endswith(".py") and p in sources and any(n in sources[p] for n in needles):
                for t, lines in _py_refs(p, sources[p], defs, index, types, py_t).items():
                    hits[t].update((p, ln) for ln in lines)
    if src_t:
        lang_src = {p: d for p, d in sources.items() if not p.endswith(".py")}
        g = graph if graph is not None else file_graph(sorted(sources), lang_src)
        importers: dict[str, set[str]] = defaultdict(set)
        for src, tgts in g.items():
            for d in tgts:
                importers[d].add(src)
        for p in files:
            if not lang_of(p) or p not in sources:
                continue
            mine = [t for t in src_t if lang_of(t.path) == lang_of(p) and (
                    p == t.path or p in importers.get(t.path, ())
                    or (lang_of(p) == "go" and p.rpartition("/")[0] == t.path.rpartition("/")[0]))]
            if mine:
                for t, lines in _src_refs(p, sources[p], mine).items():
                    hits[t].update((p, ln) for ln in lines)
    return {t: tuple(f"{p}:{ln}" for p, ln in sorted(locs)) for t, locs in sorted(hits.items())}
