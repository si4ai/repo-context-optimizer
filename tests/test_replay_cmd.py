"""Card 1319: `rco replay`, a labelled before/after estimate on the repo's own commits.

(tests/test_replay.py is about update replays; this file is the replay command.)
"""
import json
import subprocess
import sys
import types

import pytest

from rco import cli, fixture, metrics
from rco.output import Exit, UsageError
from rco.replay import HEADING_METHOD, INSTALL_HINT, pack_text_at, replay

CORE_A = "def run(x: int) -> int:\n    return x\n"
CORE_B = "from pkg.util import helper\n\n\ndef run(x: int) -> int:\n    return helper(x)\n"
# big enough that the 1341 budget (half its tokens) can outline it: B gets a
# real pack; D's import (pkg/core.py) is too small, so D's pack is skipped
UTIL = "def helper(x: int) -> int:\n    return x * 2\n\n\n" + "".join(
    f"def h{i}(x: int) -> int:\n    y = x * {i} + 1\n    z = y - x // 3\n"
    f"    total = sum(range(z % 7))\n    extra = [k * total for k in range(y % 5)]\n"
    f"    if extra and total > 3:\n        total += max(extra) - min(extra)\n"
    f"    return total + y + z\n\n" for i in range(12))
APP_A = "from pkg.core import run\n\nprint(run(1))\n"
APP_D = "from pkg.core import run\n\nprint(run(2))\n"


def _history(tmp_path):
    """A (root, code), B (core imports util), C (docs only), D (app changes)."""
    repo, a = fixture.make_repo(tmp_path / "r", {"pkg/__init__.py": "", "pkg/core.py": CORE_A,
                                                 "pkg/util.py": UTIL, "app.py": APP_A,
                                                 "README.md": "# r\n"})
    b = fixture.commit(repo, {"pkg/core.py": CORE_B}, "b")
    c = fixture.commit(repo, {"README.md": "# r\n\nmore\n"}, "c")
    d = fixture.commit(repo, {"app.py": APP_D}, "d")
    return repo, [a, b, c, d]


def _est(*texts):
    return len("".join(texts)) // 4


def test_numbers_are_exact(tmp_path):
    repo, (a, b, c, d) = _history(tmp_path)
    rep = replay(repo, 10, 12000, 50, metrics.estimate).to_obj()
    assert [x["sha"] for x in rep["commits"]] == [d, b]  # C is docs only; A is the root
    assert rep["skipped"] == 1 and rep["head"] == d
    pack_b = metrics.estimate(pack_text_at(repo, b, ["pkg/core.py"], 12000))
    assert "nothing to add" in pack_text_at(repo, d, ["app.py"], 12000)  # D: skipped
    want_d = {"sha": d, "changed": 1,
              "without": {"files": 2, "tokens": _est(APP_D) + _est(CORE_B),  # app.py + pkg/core.py
                          "imported_files": 1, "imported_tokens": _est(CORE_B)},
              "with": {"files": 2, "tokens": _est(APP_D) + _est(CORE_B), "pack_tokens": 0,
                       "pack_skipped": True, "changed_tokens": _est(APP_D)},
              "coverage": {"changed_in_pack": 0, "imported_in_pack": 0, "imported": 1}}
    want_b = {"sha": b, "changed": 1,
              "without": {"files": 2, "tokens": _est(CORE_B) + _est(UTIL),  # core.py + util.py
                          "imported_files": 1, "imported_tokens": _est(UTIL)},
              "with": {"files": 2, "tokens": pack_b + _est(CORE_B), "pack_tokens": pack_b,
                       "pack_skipped": False, "changed_tokens": _est(CORE_B)},
              "coverage": {"changed_in_pack": 0, "imported_in_pack": 1, "imported": 1}}
    assert rep["commits"] == [want_d, want_b]
    s = rep["summary"]
    wo = sorted([want_d["without"]["tokens"], want_b["without"]["tokens"]])
    wi = sorted([want_d["with"]["tokens"], want_b["with"]["tokens"]])
    assert s["n"] == 2
    assert s["median_with_tokens"] * 2 == wi[0] + wi[1]
    assert s["median_without_tokens"] * 2 == wo[0] + wo[1]
    assert s["median_without_files"] == 2 and s["median_with_files"] == 2
    assert s["pack_skipped"] == 1
    med_wo, med_wi = s["median_without_tokens"], s["median_with_tokens"]
    assert s["saved_pct"] == round(100 * (med_wo - med_wi) / med_wo, 1)
    assert s["worse_commits"] == sum(1 for c in rep["commits"]
                                     if c["with"]["tokens"] > c["without"]["tokens"])


def test_with_side_counts_changed_files_not_pack_alone(tmp_path):
    """metrics.measure's with side is the pack alone; replay adds the files being edited."""
    repo, (a, b, c, d) = _history(tmp_path)
    for row in replay(repo, 10, 12000, 50, metrics.estimate).to_obj()["commits"]:
        w = row["with"]
        if w["pack_skipped"]:
            assert w["tokens"] == row["without"]["tokens"] and w["pack_tokens"] == 0
            assert w["files"] == row["without"]["files"]
        else:
            assert w["tokens"] == w["pack_tokens"] + w["changed_tokens"] > w["pack_tokens"]
            assert w["files"] == row["changed"] + 1


def test_pack_bigger_than_reads_reported(tmp_path):
    repo, (a, b, c, d) = _history(tmp_path)
    # a pack that costs more than the one small file it replaces
    e = fixture.commit(repo, {"pkg/core.py": CORE_B + "\n\ndef more() -> int:\n    return 1\n",
                              "pkg/util.py": UTIL[:600]}, "e")
    rep = replay(repo, 1, 12000, 50, metrics.estimate)
    o = rep.to_obj()
    row = o["commits"][0]
    assert row["sha"] == e
    worse = row["with"]["tokens"] > row["without"]["tokens"]
    assert o["summary"]["worse_commits"] == int(worse)
    md = rep.to_markdown()
    assert f"in {int(worse)} of 1 commits" in md
    assert rep.exit_code() == Exit.OK


def test_no_saving_wording(tmp_path):
    rep_obj = {"head": "0" * 40, "since": None, "tokenizer_is_estimate": True, "skipped": 0,
               "assumptions": [], "commits": [],
               "summary": {"n": 1, "median_with_tokens": 20, "median_without_tokens": 10,
                           "median_without_files": 1, "median_with_files": 2, "saved_pct": -100.0,
                           "worse_commits": 1, "pack_skipped": 0}}
    from rco.replay import ReplayReport
    md = ReplayReport(rep_obj).to_markdown()
    assert "No saving" in md and "rco does not save tokens on these commits" in md
    assert "in 1 of 1 commits" in md


def _run_json(repo, capsys):
    assert cli.main(["replay", "--repo", str(repo), "--format", "json"]) == Exit.OK
    return json.loads(capsys.readouterr().out)


def test_estimate_label_without_tiktoken(tmp_path, capsys, monkeypatch):
    repo, _ = _history(tmp_path)
    monkeypatch.setitem(sys.modules, "tiktoken", None)  # import fails
    o = _run_json(repo, capsys)
    assert o["tokenizer_is_estimate"] is True and o["tokenizer"] == "chars/4 (estimate)"
    assert any("chars/4 (estimate)" in a and INSTALL_HINT in a for a in o["assumptions"])
    assert cli.main(["replay", "--repo", str(repo)]) == Exit.OK
    assert "chars/4 estimate" in capsys.readouterr().out


def test_estimate_label_absent_with_tiktoken(tmp_path, capsys, monkeypatch):
    repo, _ = _history(tmp_path)
    stub = types.ModuleType("tiktoken")
    enc = types.SimpleNamespace(encode=lambda s, disallowed_special=(): s.split())
    stub.get_encoding = lambda name: enc
    monkeypatch.setitem(sys.modules, "tiktoken", stub)
    o = _run_json(repo, capsys)
    assert o["tokenizer_is_estimate"] is False and o["tokenizer"] == metrics.TOKENIZER
    assert cli.main(["replay", "--repo", str(repo)]) == Exit.OK
    assert "chars/4" not in capsys.readouterr().out


def test_writes_nothing(tmp_path, capsys):
    repo, _ = _history(tmp_path)
    _run_json(repo, capsys)
    assert not (repo / ".rco").exists()
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_skips_counted(tmp_path):
    repo, (a, b, c, d) = _history(tmp_path)
    o = replay(repo, 10, 12000, 50, metrics.estimate).to_obj()
    assert o["skipped"] == 1  # the root commit
    big = fixture.commit(repo, {f"pkg/m{i}.py": f"X = {i}\n" for i in range(4)}, "big")
    o = replay(repo, 10, 12000, 3, metrics.estimate).to_obj()
    assert o["skipped"] == 2 and big not in [x["sha"] for x in o["commits"]]
    o = replay(repo, 1, 12000, 50, metrics.estimate).to_obj()  # --last caps commits considered
    assert [x["sha"] for x in o["commits"]] == [big] and o["skipped"] == 0


def test_no_code_commits_exit_2(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "docs", {"README.md": "# a\n"})
    fixture.commit(repo, {"README.md": "# b\n"}, "docs")
    assert cli.main(["replay", "--repo", str(repo)]) == Exit.USAGE
    with pytest.raises(UsageError):
        replay(repo, 10, 12000, 50, metrics.estimate)
    root_only, _ = fixture.make_repo(tmp_path / "root", {"a.py": "X = 1\n"})
    assert cli.main(["replay", "--repo", str(root_only)]) == Exit.USAGE


def test_json_byte_identical_twice(tmp_path, capsys, monkeypatch):
    repo, _ = _history(tmp_path)
    monkeypatch.setitem(sys.modules, "tiktoken", None)
    outs = []
    for _ in range(2):
        assert cli.main(["replay", "--repo", str(repo), "--format", "json"]) == Exit.OK
        outs.append(capsys.readouterr().out)
    assert outs[0] == outs[1]


def test_assumptions_always_printed(tmp_path):
    repo, _ = _history(tmp_path)
    rep = replay(repo, 10, 12000, 50, metrics.estimate)
    md = rep.to_markdown()
    assert HEADING_METHOD in md
    for a in rep.to_obj()["assumptions"]:
        assert f"- {a}" in md
    assert "not a measurement of an agent" in md and "oracle" in md
    assert "tool calls" not in md.split(HEADING_METHOD)[0]  # never labelled tool calls


def test_cli_subprocess_runs_from_a_clone(tmp_path):
    repo, _ = _history(tmp_path)
    import os
    from pathlib import Path
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src")}
    p = subprocess.run([sys.executable, "-m", "rco", "replay", "--repo", str(repo)],
                       capture_output=True, env=env)
    assert p.returncode == 0, p.stderr
    assert b"# rco replay" in p.stdout and HEADING_METHOD.encode() in p.stdout
