"""Import edges between in-repo modules (Phase 2 step 2; other languages in rco.edges).

Only `import` and `from ... import` statements count, relative ones too.
An import resolves to the longest dotted prefix that names an in-repo
Python file; anything else (stdlib, third party, unresolved) is ignored.
Files that do not parse add no edges. Phase 1's python extractor already
turns such files into FILE error nodes.
"""
from __future__ import annotations

import ast
import warnings
from typing import Iterable, Mapping, Sequence

from rco.extract.python import module_name
from rco.model import Module
from rco.modules import module_of


def _dotted_index(paths: Sequence[str]) -> dict[str, str]:
    """Dotted Python name -> repo path. A package's __init__.py wins over a same-named file."""
    out: dict[str, str] = {}
    for p in sorted(paths):
        if not p.endswith(".py"):
            continue
        name = module_name(p)
        if name == ".":
            continue
        if name not in out or p.endswith("/__init__.py"):
            out[name] = p
    return out


def _package_of(path: str) -> list[str]:
    name = module_name(path)
    parts = [] if name == "." else name.split(".")
    return parts if path.endswith("__init__.py") else parts[:-1]


def _resolve(dotted: str, index: Mapping[str, str]) -> str | None:
    parts = dotted.split(".")
    while parts:
        hit = index.get(".".join(parts))
        if hit is not None:
            return hit
        parts.pop()
    return None


def _local(path: str, dotted: str, index: Mapping[str, str]) -> str | None:
    """An absolute import read from the importing file's directory (sys.path[0])."""
    head = path.rpartition("/")[0]
    pkg = module_name(head + "/__init__.py") if head else "."
    if pkg == ".":
        return None  # the root is already where absolute imports resolve
    if f"{pkg}.{dotted.split('.')[0]}" not in index:
        return None  # only a file or package that sits beside the importer
    return _resolve(f"{pkg}.{dotted}", index)


def _import_refs(path: str, data: bytes) -> tuple[tuple, ...] | None:
    """The file's import statements, None when it does not parse.

    ("import", (names...)) or ("from", level, module, (names...)); they depend
    only on the file's bytes, so a replay run caches them (rco.memo).
    """
    def compute() -> tuple[tuple, ...] | None:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(data, filename=path)
        except (SyntaxError, ValueError):
            return None
        refs: list[tuple] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                refs.append(("import", tuple(a.name for a in node.names)))
            elif isinstance(node, ast.ImportFrom):
                refs.append(("from", node.level, node.module, tuple(a.name for a in node.names)))
        return tuple(refs)
    from rco import memo
    return memo.cached("import_refs", (path, data), compute)


def file_imports(path: str, data: bytes, index: Mapping[str, str],
                 local: bool = False) -> tuple[str, ...]:
    """Repo paths of the in-repo files that path imports, sorted, unique.

    local=True also tries an unresolved absolute import against the file's
    own directory, as a script or a pytest test module run from there sees it.
    """
    refs = _import_refs(path, data)
    if refs is None:
        return ()
    hits: set[str] = set()
    for ref in refs:
        if ref[0] == "import":
            for name in ref[1]:
                t = _resolve(name, index) or (_local(path, name, index) if local else None)
                if t:
                    hits.add(t)
        else:
            _, level, module, names = ref
            if level:
                pkg = _package_of(path)
                up = level - 1
                if up > len(pkg):
                    continue  # beyond the top: unresolved
                base = pkg[: len(pkg) - up]
                if module:
                    base = base + module.split(".")
            else:
                base = (module or "").split(".")
            for name in names:
                cand = ".".join(base + [name]) if name != "*" else ""
                t = (index.get(cand) if cand else None) or _resolve(".".join(base), index)
                if t is None and local and not level and module:
                    t = _local(path, cand or module, index) or _local(path, module, index)
                if t:
                    hits.add(t)
    hits.discard(path)
    return tuple(sorted(hits))


def file_graph(paths: Sequence[str], sources: Mapping[str, bytes],
               local: bool = False, only: Iterable[str] | None = None
               ) -> dict[str, tuple[str, ...]]:
    """Repo path -> sorted in-repo paths it imports, for every code file in sources.

    only limits the keys to those files (their imports still resolve repo-wide).
    """
    from rco.edges import file_edges, repo_facts
    from rco.extract.langs import lang_of
    index = _dotted_index([p for p in paths if p in sources])
    facts = repo_facts(frozenset(paths), sources)
    out: dict[str, tuple[str, ...]] = {}
    for p in sorted(sources if only is None else set(only) & set(sources)):
        if p.endswith(".py"):
            out[p] = file_imports(p, sources[p], index, local)
        elif lang_of(p):
            out[p] = file_edges(p, sources[p], facts)
    return out


def module_edges(modules: Sequence[Module], sources: Mapping[str, bytes]
                 ) -> dict[str, tuple[str, ...]]:
    """Module id -> sorted ids of in-repo modules it imports (itself excluded).

    sources maps repo-relative path to file bytes at one SHA. Every module
    in modules gets a key, with () when it imports nothing in the repo.
    """
    from rco.edges import file_edges, repo_facts
    owner = {p: m.id for m in modules for p in m.files}
    index = _dotted_index([p for p in owner if p in sources])
    facts = repo_facts(frozenset(owner), sources)
    out: dict[str, set[str]] = {m.id: set() for m in modules}
    for m in modules:
        for p in m.files:
            if p not in sources:
                continue
            found = (file_imports(p, sources[p], index) if p.endswith(".py")
                     else file_edges(p, sources[p], facts))
            for t in found:
                tid = owner.get(t, module_of(t))
                if tid != m.id and tid in out:
                    out[m.id].add(tid)
    return {k: tuple(sorted(v)) for k, v in sorted(out.items())}
