"""Simple merge planning (`rco plan BRANCH BRANCH...`, code MP).

Deterministic, read-only, no LLM: nothing is merged, rebased or pushed.
Each pair is compared against that pair's own merge-base, unless `--base`
overrides every pair. Per branch the overview is still read against the
common merge-base of all the branches (or `--base`). Per branch the plan
reads, from git:

- the changed files (BASE -> branch);
- the contract changes (rco diff BASE branch: signatures, fields, flags,
  columns, routes; doc-only changes are left out);
- the modules whose Tree changed (TreeHash differs), and the Forest of the
  branch head (which module depends on which).

For each ordered pair (A, B) it reports what A changes that B uses:

- files both changed (the places a textual merge can conflict);
- uses: lines in B's changed files that reference a symbol whose contract
  A changed or removed, resolved by rco.refs (qualified, every bundled
  language); a symbol A only added is not a use;
- module dependencies: a module B changed that depends, in B's Forest, on
  a module where A changed or removed a contract, when no use above
  explains it;
- tests: test files that reach both A's and B's changed files through
  in-repo imports at BASE, A or B (rco affected's graph); run them on the
  merged result.

Merge order: B after A when B uses or depends on A's changes or is stacked
on A (has all of A's commits), layered like
the Forest (layer 0 first). A cycle is listed and its branches share a layer.

What it cannot prove is listed in every report (`LIMITS`): a body change
with an unchanged contract, calls the reference index does not resolve,
and a clean report never means the merge result behaves.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from rco import affected, build, canon, diff, gitread, imports, refs
from rco.forest import compute_forest, layers, map_modules
from rco.output import Exit, UsageError, counted

SCHEMA_PLAN = "rco.plan/1"
LIMITS = (
    "a function body changed under an unchanged contract is not a finding; "
    "it shows only through the shared tests",
    "references the index does not resolve are not seen: calls across languages, "
    "dynamic dispatch, run-time imports, positional constructor arguments",
    "files changed on both sides may still merge cleanly; files changed on one side "
    "only may still conflict in behavior",
    "a clean pair is no proof the merged result works: run the listed tests on it",
)


@dataclass(frozen=True, slots=True)
class Side:
    branch: str
    sha: str
    changed: tuple[str, ...]
    changes: tuple[diff.Change, ...]   # contract changes, no doc-only
    modules: tuple[str, ...]           # modules whose Tree changed
    touched: tuple[str, ...]           # modules owning a changed file
    breaking: tuple[str, ...]          # modules owning a changed or removed contract
    unseen: tuple[str, ...]            # changed files the import graph cannot see readers of


@dataclass(frozen=True, slots=True)
class Pair:
    first: str    # A: the branch whose changes are used
    then: str     # B: the branch that uses them
    base: str     # merge-base of this pair, or --base when given
    overlap: tuple[str, ...]
    uses: tuple[tuple[str, str], ...]          # (qualname changed by A, "file:line" in B)
    module_deps: tuple[tuple[str, str], ...]   # (B module, A module it depends on)
    tests: tuple[str, ...]
    contains: bool = False                     # B's history already has all of A (stacked)

    @property
    def depends(self) -> bool:
        return bool(self.uses or self.module_deps or self.contains)

    @property
    def clean(self) -> bool:
        return not (self.overlap or self.depends)


@dataclass(frozen=True, slots=True)
class Plan:
    base: str
    sides: tuple[Side, ...]
    pairs: tuple[Pair, ...]
    order: tuple[tuple[str, ...], ...]   # merge layers, first layer first
    notes: tuple[str, ...]

    def to_obj(self) -> dict:
        return {
            "schema": SCHEMA_PLAN, "base": self.base,
            "branches": [{"branch": s.branch, "sha": s.sha, "changed": list(s.changed),
                          "modules": list(s.modules), "touched": list(s.touched),
                          "breaking": list(s.breaking), "unseen": list(s.unseen),
                          "changes": [{"op": c.op, "key": c.key, "kind": c.kind, "path": c.path,
                                       "before": c.before, "after": c.after} for c in s.changes]}
                         for s in self.sides],
            "pairs": [{"first": p.first, "then": p.then, "base": p.base,
                       "clean": p.clean, "contains": p.contains,
                       "overlap": list(p.overlap),
                       "uses": [{"symbol": q, "at": at} for q, at in p.uses],
                       "module_deps": [{"module": b, "depends_on": a} for b, a in p.module_deps],
                       "tests": list(p.tests)}
                      for p in self.pairs],
            "order": [list(layer) for layer in self.order],
            "notes": list(self.notes), "limits": list(LIMITS),
        }

    def to_markdown(self) -> str:
        out = [f"# rco plan  (base={self.base[:12]}, plan only: nothing is merged or pushed)", ""]
        out += ["## Merge order", ""]
        out += [f"{i + 1}. {', '.join(f'`{b}`' for b in layer)}" for i, layer in enumerate(self.order)]
        out += [f"- {n}" for n in self.notes]
        for s in self.sides:
            out += ["", f"## {s.branch} ({s.sha[:12]})",
                    f"{counted(len(s.changed), 'changed file')}, "
                    f"{counted(len(s.changes), 'contract change')}, "
                    f"Tree changed in: {', '.join(f'`{m}`' for m in s.modules) or 'none'}"]
            if s.unseen:
                out += ["", "not code, so who reads them is unknown (run all tests on a merge "
                        "with this branch if one matters):"] + [f"- `{u}`" for u in s.unseen]
        shown: set[frozenset[str]] = set()  # tests are listed once per unordered pair
        for p in self.pairs:
            two = frozenset((p.first, p.then))
            if p.clean and (not p.tests or two in shown):
                continue
            out += ["", f"## `{p.then}` against `{p.first}`"
                    + (" (clean)" if p.clean else "")
                    + f"  (base={p.base[:12]})"]
            if p.contains:
                out += ["", f"`{p.then}` is stacked on `{p.first}` (has all its commits)."]
            if p.overlap:
                out += ["", "files changed on both:"] + [f"- `{f}`" for f in p.overlap]
            if p.uses:
                out += ["", f"`{p.then}` uses what `{p.first}` changed:"]
                out += [f"- `{q}` at `{at}`" for q, at in p.uses]
            if p.module_deps:
                out += ["", f"modules `{p.then}` changed that depend on a Tree `{p.first}` changed:"]
                out += [f"- `{b}` -> `{a}`" for b, a in p.module_deps]
            if p.tests and two not in shown:
                shown.add(two)
                out += ["", "tests that see both sides (run on the merged result):"]
                out += [f"- `{t}`" for t in p.tests]
        out += ["", "## Cannot prove", ""] + [f"- {x}" for x in LIMITS]
        return "\n".join(out) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK if all(p.clean for p in self.pairs) else Exit.FINDINGS


@dataclass(frozen=True, slots=True)
class _Head:
    side: Side
    entries: tuple[gitread.Entry, ...]
    sources: Mapping[str, bytes]
    owner: Mapping[str, str]                    # path -> module at the branch head
    deps: Mapping[str, tuple[str, ...]]         # module -> modules it depends on
    graph: Mapping[str, tuple[str, ...]]        # file import graph at the branch head


def _graph(repo: Path, entries: tuple[gitread.Entry, ...]) -> Mapping[str, tuple[str, ...]]:
    paths, sources = affected._code_sources(repo, entries)
    return imports.file_graph(paths, sources, local=True)


def _hashes(m) -> dict[str, str]:
    return {t.module: canon.tree_hash(t) for t in m.trees}


def _side(repo: Path, branch: str, base: str, base_entries) -> _Head:
    sha = gitread.resolve(repo, branch)
    entries = gitread.ls_tree(repo, sha)
    changed = tuple(diff.changed_paths(base_entries, entries))
    changes = tuple(c for c in diff.diff(repo, base, sha, progress="rco plan").changes
                    if c.op != "doc")
    bmap = build.build_map(repo, base, list(changed)) if changed else None
    before = _hashes(bmap) if bmap else {}
    full = build.build_map(repo, sha)
    after = _hashes(full)
    owner = {n.path: t.module for t in full.trees for n in t.nodes if n.path}
    touched = {owner[p] for p in changed if p in owner}
    modules = tuple(sorted(mod for mod in (set(before) | touched)
                           if before.get(mod) != after.get(mod)))
    sources = diff.code_sources(repo, entries)
    from rco.forest import read_sources
    fsrc = read_sources(repo, sha)
    forest = compute_forest(full, imports.module_edges(map_modules(full), fsrc), fsrc)
    deps = {e.id: e.depends_on for e in forest.entries}
    base_owner = {n.path: t.module for t in bmap.trees for n in t.nodes if n.path} if bmap else {}
    breaking = tuple(sorted({owner.get(c.path) or base_owner.get(c.path) for c in changes
                             if c.op != "added"} - {None}))
    _, _, _, reasons = affected.select(list(changed), [e.path for e in entries], ())
    unseen = tuple(r.split(": ", 1)[0] for r in reasons)
    side = Side(branch, sha, changed, changes, modules, tuple(sorted(touched)), breaking, unseen)
    return _Head(side, entries, sources, owner, deps, _graph(repo, entries))


def _pair(repo: Path, a: _Head, b: _Head, base_graph, head_paths_ab, base: str) -> Pair:
    A, B = a.side, b.side
    overlap = tuple(sorted(set(A.changed) & set(B.changed)))
    # a symbol A only added cannot break a use in B: B can name it only by
    # defining it too, which shows as files changed on both sides
    want = {refs.Target(c.path, diff._qn(c)): diff._qn(c)
            for c in A.changes if c.kind in _PY and c.op != "added"}
    uses: set[tuple[str, str]] = set()
    if want and B.changed:
        defs = build.build_map(repo, B.sha, sorted({t.path for t in want}))
        for t, locs in refs.references(set(want), defs, b.sources, in_files=B.changed).items():
            uses |= {(want[t], loc) for loc in locs}
    explained = {b.owner.get(at.rsplit(":", 1)[0]) for _, at in uses}
    mods = set(A.breaking)
    module_deps = tuple(sorted((m, d) for m in B.touched if m not in explained
                               for d in b.deps.get(m, ()) if d in mods))
    graphs = (base_graph, a.graph, b.graph)
    _, ta, _, _ = affected.select(list(A.changed), head_paths_ab, graphs)
    _, tb, _, _ = affected.select(list(B.changed), head_paths_ab, graphs)
    tests = tuple(sorted(set(ta) & set(tb)))
    contains = A.sha != B.sha and gitread.merge_base(repo, A.sha, B.sha) == A.sha
    return Pair(A.branch, B.branch, base, overlap, tuple(sorted(uses, key=_loc_key)),
                module_deps, tests, contains)


def _loc_key(u: tuple[str, str]) -> tuple[str, str, int]:
    f, _, line = u[1].rpartition(":")
    return (u[0], f, int(line))


_PY = frozenset(k.value for k in diff._PY_KINDS)


def plan(repo: Path, branches: Sequence[str], base: str | None = None) -> Plan:
    if len(branches) < 2 or len(set(branches)) != len(branches):
        raise UsageError("plan needs two or more distinct BRANCHes")
    t0 = time.perf_counter()
    shas = [gitread.resolve(repo, b) for b in branches]
    if base is None:
        common = shas[0]
        for s in shas[1:]:
            common = gitread.merge_base(repo, common, s)
        forced = None
    else:
        common = gitread.resolve(repo, base)
        forced = common
    # overview sides stay against the common base (or --base)
    cache: dict[tuple[str, str], _Head] = {}
    trees: dict[str, tuple[gitread.Entry, ...]] = {}
    graphs: dict[str, Mapping[str, tuple[str, ...]]] = {}

    def entries_at(sha: str) -> tuple[gitread.Entry, ...]:
        if sha not in trees:
            trees[sha] = gitread.ls_tree(repo, sha)
        return trees[sha]

    def graph_at(sha: str, ents: tuple[gitread.Entry, ...]):
        if sha not in graphs:
            graphs[sha] = _graph(repo, ents)
        return graphs[sha]

    def head(branch: str, base_sha: str) -> _Head:
        key = (branch, base_sha)
        if key not in cache:
            cache[key] = _side(repo, branch, base_sha, entries_at(base_sha))
        return cache[key]

    heads = [head(b, common) for b in branches]
    base_graph = graph_at(common, entries_at(common))
    pairs = []
    for i, a in enumerate(heads):
        for j, b in enumerate(heads):
            if i == j:
                continue
            pb = forced if forced is not None else gitread.merge_base(repo, shas[i], shas[j])
            aa, bb = head(a.side.branch, pb), head(b.side.branch, pb)
            paths = sorted({e.path for e in aa.entries} | {e.path for e in bb.entries})
            pairs.append(_pair(repo, aa, bb, graph_at(pb, entries_at(pb)), paths, pb))
    names = [h.side.branch for h in heads]
    edges = {n: tuple(sorted({p.first for p in pairs if p.then == n and p.depends})) for n in names}
    layer, cycles = layers(names, edges)
    order = tuple(tuple(sorted(n for n in names if layer[n] == i))
                  for i in range(max(layer.values()) + 1))
    notes = tuple(c.replace("cycle:", "cycle (each uses the other; merge together or split):")
                  for c in cycles)
    print(f"rco plan: {len(names)} branches, "
          f"{sum(not p.clean for p in pairs)} pairs with findings "
          f"in {time.perf_counter() - t0:.3f}s", file=sys.stderr)
    return Plan(common, tuple(h.side for h in heads), tuple(pairs), order, notes)
