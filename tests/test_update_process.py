"""Card 1318: the real `rco update` command is rerun-safe, kill-safe and concurrency-safe.

These tests start real processes (the CLI, and a child that SIGKILLs itself
at a crash point), unlike tests/test_update_props.py, which runs in-process.
Each compares the store with a clean single run (SqliteStore.dump).
"""
import json
import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from rco.prose import drain_queue
from rco.store.sqlite import SqliteStore
from rco.update import update
from update_history import make_history

ROOT = Path(__file__).resolve().parents[1]
ENV = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
POSIX = pytest.mark.skipif(not hasattr(signal, "SIGKILL") or os.name == "nt",
                           reason="SIGKILL is POSIX only")


def rco(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "rco", *args, "--repo", str(repo)],
                          capture_output=True, env=ENV, timeout=120)


def _db(repo: Path) -> Path:
    return repo / ".rco" / "rco.db"


def publish(repo: Path, sha: str) -> None:
    """In-process update without draining, as the reference does."""
    s = SqliteStore(_db(repo))
    update(repo, s, sha)
    s.close()


def die_at(repo: Path, point: str, sha: str) -> int:
    """A child process that runs update (and drain) to sha and SIGKILLs itself at point."""
    if point == "drain":
        body = f"""
            from rco.store.sqlite import SqliteStore
            from rco.update import update
            from rco.prose import drain_queue
            s = SqliteStore(Path({str(_db(repo))!r}))
            update(Path({str(repo)!r}), s, {sha!r})
            def die(*a, **k):
                os.kill(os.getpid(), signal.SIGKILL)
            SqliteStore.put_prose = die
            drain_queue(Path({str(repo)!r}), s)
        """
    else:
        body = f"""
            from rco.store.sqlite import SqliteStore
            from rco.update import update
            def hook(p, d):
                if p == {point!r}:
                    os.kill(os.getpid(), signal.SIGKILL)
            s = SqliteStore(Path({str(_db(repo))!r}))
            update(Path({str(repo)!r}), s, {sha!r}, hook=hook)
        """
    code = "import os, signal\nfrom pathlib import Path\n" + textwrap.dedent(body)
    p = subprocess.run([sys.executable, "-c", code], env=ENV, capture_output=True, timeout=120)
    return p.returncode


def reference(tmp: Path, shas: list[str]) -> bytes:
    repo, ref_shas = make_history(tmp / "ref")
    assert ref_shas == shas  # FIXED_DATE makes the history byte-identical
    s = SqliteStore(tmp / "ref.db")
    update(repo, s, shas[1])
    update(repo, s, shas[4])
    drain_queue(repo, s)
    return s.dump()


def dump(repo: Path) -> bytes:
    s = SqliteStore(_db(repo))
    try:
        return s.dump()
    finally:
        s.close()


def healthy(repo: Path, why: str) -> None:
    con = sqlite3.connect(str(_db(repo)))
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok", why
    finally:
        con.close()
    p = rco(repo, "pack", "--format", "json")
    assert p.returncode == 0, f"{why}: rco pack exited {p.returncode}: {p.stderr.decode()}"
    obj = json.loads(p.stdout)
    assert obj["stale"] == [], f"{why}: stale modules {obj['stale']}"
    assert not any("built from git" in n for n in obj["notes"]), f"{why}: pack not from the store"


def test_cli_update_twice_equals_once(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    publish(repo, shas[1])
    for i in (1, 2):
        p = rco(repo, "update")
        assert p.returncode == 0, f"run {i}: {p.stderr.decode()}"
    assert dump(repo) == reference(tmp_path, shas), "two CLI updates differ from one clean run"
    healthy(repo, "after two CLI updates")


@POSIX
@pytest.mark.parametrize("point", ["extract", "index", "swap", "queue", "drain"])
def test_kill_then_rerun(tmp_path, point):
    repo, shas = make_history(tmp_path / "r")
    publish(repo, shas[1])
    code = die_at(repo, point, shas[4])
    assert code == -signal.SIGKILL, f"killed at {point}: child exited {code}, not by SIGKILL"
    p = rco(repo, "update")
    assert p.returncode == 0, f"rerun after a kill at {point}: {p.stderr.decode()}"
    assert dump(repo) == reference(tmp_path, shas), \
        f"killed at {point}, then rco update: store differs from a clean single run"
    healthy(repo, f"killed at {point}, then rco update")


def test_concurrent_updates(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    publish(repo, shas[1])
    procs = [subprocess.Popen([sys.executable, "-m", "rco", "update", sha, "--repo", str(repo)],
                              env=ENV, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for sha in (shas[2], "HEAD", "HEAD")]
    outs = [p.communicate(timeout=120) for p in procs]
    for p, (_, err) in zip(procs, outs):
        assert p.returncode == 0, f"concurrent rco update exited {p.returncode}: {err.decode()}"
    p = rco(repo, "update")
    assert p.returncode == 0, f"update after the race: {p.stderr.decode()}"
    s = SqliteStore(_db(repo))
    ref = SqliteStore(tmp_path / "ref2.db")
    rrepo, _ = make_history(tmp_path / "ref2")
    update(rrepo, ref, shas[1])
    update(rrepo, ref, shas[4])
    try:
        assert s.get_pointer("main") == shas[4], "after the race the pointer is not HEAD"
        assert s.list_by_sha(shas[4]) == ref.list_by_sha(shas[4]), "HEAD's sha index differs"
        assert s.queued() == (), f"queue not empty after the race: {s.queued()}"
    finally:
        s.close()
    healthy(repo, "after three concurrent updates")
