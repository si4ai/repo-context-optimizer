"""Phase 2 step 4: the queue worker writes prose only for published TreeHashes (slice 4)."""
import shlex
import sys

import pytest

from rco import build, canon, fixture
from rco.forest_model import is_stale
from rco.prose import drain_queue
from rco.prose.llm import ProseCommandError, llm_prose
from rco.store import IndexRow, MemoryStore, NodeKind

FILES = {"pkg/__init__.py": '"""The pkg package."""\n',
         "pkg/core.py": "def run(x: int) -> int:\n    return x\n",
         "util/helpers.py": '"""Helpers."""\ndef clamp(x: int) -> int:\n    return x\n'}


def _index(store, repo, sha):
    m = build.build_map(repo, sha)
    for t in m.trees:
        store.put_node(NodeKind.TREE, t.module_hash, canon.tree_to_obj(t))
    rows = tuple(IndexRow(t.module, t.module_hash, canon.tree_hash(t)) for t in m.trees)
    store.index_sha(sha, rows)
    return {r.module: r.tree_hash for r in rows}


def test_no_prose_for_unpublished_sha(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    s = MemoryStore()
    th = _index(s, repo, a)
    for m, h in th.items():
        s.enqueue(m, h)
    assert drain_queue(repo, s) == 0  # indexed but the pointer is not set: unpublished
    assert s.get_prose("pkg") is None and len(s.queued()) == len(th)
    s.set_pointer("main", None, a)
    assert drain_queue(repo, s) == len(th)
    assert s.get_prose("pkg").text == "The pkg package."
    assert s.get_prose("util").text == "Helpers."
    assert s.queued() == ()


def test_row_for_a_tree_the_pointer_does_not_show_writes_nothing(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    s = MemoryStore()
    _index(s, repo, a)
    s.set_pointer("main", None, a)
    b = fixture.commit(repo, {"util/helpers.py": '"""Helpers v2."""\ndef clamp(x: int, y: int) -> int:\n    return x\n'})
    th_b = _index(s, repo, b)  # indexed, never published
    s.enqueue("util", th_b["util"])
    assert drain_queue(repo, s) == 0 and s.queued() == ()
    assert s.get_prose("util") is None  # nothing written for the unpublished TreeHash


def test_superseded_row_is_dropped_and_stale_is_computed(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    s = MemoryStore()
    th_a = _index(s, repo, a)
    s.set_pointer("main", None, a)
    s.enqueue("util", th_a["util"])
    drain_queue(repo, s)
    b = fixture.commit(repo, {"util/helpers.py": '"""Helpers v2."""\ndef clamp(x: int, y: int) -> int:\n    return x\n'})
    th_b = _index(s, repo, b)
    s.set_pointer("main", a, b)
    assert is_stale(s.get_prose("util"), th_b["util"])  # shown as stale until the rewrite
    s.enqueue("util", "sha256:superseded")
    assert drain_queue(repo, s) == 0 and s.queued() == ()


def test_llm_hook_runs_named_command_only(tmp_path):
    from rco.model import Tree
    t = Tree("pkg", "h", (), ())
    cmd = f"{shlex.quote(sys.executable)} -c \"import sys,json; json.load(sys.stdin); print('Short prose.')\""
    assert llm_prose(t, cmd) == "Short prose."
    with pytest.raises(ProseCommandError):
        llm_prose(t, "")
    with pytest.raises(ProseCommandError):
        llm_prose(t, f"{shlex.quote(sys.executable)} -c \"raise SystemExit(3)\"")
