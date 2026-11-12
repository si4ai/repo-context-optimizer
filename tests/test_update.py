"""Phase 2 step 3: `rco update` writes Trees, the index, then one forward CAS, then the queue."""
import json

import pytest

from rco import cli, fixture
from rco.output import Exit
from rco.store import MemoryStore, NodeKind
from rco.store.sqlite import SqliteStore
from rco.update import is_ancestor, update
from update_history import make_history


@pytest.fixture(params=["memory", "sqlite"])
def store(request, tmp_path):
    return MemoryStore() if request.param == "memory" else SqliteStore(tmp_path / "s" / "rco.db")


@pytest.fixture
def hist(tmp_path):
    return make_history(tmp_path / "repo")


def test_first_update_publishes_everything(hist, store):
    repo, shas = hist
    r = update(repo, store, shas[0])
    assert r.swapped and r.old is None and store.get_pointer("main") == shas[0]
    mods = [row.module for row in store.list_by_sha(shas[0])]
    assert mods == [".", "db", "pkg", "util"]
    assert list(r.extracted) == mods and list(r.queued) == mods


def test_body_only_change_extracts_one_queues_none(hist, store):
    repo, shas = hist
    update(repo, store, shas[0])
    r = update(repo, store, shas[1])
    assert r.swapped and r.extracted == ("pkg",) and r.queued == ()


def test_contract_change_queues_that_module(hist, store):
    repo, shas = hist
    update(repo, store, shas[1])
    r = update(repo, store, shas[2])
    assert r.extracted == ("util",) and r.queued == ("util",)
    assert store.queued()[-1][0] == "util"


def test_skipped_sha_still_complete(hist, store):
    repo, shas = hist
    update(repo, store, shas[0])
    r = update(repo, store, shas[4])  # A straight to E
    assert r.swapped and set(r.extracted) == {"db", "extra", "pkg", "util"}
    for row in store.list_by_sha(shas[4]):
        assert store.get_node(NodeKind.TREE, row.module_hash) is not None


def test_same_update_twice_is_a_noop(hist, store):
    repo, shas = hist
    update(repo, store, shas[0])
    r = update(repo, store, shas[0])
    assert not r.swapped and r.notes == ("already published",)


def test_older_sha_is_refused(hist, store):
    repo, shas = hist
    update(repo, store, shas[3])
    r = update(repo, store, shas[1])
    assert not r.swapped and r.notes[0].startswith("refused")
    assert store.get_pointer("main") == shas[3]
    assert store.list_by_sha(shas[1]) == ()  # refused before writing anything


def test_is_ancestor(hist):
    repo, shas = hist
    assert is_ancestor(repo, shas[0], shas[2]) and not is_ancestor(repo, shas[2], shas[0])
    assert is_ancestor(repo, shas[1], shas[1])
    assert not is_ancestor(repo, "0" * 40, shas[1])


def test_cli_update_uses_repo_store(hist, capsys):
    repo, shas = hist
    assert cli.main(["update", "--repo", str(repo), "--format", "json"]) == Exit.OK
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "rco.update/1" and out["swapped"] and out["new"] == shas[-1]
    assert cli.main(["update", shas[1], "--repo", str(repo)]) == Exit.OK  # refused: exit 0
    assert "not moved" in capsys.readouterr().out
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_cli_update_drains_mined_prose(tmp_path, capsys):
    """rco update must mine prose so a later pack shows it (W1)."""
    files = {
        "pkg/__init__.py": '"""pkg does the work."""\n',
        "pkg/core.py": "def run(x: int) -> int:\n    return x\n",
    }
    repo, sha = fixture.make_repo(tmp_path / "r", files)
    assert cli.main(["update", "--repo", str(repo)]) == 0
    capsys.readouterr()
    assert cli.main(["pack", "--repo", str(repo), "--format", "json"]) == 0
    p = json.loads(capsys.readouterr().out)
    forest = {e["id"]: e for e in p["forest"]}
    prose = forest["pkg"].get("prose") or {}
    assert prose.get("text") == "pkg does the work."
    assert prose.get("source") == "mined"
    assert p["stale"] == []
