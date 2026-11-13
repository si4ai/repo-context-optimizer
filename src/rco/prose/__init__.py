"""Prose for Forest entries (Phase 2 step 4).

Mined prose is the default (zero tokens). When nothing can be mined, prose
is derived from the Tree's facts (prose/derive.py, also zero tokens). The
LLM path (prose/llm.py) is a hook, off by default. The queue worker writes prose for a (module,
tree_hash) row only when that TreeHash is what the published pointer shows
for the module. `rco update` queues only after its swap (rule 4), so any
other row was superseded and is dropped; the newer update queued its own. Staleness and name flags are computed at read
time and never stored.
"""
from __future__ import annotations

from pathlib import Path

from rco import canon, gitread
from rco.forest_model import DEFAULT_POINTER, Prose, ProseSource
from rco.model import Module, Tree
from rco.prose.check import unknown_names
from rco.prose.derive import DERIVED_PROMPT_VERSION, module_prose
from rco.prose.mine import mine
from rco.store import NodeKind, Store


def _sources(repo: Path, sha: str, files: tuple[str, ...]) -> dict[str, bytes]:
    want = set(files)
    ents = [e for e in gitread.ls_tree(repo, sha) if e.path in want
            and e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return {e.path: data[e.blob] for e in ents}


def drain_queue(repo: Path, store: Store, name: str = DEFAULT_POINTER) -> int:
    """Write prose for queued (module, tree_hash) rows of published SHAs; returns rows written."""
    rows = store.queued()  # before the pointer: every row here was queued at or before it
    sha = store.get_pointer(name)
    if sha is None:
        return 0  # nothing published: write nothing
    published = {r.module: r for r in store.list_by_sha(sha)}
    done = 0
    for module, tree_hash in rows:
        row = published.get(module)
        if row is None or row.tree_hash != tree_hash:
            # update queues only after its swap (rule 4), so a row that does not
            # match the pointer was superseded; the newer update queued its own.
            # Either way no prose is written for a TreeHash the pointer does not show.
            store.dequeue(module, tree_hash)
            continue
        obj = store.get_node(NodeKind.TREE, row.module_hash)
        if obj is None:
            continue
        tree = canon.tree_from_obj(obj)
        files = tuple(sorted({n.path for n in tree.nodes if n.path}))
        text = mine(Module(module, files), _sources(repo, sha, files))
        prose = Prose(text, ProseSource.MINED, tree_hash)
        if not text:
            prose = derived_prose(tree, tree_hash)
        store.put_prose(module, prose)
        store.dequeue(module, tree_hash)
        done += 1
    return done


def derived_prose(tree: Tree, tree_hash: str) -> Prose:
    """Prose derived from the Tree alone: what it defines and owns."""
    from rco.forest import owns_of
    o = owns_of(tree)
    return Prose(module_prose(tree, o.commands, o.tables, o.routes), ProseSource.DERIVED,
                 tree_hash, prompt_version=DERIVED_PROMPT_VERSION)


def flags(prose: Prose | None, tree: Tree) -> tuple[str, ...]:
    """Names the prose cites that are not in the Tree; () means the prose checks out."""
    return () if prose is None else unknown_names(prose.text, tree)
