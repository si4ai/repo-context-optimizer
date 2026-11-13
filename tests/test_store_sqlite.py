"""Phase 2 step 1: the SQLite store passes conformance, its CAS holds across processes."""
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from rco.store import conformance as conf
from rco import fixture
from rco.forest_model import Prose, ProseSource
from rco.store import IndexRow, MemoryStore, NodeKind
from rco.store.sqlite import DB_RELPATH, EXCLUDE_LINE, SqliteStore, open_repo_store

SRC = str(Path(__file__).resolve().parents[1] / "src")


def _makers(tmp_path):
    n = iter(range(1000))
    return {
        "memory": MemoryStore,
        "sqlite": lambda: SqliteStore(tmp_path / f"db{next(n)}" / "rco.db"),
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
@pytest.mark.parametrize("check", conf.CHECKS, ids=lambda c: c.__name__)
def test_conformance(backend, check, tmp_path):
    check(_makers(tmp_path)[backend]())


def test_wal_and_no_stale_column(tmp_path):
    s = SqliteStore(tmp_path / "rco.db")
    assert s._one("PRAGMA journal_mode")[0] == "wal"
    db = sqlite3.connect(tmp_path / "rco.db")
    cols = [r[1] for t in ("node", "sha_index", "pointer", "prose", "queue", "notice")
            for r in db.execute(f"PRAGMA table_info({t})")]
    assert cols and not any("stale" in c for c in cols)


def test_persists_across_reopen(tmp_path):
    p = tmp_path / "rco.db"
    s = SqliteStore(p)
    s.put_node(NodeKind.TREE, conf.H1, {"a": 1})
    s.index_sha("s1", (IndexRow("a", conf.H1, conf.H1),))
    s.set_pointer("main", None, "s1")
    s.put_prose("a", Prose("x", ProseSource.MINED, conf.H1))
    s.close()
    s = SqliteStore(p)
    assert s.get_node(NodeKind.TREE, conf.H1) == {"a": 1}
    assert s.list_by_sha("s1") == (IndexRow("a", conf.H1, conf.H1),)
    assert s.get_pointer("main") == "s1"
    assert s.get_prose("a").text == "x"


def test_dump_is_idempotent(tmp_path):
    s = SqliteStore(tmp_path / "rco.db")

    def work():
        s.put_node(NodeKind.TREE, conf.H1, {"a": 1})
        s.index_sha("s1", (IndexRow("a", conf.H1, conf.H1),))
        s.set_pointer("main", None, "s1")
        s.put_prose("a", Prose("x", ProseSource.MINED, conf.H1))
        s.enqueue("a", conf.H1)

    work()
    first = s.dump()
    work()
    assert s.dump() == first


_RACER = """
import sys, time
from pathlib import Path
from rco.store.sqlite import SqliteStore
db, go, i = sys.argv[1], Path(sys.argv[2]), sys.argv[3]
s = SqliteStore(Path(db))
while not go.exists():
    time.sleep(0.001)
print(1 if s.set_pointer("main", "old", "new" + i) else 0)
"""


def test_processes_racing_set_pointer_one_winner(tmp_path):
    db = tmp_path / "rco.db"
    SqliteStore(db).set_pointer("main", None, "old")
    go = tmp_path / "go"
    env = dict(os.environ, PYTHONPATH=SRC, PYTHONDONTWRITEBYTECODE="1")
    procs = [subprocess.Popen([sys.executable, "-c", _RACER, str(db), str(go), str(i)],
                              stdout=subprocess.PIPE, env=env) for i in range(8)]
    time.sleep(0.5)  # let every racer open the database first
    go.touch()
    outs = [p.communicate(timeout=60)[0].decode().strip() for p in procs]
    assert all(p.returncode == 0 for p in procs)
    winners = [i for i, o in enumerate(outs) if o == "1"]
    assert len(winners) == 1, outs
    assert SqliteStore(db).get_pointer("main") == f"new{winners[0]}"


def test_open_repo_store_keeps_git_status_clean(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "repo", {"a.py": "x = 1\n"})
    s = open_repo_store(repo)
    s.set_pointer("main", None, "s")
    assert (repo / DB_RELPATH).exists()
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""
    assert not (repo / ".gitignore").exists()
    excl = (repo / ".git" / "info" / "exclude").read_text().splitlines()
    open_repo_store(repo)  # second use adds nothing
    assert (repo / ".git" / "info" / "exclude").read_text().splitlines().count(EXCLUDE_LINE) == 1
    assert EXCLUDE_LINE in excl


def test_open_repo_store_in_a_worktree(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "repo", {"a.py": "x = 1\n"})
    wt = tmp_path / "wt"
    fixture.git(repo, "worktree", "add", "-q", "-b", "side", str(wt))
    open_repo_store(wt)
    assert (wt / DB_RELPATH).exists()
    assert fixture.git(wt, "status", "--porcelain", "--untracked-files=all") == ""
