"""`rco pack` (Phase 2 step 5, README slice 5, thin; forest-contract section 4).

The pack is the map: the repo Forest and, with --for, the signatures of the
files the --for files import (one hop in the file import graph, across
module boundaries; card 1340), plus one import hop out of the owning
modules as name and one line. The --for files' own outlines are left out:
the agent opens those files anyway to edit them. Imported files are ranked
by distance from the --for files: files more --for files import come
first, then files whose directory is nearer, then by path. Raw files are
fetched on demand. The budget is tokens of the rendered markdown, footer included:
o200k_base when tiktoken is installed, otherwise characters / 3.5 (card 1343). Over budget, cuts go in this order:
hop lines from the end, then empty Forest lines (layer 0, no edges, nothing
owned, no prose) from the deepest level up, then Tree nodes of the farthest
imported file first, then the remaining Forest entries. With --for, the
budget is min(--budget, half the estimated tokens of the imported files);
when that is below MIN_BUDGET, or the --for files import nothing in the
repo, they get a one-line no-op pack instead (card 1341). With --for, the Forest holds only the
owning modules and their one-hop modules; those neighbor lines are cut
right after the hop lines, before any Tree node, and the owners' lines go
last (card 1342). After the pack
fits, hop lines that now fit are put back, last cut first, so leftover
budget still shows a One hop section. Hop lists put test modules after
non-test modules. A parent Forest line is kept until all of its children
are cut. Every cut is named in `trimmed`. After every content cut, a pack that is still over budget shortens the trimmed footer to counts and, if only the header and footer remain, records that residue in a note; the command exits 0 (exit 1 still means stale prose).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from rco import build, canon, forest, gitread, imports, modules
from rco.forest_model import (DEFAULT_BUDGET, BuiltFrom, ForestEntry, HopLine, Pack, ProseSource,
                              estimate_tokens, is_stale, pack_to_obj)
from rco.model import Kind, Map, Node, Tree, node_key
from rco.output import Exit, UsageError
from rco.render import node_line
from rco.extract.langs import lang_of
from rco.prose import derived_prose
from rco.prose.derive import call_index, with_derived_docs
from rco.store import NodeKind, Store

MIN_BUDGET = 128  # header, built line, a short warning and the footer need about this much
LINE_CAP = 100
WARN_NAMES = 10  # names shown on the "changed since this pack" line
NOOP_NOTE = ("nothing to add for these files: they import no other file in the repo; "
             "read them directly")
SMALL_NOTE = ("nothing to add for these files: the files they import are too small to "
              "outline; read them directly")
NOOP_NOTES = (NOOP_NOTE, SMALL_NOTE)


def _is_empty_forest_entry(e: ForestEntry) -> bool:
    """layer 0, no edges, nothing owned, and no mined or written prose to show.

    Derived prose only restates the Tree, so it does not keep a line from being cut first.
    """
    if e.prose is not None and e.prose.text.strip() and e.prose.source is not ProseSource.DERIVED:
        return False
    return (e.layer == 0 and not e.depends_on and not e.used_by
            and not e.owns.commands and not e.owns.tables and not e.owns.routes)


def _parent_id(mid: str, ids: set[str]) -> str | None:
    """closest ancestor module id, or None at the root."""
    if mid == ".":
        return None
    head = mid
    while "/" in head:
        head = head.rsplit("/", 1)[0]
        if head in ids:
            return head
    if "." in ids:
        return "."
    return None


def _dir_distance(a: str, b: str) -> int:
    """How many steps apart the directories of two repo paths are in the directory tree."""
    da, db = a.split("/")[:-1], b.split("/")[:-1]
    common = 0
    for x, y in zip(da, db):
        if x != y:
            break
        common += 1
    return (len(da) - common) + (len(db) - common)


def rank_imports(for_paths: Sequence[str], graph: dict[str, tuple[str, ...]]) -> list[str]:
    """Files the --for files import (one hop), nearest first; the --for files are left out.

    A file is nearer when more --for files import it, then when its directory is
    closer to the nearest importing --for file, then by path, so the list is deterministic.
    """
    fps = set(for_paths)
    by: dict[str, list[str]] = {}
    for f in sorted(fps):
        for t in graph.get(f, ()):
            if t not in fps:
                by.setdefault(t, []).append(f)
    return sorted(by, key=lambda t: (-len(by[t]), min(_dir_distance(f, t) for f in by[t]), t))


def _forest_depth(mid: str) -> int:
    return 0 if mid == "." else mid.count("/") + 1


def _is_test_module(mid: str) -> bool:
    """A module whose id has a `test` or `tests` path component."""
    return any(part in {"test", "tests"} for part in mid.split("/"))


def _hop_key(mid: str) -> tuple[bool, str]:
    """Non-test modules first, then by id, so the list is deterministic."""
    return (_is_test_module(mid), mid)



def _next_forest_cut(entries: list[ForestEntry], all_ids: set[str],
                     empty_only: bool) -> int | None:
    """index of the next Forest line to cut: deepest eligible, then by id.

    A line is eligible only when none of its descendants are still shown.
    """
    shown = {e.id for e in entries}
    blocked: set[str] = set()
    for mid in shown:
        p = _parent_id(mid, all_ids)
        while p is not None:
            if p in shown:
                blocked.add(p)
            p = _parent_id(p, all_ids)
    candidates = [i for i, e in enumerate(entries)
                  if e.id not in blocked and (not empty_only or _is_empty_forest_entry(e))]
    if not candidates:
        return None
    return max(candidates, key=lambda i: (_forest_depth(entries[i].id), entries[i].id))


def _one_line(e: ForestEntry) -> str:
    if e.prose is not None and e.prose.text.strip():
        text = e.prose.text.strip().splitlines()[0]
        if e.prose.source is ProseSource.DERIVED:
            text = f"layer {e.layer}; {text}"
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


_DOC_KINDS = (Kind.CLASS, Kind.FUNCTION, Kind.METHOD)


def _tree_lines(n: Node) -> list[str]:
    """Tree lines as `rco tree` renders them, plus `  # doc` on classes and functions."""
    out: list[str] = []
    _doc_lines(n, 0, out, "")
    return out


def _doc_lines(n: Node, depth: int, out: list[str], lang: str) -> None:
    line = "  " * depth + node_line(n, lang)
    if n.kind in _DOC_KINDS and n.doc:
        line += f"  # {n.doc}"
    out.append(line)
    if n.kind is Kind.SRCMODULE:
        lang = dict(n.attrs).get("lang", "")
    for c in n.children:
        _doc_lines(c, depth + 1, out, lang)


TRIM_COUNTS = "trimmed line shows counts only"  # card 1343: shorter footer when names would overflow


def _trimmed_summary(trimmed: Sequence[str], counts_only: bool = False) -> str:
    hop = [t[4:] for t in trimmed if t.startswith("hop:")]
    fst = [t[7:] for t in trimmed if t.startswith("forest:")]
    nodes: dict[str, int] = {}
    for t in trimmed:
        if t.startswith("node:"):
            mod = t[5:].split("|", 1)[0]
            nodes[mod] = nodes.get(mod, 0) + 1
    parts = []
    if counts_only:
        if hop:
            parts.append(f"hop {len(hop)}")
        if nodes:
            parts.append(f"nodes {sum(nodes.values())}")
        if fst:
            parts.append(f"forest {len(fst)}")
        return f"# trimmed: {len(trimmed)} ({'; '.join(parts)})"
    if hop:
        parts.append("hop: " + ", ".join(hop))
    if nodes:
        parts.append("nodes: " + ", ".join(f"{m} {n}" for m, n in nodes.items()))
    if fst:
        parts.append("forest: " + ", ".join(fst))
    return f"# trimmed: {len(trimmed)} ({'; '.join(parts)})"


def built_lines(p: Pack, named: bool = True) -> list[str]:
    """The freshness header: where the pack came from and, if named, what changed since.

    Rendered before "## Forest" so budget cuts never remove it.
    """
    b = p.built
    if b is None:
        return []
    if b.behind is None:
        where = "not on HEAD's history"
    elif b.behind == 0:
        where = "current"
    else:
        where = f"{b.behind} commit{'' if b.behind == 1 else 's'} behind HEAD"
    n = len(b.differs)
    tree = "matches" if n == 0 else f"{n} file{'' if n == 1 else 's'} differ{'s' if n == 1 else ''}"
    head = b.head[:7] if b.head else "none"
    out = [f"# built from {p.sha[:7]} ({b.source}); HEAD {head}; {where}; working tree {tree}"]
    if named and b.differs:
        shown = ", ".join(b.differs[:WARN_NAMES])
        more = f" (+{n - WARN_NAMES} more)" if n > WARN_NAMES else ""
        out.append(f"# warning: changed since this pack: {shown}{more}")
    return out


def pack_markdown(p: Pack, for_label: str = "") -> str:
    noop = next((n for n in NOOP_NOTES if n in p.notes), None)
    if noop:
        # card 1341: one line, so a pack with nothing to add costs as little as it
        # can; the card 1315 warning still follows when the --for files changed
        warn = [x for x in built_lines(p) if x.startswith("# warning:")]
        return "\n".join([f"# rco pack  (SHA={p.sha[:7]}): {noop}", *warn]) + "\n"
    flagged = _name_flags(p.notes)
    out = [f"# rco pack  (SHA={p.sha[:7]}, budget={p.budget} estimated tokens)"]
    out += built_lines(p, named=bool(p.trees) or bool(for_label))
    out += ["", "## Forest"]
    out += [forest_line(e, flagged.get(e.id, "")) for e in p.forest]
    if p.trees:
        out += ["", f"## Trees (files imported by --for {for_label}; the --for files are not outlined)"
                if for_label else "## Trees (files imported by --for)"]
        prev = None
        for t in p.trees:
            if t.module != prev:
                out.append(f"### {t.module}")
                prev = t.module
            for n in t.nodes:
                out += _tree_lines(n)
    if p.hop:
        out += ["", "## One hop (name and one line)"]
        out += [f"- {h.id}: {h.line}" for h in p.hop]
    out += ["", f"# budget used: ~{p.used} / {p.budget} estimated tokens"]
    if p.trimmed:
        out.append(_trimmed_summary(p.trimmed, TRIM_COUNTS in p.notes))
    return "\n".join(out) + "\n"


def _measure(p: Pack, label: str) -> int:
    # `used` is part of the text; repeat until the digit length settles
    used = 0
    for _ in range(4):
        nxt = estimate_tokens(pack_markdown(replace(p, used=used), label))
        if nxt == used:
            return used
        used = nxt
    return used


def _code_sources(repo: Path, sha: str) -> dict[str, bytes]:
    """Bytes of every Python or bundled-language file at sha, for the call index."""
    ents = [e for e in gitread.ls_tree(repo, sha)
            if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)
            and (e.path.endswith(".py") or lang_of(e.path))]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return {e.path: data[e.blob] for e in ents}


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


def _built(repo: Path, sha: str, source: str, owners: Sequence[str] | None) -> BuiltFrom:
    """How fresh a pack at sha is. With --for, only paths in the owning modules count."""
    head = gitread.resolve(repo, "HEAD") if gitread.has_commit(repo) else None
    behind = gitread.commits_behind(repo, sha, head) if head else None
    differs = gitread.worktree_differs(repo, sha)
    if owners is not None:
        layout = modules.layout_at(repo, sha)
        keep = set(owners)
        differs = tuple(d for d in differs if layout.module_of(modules.for_path(d)) in keep)
    return BuiltFrom(source, head, behind, differs)


def pack(repo: Path, sha: str, for_paths: Sequence[str] | None = None,
         budget: int = DEFAULT_BUDGET, store: Store | None = None,
         fit_to_imports: bool = True, as_if_head: bool = False) -> Pack:
    """Use the store when it has sha indexed; else build from git, writing nothing.

    fit_to_imports (card 1341; the CLI always sets it): with --for, print the
    short no-op pack when the --for files import nothing in the repo, and cap
    the budget at half the estimated tokens of the imported files. Tests of
    the cut order turn it off to keep a fixed budget on small fixtures.

    as_if_head (card 1344; rco replay): render the freshness header as if sha
    were HEAD with a clean working tree, which is what an agent packing at
    that commit sees. Replay's own checkout sits at a later HEAD, and without
    this every replayed pack carried a "files differ" warning no agent gets.
    """
    if budget < MIN_BUDGET:
        raise UsageError(f"--budget must be at least {MIN_BUDGET}")
    m = _map_from_store(store, sha) if store is not None else None
    notes: list[str] = []
    source = "store"
    if m is None:
        m = build.build_map(repo, sha)
        notes.append("built from git (no published SHA in the store)")
        source = "git"
    sources = forest.read_sources(repo, sha)
    f = forest.compute_forest(m, imports.module_edges(forest.map_modules(m), sources), sources)
    entries = list(f.entries)
    if store is not None:
        entries = [replace(e, prose=store.get_prose(e.id)) for e in entries]
    trees_by = {t.module: t for t in m.trees}
    entries = [e if e.prose is not None and e.prose.text.strip()
               else replace(e, prose=derived_prose(trees_by[e.id], e.tree_hash))
               if e.id in trees_by else e for e in entries]
    entries = [replace(e, prose=None) if e.prose is not None and not e.prose.text.strip() else e
               for e in entries]
    by_id = {e.id: e for e in entries}
    if store is not None:
        from rco.prose import flags as prose_flags
        for e in entries:
            t = trees_by.get(e.id)
            bad = prose_flags(e.prose, t) if t is not None else ()
            if bad:
                notes.append(f"name-in-tree: {e.id}: {', '.join(bad)}")

    owners: list[str] = []
    if for_paths:
        layout = modules.layout_at(repo, sha)
        owners = sorted({layout.module_of(modules.for_path(p)) for p in for_paths} & set(by_id))
        if not owners:
            notes.append("no module owns the --for paths")
    hop_ids = sorted({n for o in owners for n in by_id[o].depends_on + by_id[o].used_by}
                     - set(owners), key=_hop_key)
    built = (BuiltFrom(source, sha, 0, ()) if as_if_head
             else _built(repo, sha, source, owners if for_paths else None))
    trees: list[Tree] = []
    if for_paths:
        code = _code_sources(repo, sha)
        fps = sorted({modules.for_path(p) for p in for_paths})
        paths = [e.path for e in gitread.ls_tree(repo, sha)]
        ranked = rank_imports(fps, imports.file_graph(paths, code, only=fps))
        if fit_to_imports and owners and not ranked:
            # card 1341: nothing in the repo to outline; any pack would cost more
            # than reading the --for files alone
            return _noop_pack(sha, budget, (*notes, NOOP_NOTE), built, " ".join(for_paths))
        if fit_to_imports and ranked:
            # card 1341: the budget is a cap, not a fill target
            half = sum(estimate_tokens(code[p].decode("utf-8", "replace")) for p in ranked) // 2
            if min(budget, half) < MIN_BUDGET:
                # card 1341 (auditor 7164): too little to outline in the smallest pack
                return _noop_pack(sha, budget, (*notes, SMALL_NOTE), built, " ".join(for_paths))
            if half < budget:
                budget = half
                notes.append(f"budget scaled to {budget}: half the estimated tokens of the imported files")
        rank = {p: i for i, p in enumerate(ranked)}
        per_file: dict[str, list[tuple[Tree, Node]]] = {}
        for t in m.trees:
            for n in t.nodes:
                if n.path in rank:
                    per_file.setdefault(n.path, []).append((t, n))
        # one Tree per imported file, nearest first, so cut_one (last node of
        # the last Tree) always cuts the farthest file first
        for p in sorted(per_file, key=rank.__getitem__):
            t0 = per_file[p][0][0]
            trees.append(Tree(t0.module, t0.module_hash, t0.extractors,
                              tuple(n for _, n in per_file[p])))
        if trees:
            idx = call_index(m.trees, code, focus=trees)
            trees = [with_derived_docs(t, idx) for t in trees]
    hop = [HopLine(h, _one_line(by_id[h])) for h in hop_ids]
    neighbors: list[str] = []
    if owners:
        # card 1342: with --for, the Forest is the owners and their one-hop
        # modules; the whole-repo Forest stays on the path without --for
        keep = set(owners) | set(hop_ids)
        entries = [e for e in entries if e.id in keep]
        neighbors = list(reversed(hop_ids))  # cut order: test modules, then by id from the end
    stale = tuple(sorted(e.id for e in entries if is_stale(e.prose, e.tree_hash)))
    label = " ".join(for_paths or ())
    trimmed: list[str] = []

    def current() -> Pack:
        return Pack(sha, budget, 0, tuple(entries), tuple(trees), tuple(hop), tuple(trimmed),
                    stale, tuple(sorted(notes)), built)

    all_ids = {e.id for e in entries}

    def cut_one() -> bool:
        """Cut one item. False when nothing is left to cut."""
        if hop:
            trimmed.append(f"hop:{hop.pop().id}")
            return True
        while neighbors:  # card 1342: neighbor Forest lines go before any Tree node
            mid = neighbors.pop(0)
            idx = next((i for i, e in enumerate(entries) if e.id == mid), None)
            if idx is not None:
                trimmed.append(f"forest:{entries.pop(idx).id}")
                return True
        # with --for only the owners' lines are left here, and they go last
        idx = None if owners else _next_forest_cut(entries, all_ids, empty_only=True)
        if idx is not None:
            trimmed.append(f"forest:{entries.pop(idx).id}")
            return True
        cut = len(trees) - 1 if trees else None
        if cut is not None:
            t = trees[cut]
            if not t.nodes:
                del trees[cut]
                return True
            last = t.nodes[-1]
            trimmed.append(f"node:{t.module}|{node_key(last.path, last.name, last.kind)}")
            t = Tree(t.module, t.module_hash, t.extractors, t.nodes[:-1])
            if t.nodes:
                trees[cut] = t
            else:
                del trees[cut]  # every node is named in trimmed; drop the empty heading
            return True
        idx = _next_forest_cut(entries, all_ids, empty_only=False)
        if idx is not None:
            trimmed.append(f"forest:{entries.pop(idx).id}")
            return True
        return False

    while _measure(current(), label) > budget:
        if not cut_one():
            break
    # leftover budget: put hop lines back, last cut first, while they still fit.
    # The trial measure uses the trimmed list that would actually be emitted, so
    # putting a hop back cannot hide a longer # trimmed line.
    hop_cuts = [t for t in trimmed if t.startswith("hop:")]
    other_cuts = [t for t in trimmed if not t.startswith("hop:")]
    pending = list(hop_cuts)
    for item in reversed(hop_cuts):
        mid = item[4:]
        hop.append(HopLine(mid, _one_line(by_id[mid])))
        pending.remove(item)
        trimmed = pending + other_cuts
        if _measure(current(), label) <= budget:
            continue
        hop.pop()
        pending.append(item)
        trimmed = pending + other_cuts
    pending = [t for t in hop_cuts if t in pending]  # original cut order
    trimmed = pending + other_cuts
    hop.sort(key=lambda h: _hop_key(h.id))
    while _measure(current(), label) > budget:
        if not cut_one():
            break
    p = current()
    shown = {e.id for e in p.forest}
    p = replace(p, stale=tuple(s for s in stale if s in shown))
    p = replace(p, used=_measure(p, label))
    if p.used > p.budget and p.trimmed and TRIM_COUNTS not in p.notes:
        # card 1343: the named cut list in the footer can itself overflow
        compact = replace(p, notes=tuple(sorted((*p.notes, TRIM_COUNTS))), used=0)
        compact = replace(compact, used=_measure(compact, label))
        if compact.used <= p.used:
            p = compact
    if p.used > p.budget:
        residue = (not p.forest and not p.trees and not p.hop)
        note = (f"over budget: {p.used} / {p.budget} (header and footer)" if residue
                else f"over budget: {p.used} / {p.budget}")
        if note not in p.notes:
            p = replace(p, notes=tuple(sorted((*p.notes, note))))
        p = replace(p, used=_measure(p, label))  # the new note is not in the markdown
    return p


def _noop_pack(sha: str, budget: int, notes: Sequence[str], built: BuiltFrom | None,
               label: str) -> Pack:
    """The short pack for --for files with nothing worth outlining (exit 0)."""
    p = Pack(sha, budget, 0, (), (), (), (), (), tuple(sorted(notes)), built)
    return replace(p, used=_measure(p, label))


def empty_pack(budget: int = DEFAULT_BUDGET) -> Pack:
    """A pack for a git repo that has no commits yet (empty Forest, exit 0)."""
    if budget < MIN_BUDGET:
        raise UsageError(f"--budget must be at least {MIN_BUDGET}")
    p = Pack("HEAD", budget, 0, (), (), (), (), (), ())
    return replace(p, used=_measure(p, ""))


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
