"""Card 1314: one JSON line per rco run in .rco/events.jsonl, safe under concurrent runs."""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from rco import cli, events, fixture
from rco.output import Exit

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).parent
FIELDS = {"v": int, "run": str, "ts": str, "cmd": str, "exit": int, "ms": int}


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _repo(tmp_path, rco_dir=True):
    repo, sha = fixture.make_repo(tmp_path / "repo", _files(HERE / "fixtures" / "forest_basic"))
    if rco_dir:
        (repo / ".rco").mkdir()
    return repo, sha


def _lines(repo: Path) -> list[dict]:
    raw = (repo / events.EVENTS_RELPATH).read_bytes()
    assert raw.endswith(b"\n")
    return [json.loads(x) for x in raw.decode("utf-8").splitlines()]


def _check_fields(ev: dict) -> None:
    for k, t in FIELDS.items():
        if k == "cmd":
            assert ev[k] is None or isinstance(ev[k], str), (k, ev[k])
            continue
        assert isinstance(ev[k], t), (k, ev[k])
    assert ev["v"] == 1
    assert re.fullmatch(r"[0-9a-f]{32}", ev["run"])
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", ev["ts"])
    assert ev["store"] is None or isinstance(ev["store"], str)
    assert ev["head"] is None or re.fullmatch(r"[0-9a-f]{40}", ev["head"])
    assert ev["ms"] >= 0
    allowed = {*FIELDS, "store", "head", "for"}
    if "for_cut" in ev:
        assert isinstance(ev["for_cut"], int) and ev["for_cut"] > 0
        assert ev["for"] is not None
        allowed = {*allowed, "for_cut"}
    pack_keys = {"pack_tokens", "pack_budget", "pack_skipped", "pack_overrun"}
    if pack_keys & set(ev):
        assert pack_keys <= set(ev)
        assert isinstance(ev["pack_tokens"], int) and ev["pack_tokens"] >= 0
        assert isinstance(ev["pack_budget"], int) and ev["pack_budget"] >= 0
        assert isinstance(ev["pack_skipped"], bool)
        assert isinstance(ev["pack_overrun"], int) and ev["pack_overrun"] >= 0
        allowed = {*allowed, *pack_keys}
    assert set(ev) == allowed


def test_one_line_per_run(tmp_path):
    repo, sha = _repo(tmp_path)
    r = str(repo)
    codes = [cli.main(["pack", "--repo", r, "--for", "src/app/cli.py"]),
             cli.main(["update", "--repo", r]),
             cli.main(["tree", "--repo", r])]
    got = _lines(repo)
    assert [e["cmd"] for e in got] == ["pack", "update", "tree"]
    assert [e["exit"] for e in got] == codes
    for e in got:
        _check_fields(e)
        assert e["head"] == sha
    assert got[0]["store"] is None and got[0]["for"] == ["src/app/cli.py"]
    assert got[1]["store"] == sha and got[1]["for"] is None  # pointer at exit
    assert got[2]["store"] == sha
    assert len({e["run"] for e in got}) == 3


def test_metrics_subcommand_named(tmp_path):
    repo, _ = _repo(tmp_path)
    missing = tmp_path / "nope.json"
    code = cli.main(["metrics", "check", str(missing)])
    assert code != Exit.OK
    # metrics check has no --repo; run it from the repo so the event lands there
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        cli.main(["metrics", "check", str(missing)])
    finally:
        os.chdir(cwd)
    assert [e["cmd"] for e in _lines(repo)] == ["metrics check"]


def test_failed_run_still_logged(tmp_path):
    repo, _ = _repo(tmp_path)
    assert cli.main(["pack", "--repo", str(repo), "--budget", "1"]) == Exit.USAGE
    (ev,) = _lines(repo)
    assert ev["cmd"] == "pack" and ev["exit"] == 2


def test_help_and_version_are_not_runs(tmp_path, capsys):
    repo, _ = _repo(tmp_path)
    cwd = os.getcwd()
    os.chdir(repo)
    try:
        assert cli.main(["--version"]) == 0
        assert cli.main(["pack", "--help"]) == 0
    finally:
        os.chdir(cwd)
    assert not (repo / events.EVENTS_RELPATH).exists()


def test_parallel_runs_no_torn_lines(tmp_path):
    """The done-when: N concurrent commands leave exactly N whole lines with distinct run ids."""
    repo, _ = _repo(tmp_path)
    env = {**os.environ, "PYTHONPATH": str(ROOT / "src")}
    n = 12
    pack = [sys.executable, "-m", "rco", "pack", "--repo", str(repo), "--for", "src/app/cli.py"]
    update = [sys.executable, "-m", "rco", "update", "--repo", str(repo)]
    procs = [subprocess.Popen(update if i % 2 else pack, env=env,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
             for i in range(n)]
    for p in procs:
        p.wait(timeout=120)
    got = _lines(repo)
    assert len(got) == n
    assert len({e["run"] for e in got}) == n
    assert sorted(e["cmd"] for e in got) == ["pack"] * (n // 2) + ["update"] * (n // 2)
    for e in got:
        _check_fields(e)


def test_log_failure_never_breaks_command(tmp_path, capsys):
    repo, _ = _repo(tmp_path)
    (repo / events.EVENTS_RELPATH).mkdir()
    assert cli.main(["pack", "--repo", str(repo)]) == Exit.OK
    assert "events log not written" in capsys.readouterr().err


def test_no_rco_dir_no_write(tmp_path):
    repo, _ = _repo(tmp_path, rco_dir=False)
    assert cli.main(["pack", "--repo", str(repo), "--for", "src/app/cli.py"]) == Exit.OK
    assert not (repo / ".rco").exists()
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_not_a_git_repo_is_skipped(tmp_path):
    plain = tmp_path / "plain"
    (plain / ".rco").mkdir(parents=True)
    cli.main(["pack", "--repo", str(plain)])
    assert not (plain / events.EVENTS_RELPATH).exists()


def test_pack_for_field(tmp_path):
    repo, _ = _repo(tmp_path)
    cli.main(["pack", "--repo", str(repo), "--for", "src/app/cli.py", "src/app/core/engine.py"])
    (ev,) = _lines(repo)
    assert ev["for"] == ["src/app/cli.py", "src/app/core/engine.py"]
    assert "for_cut" not in ev


def test_line_cap(tmp_path):
    repo, _ = _repo(tmp_path)
    paths = [f"src/app/f{i:05d}.py" for i in range(700)]  # about 14 KB of paths
    assert len(json.dumps(paths)) > 10_000
    cli.main(["pack", "--repo", str(repo), "--for", *paths])
    raw = (repo / events.EVENTS_RELPATH).read_bytes()
    assert raw.count(b"\n") == 1 and len(raw) <= events.MAX_LINE
    (ev,) = _lines(repo)
    assert ev["cmd"] == "pack" and 0 < len(ev["for"]) < len(paths)
    assert ev["for"] == paths[:len(ev["for"])]  # a prefix, in order
    assert ev["for_cut"] == len(paths) - len(ev["for"])
    _check_fields(ev)


def test_store_pointer_read_does_not_lock(tmp_path):
    """read_pointer works while another connection holds the write lock."""
    import sqlite3
    from rco.store.sqlite import SqliteStore, read_pointer
    db = tmp_path / "s" / "rco.db"
    s = SqliteStore(db)
    s.set_pointer("main", None, "a" * 40)
    con = sqlite3.connect(str(db), isolation_level=None)
    con.execute("BEGIN IMMEDIATE")
    try:
        assert read_pointer(db, "main") == "a" * 40
    finally:
        con.execute("ROLLBACK")
        con.close()
    assert read_pointer(tmp_path / "missing.db", "main") is None
    assert read_pointer(db, "other") is None


def test_for_paths_logged_as_repo_paths(tmp_path):
    repo, _ = _repo(tmp_path)
    abs_p = repo / "src/app/cli.py"
    cli.main(["pack", "--repo", str(repo), "--for", "./src/app/cli.py", str(abs_p)])
    (ev,) = _lines(repo)
    assert ev["for"] == ["src/app/cli.py", "src/app/cli.py"]


def test_short_write_notes_and_keeps_exit(tmp_path, capsys, monkeypatch):
    repo, _ = _repo(tmp_path)
    real = os.write

    def short(fd, data):
        # Only the events line: other writes (git cat-file stdin) must be real.
        if (isinstance(data, (bytes, bytearray)) and data.startswith(b"{")
                and data.endswith(b"\n") and b'"run":' in data):
            return max(0, len(data) - 1)
        return real(fd, data)

    monkeypatch.setattr(os, "write", short)
    code = cli.main(["pack", "--repo", str(repo), "--for", "src/app/cli.py"])
    assert code == Exit.OK
    assert "events log short write" in capsys.readouterr().err


def test_usage_error_logs_null_cmd(tmp_path):
    repo, _ = _repo(tmp_path)
    code = cli.main(["pack", "--repo", str(repo), "--bogus"])
    assert code == 2
    (ev,) = _lines(repo)
    assert ev["cmd"] is None and ev["exit"] == 2
    _check_fields(ev)


def test_pack_fields_on_events_line(tmp_path):
    """Card 1351: a pack run records pack_tokens, budget, skipped, and overrun."""
    from rco import events
    repo, _ = fixture.make_repo(tmp_path / "r", {"a.py": "print(1)\n"})
    (repo / ".rco").mkdir()
    t0 = __import__("time").monotonic()
    events.record(repo, "pack", 0, t0, pack={
        "pack_tokens": 80, "pack_budget": 12000,
        "pack_skipped": False, "pack_overrun": 0,
    })
    rec = json.loads((repo / ".rco" / "events.jsonl").read_text().splitlines()[-1])
    assert rec["pack_tokens"] == 80
    assert rec["pack_budget"] == 12000
    assert rec["pack_skipped"] is False
    assert rec["pack_overrun"] == 0
    assert rec["cmd"] == "pack"


def test_pack_without_rco_dir_notes_on_stderr(tmp_path, capsys):
    """No .rco/: pack fields go to stderr and nothing is written in the tree."""
    from rco import events
    repo, _ = fixture.make_repo(tmp_path / "r", {"a.py": "print(1)\n"})
    assert not (repo / ".rco").exists()
    t0 = __import__("time").monotonic()
    events.record(repo, "pack", 0, t0, pack={
        "pack_tokens": 12, "pack_budget": 12000,
        "pack_skipped": True, "pack_overrun": 0,
    })
    err = capsys.readouterr().err
    assert "pack tokens=12" in err
    assert "budget=12000" in err
    assert "skipped=true" in err
    assert "overrun=0" in err
    assert not (repo / ".rco").exists()
    # no events file under the repo
    assert not list(repo.rglob("events.jsonl"))


def test_cli_pack_writes_pack_fields(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "r", {
        "pkg/__init__.py": "",
        "pkg/a.py": "X = 1\n",
        "pkg/b.py": "from pkg.a import X\n",
    })
    (repo / ".rco").mkdir()
    assert cli.main(["pack", "--repo", str(repo), "--for", "pkg/b.py"]) == Exit.OK
    rec = json.loads((repo / ".rco" / "events.jsonl").read_text().splitlines()[-1])
    assert rec["cmd"] == "pack"
    assert "pack_tokens" in rec and rec["pack_tokens"] >= 0
    assert rec["pack_budget"] == 12000 or rec["pack_budget"] > 0
    assert "pack_skipped" in rec
    assert rec["pack_overrun"] >= 0
