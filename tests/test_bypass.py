"""rco metrics bypass (card 1317): code commits with no pack run before them."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from rco import bypass, cli, fixture
from rco.output import Exit, UsageError

T0 = 1767225600  # FIXED_DATE, 2026-01-01T00:00:00Z


def _when(t: int) -> str:
    return f"{t} +0000"


def _ts(t: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _ev(run: str, t: int, head: str | None, cmd: str = "pack", exit_code: int = 0) -> dict:
    return {"v": 1, "run": run, "ts": _ts(t), "cmd": cmd, "exit": exit_code, "store": None,
            "head": head, "ms": 5, "for": ["pkg/core.py"]}


def _events(path: Path, rows: list[dict], extra: tuple[str, ...] = ()) -> Path:
    lines = [json.dumps(r, sort_keys=True) for r in rows] + list(extra)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _code(n: int) -> dict:
    return {"pkg/core.py": f'"""Core."""\nLIMIT = {n}\n'}


def _history(tmp_path: Path):
    """A at t0 (base); B code t0+60 s; C code t0+2 h; D docs only t0+2 h."""
    repo, a = fixture.make_repo(tmp_path / "repo", fixture.DRIFT_FILES)
    b = fixture.commit(repo, _code(1), "B code", _when(T0 + 60))
    c = fixture.commit(repo, _code(2), "C code", _when(T0 + 7200))
    d = fixture.commit(repo, {"README.md": "# docs\n"}, "D docs", _when(T0 + 7200))
    return repo, a, b, c, d


def _run(repo, a, events, *extra, capsys=None):
    argv = ["metrics", "bypass", "--repo", str(repo), "--base", a, "--events", str(events),
            "--window-min", "30", "--format", "json", *extra]
    return cli.main(argv)


def _report(repo, a, events, *extra) -> dict:
    out = repo.parent / "out.json"
    code = _run(repo, a, events, "--out", str(out), *extra)
    assert code in (Exit.OK, Exit.FINDINGS)
    return json.loads(out.read_text())


def test_reports_exactly_the_unpacked_commit(tmp_path):
    repo, a, b, c, d = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)])
    events, _ = bypass.read_events(ev)
    rep = bypass.bypass(repo, events, bypass.code_commits(repo, a, 14), 1800)
    assert [(x.sha, x.covered, x.run) for x in rep.commits] == [(b, True, "run-a"), (c, False, None)]
    assert [x.sha for x in rep.commits if not x.covered] == [c]
    assert d not in {x.sha for x in rep.commits}
    obj = _report(repo, a, ev)
    assert obj["schema"] == "rco.bypass/1" and obj["window_s"] == 1800
    assert (obj["events"], obj["bad_lines"], obj["covered"], obj["uncovered"]) == (1, 0, 1, 1)
    assert [(x["sha"], x["covered"], x["run"]) for x in obj["commits"]] == [
        (b, True, "run-a"), (c, False, None)]


def test_pack_must_exit_0_or_1(tmp_path):
    repo, a, b, c, _ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("bad", T0 + 10, a, exit_code=2),
                                        _ev("other", T0 + 10, a, cmd="tree")])
    assert _report(repo, a, ev)["uncovered"] == 2
    ev = _events(tmp_path / "e.jsonl", [_ev("stale", T0 + 10, a, exit_code=1)])
    assert _report(repo, a, ev)["covered"] == 1


def test_pack_on_other_branch_does_not_cover(tmp_path):
    repo, a, b, c, _ = _history(tmp_path)
    fixture.git(repo, "checkout", "-q", "-b", "side", a)
    side = fixture.commit(repo, {"side.py": "X = 1\n"}, "side", _when(T0 + 20))
    fixture.git(repo, "checkout", "-q", "main")
    ev = _events(tmp_path / "e.jsonl", [_ev("side-run", T0 + 30, side)])
    obj = _report(repo, a, ev)
    assert (obj["covered"], obj["uncovered"]) == (0, 2)


def test_old_pack_outside_window_does_not_cover(tmp_path):
    repo, a, b, c, _ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("old", T0 + 10, a)])
    obj = _report(repo, a, ev)  # window 30 min: B is 50 s after, C is 2 h after
    assert [x["covered"] for x in obj["commits"]] == [True, False]
    # an event after the commit never covers it
    ev = _events(tmp_path / "e.jsonl", [_ev("late", T0 + 120, a)])
    assert [x["covered"] for x in _report(repo, a, ev)["commits"]] == [False, False]


def test_missing_events_exit_2(tmp_path, capsys):
    repo, a, *_ = _history(tmp_path)
    with pytest.raises(UsageError, match="no events file; events are per machine"):
        bypass.read_events(tmp_path / "nope.jsonl")
    assert _run(repo, a, tmp_path / "nope.jsonl") == Exit.USAGE
    assert "events are per machine, collect them first" in capsys.readouterr().err
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert _run(repo, a, empty) == Exit.USAGE
    assert _run(repo, a, empty, "--strict") == Exit.USAGE


def test_bad_lines_counted_not_fatal(tmp_path):
    repo, a, b, c, _ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)],
                 extra=("{not json", "[1, 2]", '{"cmd": "pack"}', ""))
    events, bad = bypass.read_events(ev)
    assert len(events) == 1 and bad == 3
    obj = _report(repo, a, ev)
    assert (obj["events"], obj["bad_lines"], obj["covered"]) == (1, 3, 1)


def test_strict_exit_1_default_exit_0(tmp_path):
    repo, a, b, c, _ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)])
    assert _run(repo, a, ev) == Exit.OK
    assert _run(repo, a, ev, "--strict") == Exit.FINDINGS
    ev = _events(tmp_path / "e2.jsonl", [_ev("r1", T0 + 10, a), _ev("r2", T0 + 7190, b)])
    assert _run(repo, a, ev, "--strict") == Exit.OK  # nothing uncovered


def test_json_byte_identical_twice(tmp_path):
    repo, a, *_ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)])
    outs = []
    for i in (1, 2):
        out = tmp_path / f"o{i}.json"
        _run(repo, a, ev, "--out", str(out))
        outs.append(out.read_bytes())
    assert outs[0] == outs[1] and outs[0].endswith(b"\n")


def test_markdown_names_uncovered_and_says_what_it_does_not_check(tmp_path, capsys):
    repo, a, b, c, _ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)])
    assert cli.main(["metrics", "bypass", "--repo", str(repo), "--base", a, "--events", str(ev),
                     "--window-min", "30"]) == Exit.OK
    text = capsys.readouterr().out
    assert c[:7] in text and b[:7] not in text
    assert "pack ran before the commit; reading it is not checked" in text


def test_root_commit_is_uncovered_and_default_events_path(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "repo", fixture.DRIFT_FILES)
    (repo / ".rco").mkdir()
    _events(repo / ".rco" / "events.jsonl", [_ev("run-x", T0 + 10, a)])
    out = tmp_path / "o.json"
    assert cli.main(["metrics", "bypass", "--repo", str(repo), "--days", "36500",
                     "--format", "json", "--out", str(out)]) == Exit.OK
    obj = json.loads(out.read_text())
    assert [(x["sha"], x["covered"]) for x in obj["commits"]] == [(a, False)]


def test_bad_numbers_are_usage_errors(tmp_path):
    repo, a, *_ = _history(tmp_path)
    ev = _events(tmp_path / "e.jsonl", [_ev("run-a", T0 + 10, a)])
    assert _run(repo, a, ev, "--days", "0") == Exit.USAGE
    assert cli.main(["metrics", "bypass", "--repo", str(repo), "--events", str(ev),
                     "--window-min", "-1"]) == Exit.USAGE
