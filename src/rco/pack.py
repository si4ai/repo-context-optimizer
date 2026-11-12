"""`rco pack` (Phase 2 step 5, README slice 5, thin; forest-contract section 4).

The pack is the map: the repo Forest, full Trees for the modules that own
the --for files, and one import hop out as name and one line. Raw files are
fetched on demand. The budget is estimated tokens (characters / 4) of the
rendered markdown, footer included. Over budget, cuts go in this order:
hop lines from the end, then top-level Tree nodes from the end of each full
Tree, then Forest entries from the end. Every cut is named in `trimmed`.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from rco import build, canon, forest, gitread, imports, modules
from rco.forest_model import (DEFAULT_BUDGET, ForestEntry, HopLine, Pack, estimate_tokens,
                              is_stale, pack_to_obj)
from rco.model import Map, Node, Tree, node_key
from rco.output import Exit, UsageError
from rco.render import _lines as _node_lines
from rco.store import NodeKind, Store

MIN_BUDGET = 64  # header and footer alone need about this much
LINE_CAP = 100


def _one_line(e: ForestEntry) -> str:
    if e.prose is not None and e.prose.text.strip():
        text = e.prose.text.strip().splitlines()[0]
        line = text if len(text) <= LINE_CAP else text[: LINE_CAP - 3] + "..."
        return line + (" [stale]" if is_stale(e.prose, e.tree_hash) else "")
    bits = [f"layer {e.layer}"]
    for label, names in (("commands", e.owns.commands), ("tables", e.owns.tables),
                         ("routes", e.owns.routes)):
        if names:
            bits.append(f"{len(names)} {label}")
    return ", ".join(bits)


def _name_flags(notes: Sequence[str]) -> dict[str, str]:
    """Turn `name-in-tree: <id>: <names>` notes written by pack() into a dict."""
    out: dict[str, str] = {}
    prefix = "name-in-tree: "
    for n in notes:
        if n.startswith(prefix):
            mid, sep, names = n[len(prefix):].partition(": ")
            if sep:
                out[mid] = names
    return out


def forest_line(e: ForestEntry, name_flags: str = "") -> str:
    s = f"- {e.id}: {_one_line(e)}"
    if e.depends_on:
        s += f" (depends on: {', '.join(e.depends_on)})"
    if e.used_by:
        s += f" (used by: {', '.join(e.used_by)})"
    if name_flags:
        s += f" [name flags: {name_flags}]"
    return s


def _tree_lines(n: Node) -> list[str]:
    out: list[str] = []
    _node_lines(n, 0, out)
    return out


def _trimmed_summary(trimmed: Sequence[str]) -> str:
    hop = [t[4:] for t in trimmed if t.startswith("hop:")]
    fst = [t[7:] for t in trimmed if t.startswith("forest:")]
    nodes: dict[str, int] = {}
    for t in trimmed:
        if t.startswith("node:"):
            mod = t[5:].split("|", 1)[0]
            nodes[mod] = nodes.get(mod, 0) + 1
    parts = []
    if hop:
        parts.append("hop: " + ", ".join(hop))
    if nodes:
        parts.append("nodes: " + ", ".join(f"{m} {n}" for m, n in nodes.items()))
    if fst:
        parts.append("forest: " + ", ".join(fst))
    return f"# trimmed: {len(trimmed)} ({'; '.join(parts)})"


def pack_markdown(p: Pack, for_label: str = "") -> str:
    flagged = _name_flags(p.notes)
    out = [f"# rco pack  (SHA={p.sha[:7]}, budget={p.budget} estimated tokens)", "", "## Forest"]
    out += [forest_line(e, flagged.get(e.id, "")) for e in p.forest]
    if p.trees:
        out += ["", f"## Trees (full, for --for {for_label})" if for_label else "## Trees (full)"]
        for t in p.trees:
            out.append(f"### {t.module}")
            for n in t.nodes:
                out += _tree_lines(n)
    if p.hop:
        out += ["", "## One hop (name and one line)"]
        out += [f"- {h.id}: {h.line}" for h in p.hop]
    out += ["", f"# budget used: ~{p.used} / {p.budget} estimated tokens"]
    if p.trimmed:
        out.append(_trimmed_summary(p.trimmed))
    return "\n".join(out) + "\n"


def _measure(p: Pack, label: str) -> int:
    # `used` is part of the text; two passes settle its own digits
    used = estimate_tokens(pack_markdown(replace(p, used=0), label))
    return estimate_tokens(pack_markdown(replace(p, used=used), label))


def _map_from_store(store: Store, sha: str) -> Map | None:
    rows = store.list_by_sha(sha)
    if not rows:
        return None
    trees = []
    for r in rows:
        obj = store.get_node(NodeKind.TREE, r.module_hash)
        if obj is None:
            return None  # incomplete in this store: build from git instead
        trees.append(canon.tree_from_obj(obj))
    return Map(sha, tuple(trees))


def pack(repo: Path, sha: str, for_paths: Sequence[str] | None = None,
         budget: int = DEFAULT_BUDGET, store: Store | None = None) -> Pack:
    """Use the store when it has sha indexed; else build from git, writing nothing."""
    if budget < MIN_BUDGET:
        raise UsageError(f"--budget must be at least {MIN_BUDGET}")
    m = _map_from_store(store, sha) if store is not None else None
    notes: list[str] = []
    if m is None:
        m = build.build_map(repo, sha)
        notes.append("built from git (no published SHA in the store)")
    sources = forest.read_sources(repo, sha)
    mods = modules.group(e.path for e in gitread.ls_tree(repo, sha)
                         if e.mode != gitread.MODE_SUBMODULE)
    f = forest.compute_forest(m, imports.module_edges(mods, sources), sources)
    entries = list(f.entries)
    if store is not None:
        entries = [replace(e, prose=store.get_prose(e.id)) for e in entries]
    by_id = {e.id: e for e in entries}
    trees_by = {t.module: t for t in m.trees}
    if store is not None:
        from rco.prose import flags as prose_flags
        for e in entries:
            t = trees_by.get(e.id)
            bad = prose_flags(e.prose, t) if t is not None else ()
            if bad:
                notes.append(f"name-in-tree: {e.id}: {', '.join(bad)}")

    owners: list[str] = []
    if for_paths:
        owners = sorted({modules.module_of(p.strip("/")) for p in for_paths} & set(by_id))
        if not owners:
            notes.append("no module owns the --for paths")
    hop_ids = sorted({n for o in owners for n in by_id[o].depends_on + by_id[o].used_by}
                     - set(owners))
    trees = [trees_by[o] for o in owners]
    hop = [HopLine(h, _one_line(by_id[h])) for h in hop_ids]
    stale = tuple(sorted(e.id for e in entries if is_stale(e.prose, e.tree_hash)))
    label = " ".join(for_paths or ())
    trimmed: list[str] = []

    def current() -> Pack:
        return Pack(sha, budget, 0, tuple(entries), tuple(trees), tuple(hop), tuple(trimmed),
                    stale, tuple(sorted(notes)))

    while _measure(current(), label) > budget:
        if hop:
            trimmed.append(f"hop:{hop.pop().id}")
            continue
        cut = len(trees) - 1 if trees else None
        if cut is not None:
            t = trees[cut]
            if not t.nodes:
                del trees[cut]
                continue
            last = t.nodes[-1]
            trimmed.append(f"node:{t.module}|{node_key(last.path, last.name, last.kind)}")
            t = Tree(t.module, t.module_hash, t.extractors, t.nodes[:-1])
            if t.nodes:
                trees[cut] = t
            else:
                del trees[cut]  # every node is named in trimmed; drop the empty heading
            continue
        if entries:
            trimmed.append(f"forest:{entries.pop().id}")
            continue
        raise UsageError(f"--budget {budget} is too small for this pack's header and footer")
    p = current()
    shown = {e.id for e in p.forest}
    p = replace(p, stale=tuple(s for s in stale if s in shown))
    return replace(p, used=_measure(p, label))


@dataclass(frozen=True)
class PackReport:
    pack: Pack
    for_label: str = ""

    def to_obj(self) -> dict:
        return pack_to_obj(self.pack)

    def to_markdown(self) -> str:
        return pack_markdown(self.pack, self.for_label)

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if self.pack.stale else Exit.OK
