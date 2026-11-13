"""`rco update`: publish a SHA to the store (Phase 2 step 3, README slice 3).

Order (README sequence diagram, verify/spin/rco_update.pml variant fixed):

1. Hash every module of NEW from git blob ids (no parsing). Modules the
   diff from the published pointer touched go first, then the rest.
2. Extract and store the Tree of every module whose ModuleHash is not in the
   store yet (rule 2: complete SHAs only, not only the diff).
3. Write the sha index of NEW.
4. Move the pointer with one compare-and-swap, and only when NEW descends
   from the pointer it replaces (rule 1). A pointer that moved meanwhile is
   re-read; one that moved past NEW refuses the swap, and nothing more is
   written (late finisher).
5. Only after a successful swap, queue a prose rewrite for each module whose
   TreeHash differs from the replaced SHA's (rule 4).

Staleness is never written (rule 3). No holds, no locks. Running the same
update twice equals running it once (content-addressed writes).
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rco import build, canon, extract, gitread, modules
from rco.forest_model import DEFAULT_POINTER, SCHEMA_UPDATE, UpdateResult
from rco.model import Module, SourceFile
from rco.output import Exit
from rco.store import IndexRow, NodeKind, Store

# Test-only crash hook: called as hook(point, detail) at each crash point.
# Points: "extract" (before a module's Tree is written), "index" (before the
# sha index), "swap" (before the pointer CAS), "queue" (before queueing).
Hook = Callable[[str, str], None]


def is_ancestor(repo: Path, old: str, new: str) -> bool:
    """git merge-base --is-ancestor OLD NEW (a SHA is its own ancestor)."""
    p = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", old, new],
                       capture_output=True)
    if p.returncode not in (0, 1):
        return False  # unknown OLD (e.g. history rewritten): never treat NEW as descending
    return p.returncode == 0


def _changed_paths(repo: Path, old: str, new: str) -> set[str]:
    out = gitread._git(repo, "diff", "--name-only", "-z", "--no-renames", old, new)
    return {p.decode("utf-8", "surrogateescape") for p in out.split(b"\0") if p}


@dataclass(frozen=True)
class _Plan:
    mods: tuple[Module, ...]
    hashes: dict[str, str]  # module id -> ModuleHash
    entries: dict[str, gitread.Entry]
    layout: modules.Layout


def _plan(repo: Path, sha: str) -> _Plan:
    entries = {e.path: e for e in gitread.ls_tree(repo, sha) if e.mode != gitread.MODE_SUBMODULE}
    layout = modules.layout_at(repo, sha, tuple(entries.values()))
    mods = layout.group(entries)
    vers = extract.versions()
    hashes = {m.id: canon.module_hash(m, {p: entries[p].blob for p in m.files}, vers)
              for m in mods}
    return _Plan(mods, hashes, entries, layout)


def _extract(repo: Path, plan: _Plan, mod: Module):
    ents = [plan.entries[p] for p in mod.files]
    links = frozenset(e.path for e in ents if e.mode == gitread.MODE_SYMLINK)
    data = gitread.read_blobs(repo, [e.blob for e in ents if e.path not in links])
    srcs = [SourceFile(e.path, e.blob, b"" if e.path in links else data[e.blob]) for e in ents]
    return build.build_tree(mod, srcs, links)


def update(repo: Path, store: Store, new: str, name: str = DEFAULT_POINTER,
           hook: Hook | None = None) -> UpdateResult:
    hit = hook or (lambda point, detail: None)
    new = gitread.resolve(repo, new)
    start = store.get_pointer(name)
    notes: list[str] = []
    if start == new:
        return UpdateResult(name, start, new, False, (), (), ("already published",))
    if start is not None and not is_ancestor(repo, start, new):
        return UpdateResult(name, start, new, False, (), (),
                            (f"refused: {new[:12]} does not descend from {name}={start[:12]}",))

    plan = _plan(repo, new)
    touched: set[str] = set()
    if start is not None:
        touched = {plan.layout.module_of(p) for p in _changed_paths(repo, start, new)}
    order = sorted(plan.mods, key=lambda m: (m.id not in touched, m.id))

    extracted: list[str] = []
    trees: dict[str, str] = {}  # module id -> TreeHash
    for mod in order:
        mh = plan.hashes[mod.id]
        have = store.get_node(NodeKind.TREE, mh)
        if have is None:
            hit("extract", mod.id)
            t = _extract(repo, plan, mod)
            assert t.module_hash == mh, f"module hash drift for {mod.id}"
            store.put_node(NodeKind.TREE, mh, canon.tree_to_obj(t))
            extracted.append(mod.id)
            trees[mod.id] = canon.tree_hash(t)
        else:
            trees[mod.id] = canon.tree_hash(canon.tree_from_obj(have))

    # rule 2: every module of NEW is in the store before anything points at it
    missing = [m.id for m in plan.mods if store.get_node(NodeKind.TREE, plan.hashes[m.id]) is None]
    if missing:
        raise RuntimeError(f"store lost Trees for {', '.join(missing)}")

    hit("index", new)
    store.index_sha(new, tuple(IndexRow(m.id, plan.hashes[m.id], trees[m.id]) for m in plan.mods))

    # rule 1: one CAS from the pointer we replace, only forward
    hit("swap", new)
    cur = store.get_pointer(name)
    while True:
        if cur == new:
            notes.append("already published by another update")
            return UpdateResult(name, start, new, False, tuple(extracted), (), tuple(notes))
        if cur is not None and not is_ancestor(repo, cur, new):
            notes.append(f"refused: {name} moved past {new[:12]} to {cur[:12]}")
            return UpdateResult(name, start, new, False, tuple(extracted), (), tuple(notes))
        if store.set_pointer(name, cur, new):
            break
        cur = store.get_pointer(name)  # moved meanwhile: re-read and re-check

    # rule 4: prose rewrites only after the pointer write
    before = {r.module: r.tree_hash for r in store.list_by_sha(cur)} if cur else {}
    queued = [m for m in sorted(trees) if before.get(m) != trees[m]]
    hit("queue", new)
    for m in queued:
        store.enqueue(m, trees[m])
    return UpdateResult(name, cur, new, True, tuple(extracted), tuple(queued), tuple(notes))


def result_to_obj(r: UpdateResult) -> dict:
    return {"schema": SCHEMA_UPDATE, "name": r.name, "old": r.old, "new": r.new,
            "swapped": r.swapped, "extracted": list(r.extracted), "queued": list(r.queued),
            "notes": list(r.notes)}


@dataclass(frozen=True)
class UpdateReport:
    result: UpdateResult

    def to_obj(self) -> dict:
        return result_to_obj(self.result)

    def to_markdown(self) -> str:
        r = self.result
        state = "published" if r.swapped else "not moved"
        lines = [f"# rco update  ({r.name}: {(r.old or '-')[:12]} -> {r.new[:12]}, {state})", "",
                 f"- extracted: {len(r.extracted)} module(s)",
                 f"- queued for prose: {len(r.queued)} module(s)"]
        lines += [f"- note: {n}" for n in r.notes]
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK  # a refused swap is not an error
