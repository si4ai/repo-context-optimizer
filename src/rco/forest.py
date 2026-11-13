"""Computed Forest (Phase 2 step 2; docs/design/forest-contract.md, section 2).

Everything here is computed from git: layer, depends_on and used_by from
the import graph, entry points, and the commands, tables and routes each
module's Tree owns. No prose: that is step 4.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from rco import build, canon, gitread, imports, modules
from rco.check import Finding
from rco.extract.python import module_name
from rco.forest_model import Forest, ForestEntry, Owns, forest_to_obj
from rco.model import Kind, Map, Tree, walk
from rco.output import Exit

_OWN_KINDS = {Kind.COMMAND: "commands", Kind.TABLE: "tables", Kind.VIEW: "tables",
              Kind.ROUTE: "routes"}


def _sccs(nodes: list[str], edges: Mapping[str, tuple[str, ...]]) -> list[list[str]]:
    """Tarjan, iterative; components in reverse topological order (sinks first)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on: set[str] = set()
    stack: list[str] = []
    out: list[list[str]] = []
    counter = 0
    for root in nodes:
        if root in index:
            continue
        work = [(root, iter(edges.get(root, ())))]
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on.add(root)
        while work:
            v, it = work[-1]
            nxt = next(it, None)
            if nxt is not None:
                if nxt not in index:
                    index[nxt] = low[nxt] = counter
                    counter += 1
                    stack.append(nxt)
                    on.add(nxt)
                    work.append((nxt, iter(edges.get(nxt, ()))))
                elif nxt in on:
                    low[v] = min(low[v], index[nxt])
                continue
            work.pop()
            if work:
                low[work[-1][0]] = min(low[work[-1][0]], low[v])
            if low[v] == index[v]:
                comp = []
                while True:
                    w = stack.pop()
                    on.discard(w)
                    comp.append(w)
                    if w == v:
                        break
                out.append(sorted(comp))
    return out


def layers(ids: list[str], edges: Mapping[str, tuple[str, ...]]) -> tuple[dict[str, int], list[str]]:
    """Layer per module (0 = imports nothing in the repo) and one note per cycle."""
    comps = _sccs(sorted(ids), edges)
    comp_of = {m: i for i, c in enumerate(comps) for m in c}
    layer_of_comp: dict[int, int] = {}
    for i, comp in enumerate(comps):  # sinks first, so every dependency is done
        deps = {comp_of[d] for m in comp for d in edges.get(m, ()) if comp_of[d] != i}
        layer_of_comp[i] = 1 + max((layer_of_comp[d] for d in deps), default=-1)
    notes = [f"cycle: {' '.join(c)}" for c in comps if len(c) > 1]
    return {m: layer_of_comp[comp_of[m]] for m in ids}, sorted(notes)


def _scripts(sources: Mapping[str, bytes], index: Mapping[str, str]) -> dict[str, set[str]]:
    """Module id -> [project.scripts] names whose target lives there."""
    raw = sources.get("pyproject.toml")
    out: dict[str, set[str]] = {}
    if raw is None:
        return out
    try:
        scripts = tomllib.loads(raw.decode("utf-8")).get("project", {}).get("scripts", {})
    except (tomllib.TOMLDecodeError, UnicodeDecodeError, AttributeError):
        return out
    if not isinstance(scripts, dict):
        return out
    for name, target in scripts.items():
        mod = str(target).split(":", 1)[0].strip()
        path = index.get(mod)
        if path is not None:
            out.setdefault(modules.module_of(path), set()).add(str(name))
    return out


def _owns(t: Tree) -> tuple[Owns, set[str]]:
    got: dict[str, set[str]] = {"commands": set(), "tables": set(), "routes": set()}
    top_cmds: set[str] = set()
    for _key, n in walk(t):
        slot = _OWN_KINDS.get(n.kind)
        if slot:
            got[slot].add(n.name)
            if n.kind is Kind.COMMAND and " " not in n.name:
                top_cmds.add(n.name)
    return Owns(*(tuple(sorted(got[s])) for s in ("commands", "tables", "routes"))), top_cmds


def compute_forest(m: Map, edges: Mapping[str, tuple[str, ...]],
                   sources: Mapping[str, bytes]) -> Forest:
    """Forest of m: layer, depends_on/used_by mirrors, entry points, owns. No prose.

    sources is used only for entry points ([project.scripts] in pyproject.toml).
    """
    ids = sorted(t.module for t in m.trees)
    known = set(ids)
    deps = {i: tuple(sorted(d for d in edges.get(i, ()) if d in known and d != i)) for i in ids}
    used: dict[str, set[str]] = {i: set() for i in ids}
    for i, ds in deps.items():
        for d in ds:
            used[d].add(i)
    layer, notes = layers(ids, deps)
    py = [p for t in m.trees for p in _tree_paths(t)]
    index = {module_name(p): p for p in sorted(py) if p.endswith(".py")}
    scripts = _scripts(sources, index)
    entries = []
    for t in sorted(m.trees, key=lambda t: t.module):
        owns, top_cmds = _owns(t)
        eps = set(scripts.get(t.module, ())) | top_cmds
        if any(p.endswith("/__main__.py") or p == "__main__.py" for p in _tree_paths(t)):
            eps.add("__main__")
        entries.append(ForestEntry(
            id=t.module, layer=layer[t.module], depends_on=deps[t.module],
            used_by=tuple(sorted(used[t.module])), entry_points=tuple(sorted(eps)),
            owns=owns, tree_hash=canon.tree_hash(t)))
    return Forest(m.sha, tuple(entries), tuple(notes))


def owns_of(t: Tree) -> Owns:
    """The commands, tables and routes a module's Tree owns."""
    return _owns(t)[0]


def _tree_paths(t: Tree) -> list[str]:
    return sorted({n.path for n in t.nodes if n.path})


def read_sources(repo: Path, sha: str) -> dict[str, bytes]:
    """Python files and pyproject.toml at sha; no symlinks, no submodules."""
    ents = [e for e in gitread.ls_tree(repo, sha)
            if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)
            and (e.path.endswith(".py") or e.path == "pyproject.toml")]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return {e.path: data[e.blob] for e in ents}


def build_forest(repo: Path, sha: str) -> Forest:
    """Forest of a commit, computed on the fly from git (no store)."""
    m = build.build_map(repo, sha)
    sources = read_sources(repo, sha)
    mods = modules.group(e.path for e in gitread.ls_tree(repo, sha)
                         if e.mode != gitread.MODE_SUBMODULE)
    return compute_forest(m, imports.module_edges(mods, sources), sources)


def compare_edges(claimed: Forest, actual: Forest) -> tuple[Finding, ...]:
    """`edge` findings: an import edge the claimed Forest lacks or invents."""
    c = {e.id: set(e.depends_on) for e in claimed.entries}
    a = {e.id: set(e.depends_on) for e in actual.entries}
    out = []
    for mod in sorted(set(c) | set(a)):
        for d in sorted(a.get(mod, set()) - c.get(mod, set())):
            out.append(Finding("edge", f"edge:{mod}->{d}", mod, "in code, not in forest"))
        for d in sorted(c.get(mod, set()) - a.get(mod, set())):
            out.append(Finding("edge", f"edge:{mod}->{d}", mod, "claimed, not in code"))
    return tuple(out)


@dataclass(frozen=True)
class ForestReport:
    forest: Forest

    def to_obj(self) -> dict:
        return forest_to_obj(self.forest)

    def to_markdown(self) -> str:
        return forest_markdown(self.forest)

    def exit_code(self) -> Exit:
        return Exit.OK


def entry_line(e: ForestEntry) -> str:
    parts = [f"- {e.id} (layer {e.layer})"]
    if e.depends_on:
        parts.append(f"(depends on: {', '.join(e.depends_on)})")
    if e.used_by:
        parts.append(f"(used by: {', '.join(e.used_by)})")
    if e.entry_points:
        parts.append(f"(entry: {', '.join(e.entry_points)})")
    return " ".join(parts)


def forest_markdown(f: Forest) -> str:
    lines = [f"# rco forest  (SHA={f.sha[:12]})", ""]
    lines += [entry_line(e) for e in f.entries]
    if f.notes:
        lines += ["", "## Notes"] + [f"- {n}" for n in f.notes]
    return "\n".join(lines) + "\n"
