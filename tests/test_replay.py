"""phase 2 step 7: incremental rco update equals a full rebuild at every SHA."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from rco.store import MemoryStore, NodeKind
from rco.update import update
from update_history import make_history

HERE = Path(__file__).resolve().parents[1]


def _index(store, sha: str) -> tuple[tuple[str, str, str], ...]:
    return tuple((r.module, r.module_hash, r.tree_hash) for r in store.list_by_sha(sha))


def _trees(store, sha: str) -> dict[str, object]:
    return {r.module: store.get_node(NodeKind.TREE, r.module_hash) for r in store.list_by_sha(sha)}


def _first_parents(repo: Path, tip: str = "HEAD", limit: int | None = None) -> list[str]:
    cmd = ["git", "-C", str(repo), "rev-list", "--first-parent", tip]
    if limit is not None:
        cmd.insert(-1, f"--max-count={limit}")
    raw = subprocess.run(cmd, capture_output=True, check=True, text=True)
    shas = [s for s in raw.stdout.split() if s]
    shas.reverse()  # oldest first
    return shas


def _assert_inc_equals_full(repo: Path, shas: list[str]) -> None:
    inc = MemoryStore()
    for sha in shas:
        update(repo, inc, sha)
        full = MemoryStore()
        update(repo, full, sha)
        assert _index(inc, sha) == _index(full, sha)
        assert _trees(inc, sha) == _trees(full, sha)


def test_incremental_equals_full_rebuild_on_fixture(tmp_path):
    repo, shas = make_history(tmp_path / "h")
    assert len(shas) >= 2
    _assert_inc_equals_full(repo, shas)


def test_incremental_equals_full_rebuild_on_this_repo():
    try:
        shas = _first_parents(HERE)
    except subprocess.CalledProcessError:
        pytest.skip("not a git checkout")
    if len(shas) < 2:
        pytest.skip("need full git history (two or more first-parent SHAs)")
    _assert_inc_equals_full(HERE, shas)


@pytest.mark.skipif(not os.environ.get("RCO_REPLAY_MILL"),
                    reason="set RCO_REPLAY_MILL to a mill checkout to run this")
def test_incremental_equals_full_rebuild_on_mill():
    repo = Path(os.environ["RCO_REPLAY_MILL"])
    if not (repo / ".git").exists() and not (repo / ".git").is_file():
        # a worktree has a .git file; a bare checkout has a .git dir
        if not (repo / "HEAD").exists():
            pytest.skip("RCO_REPLAY_MILL is not a git checkout")
    shas = _first_parents(repo, "HEAD", limit=100)
    if len(shas) < 2:
        pytest.skip("mill checkout has too little history")
    _assert_inc_equals_full(repo, shas)
