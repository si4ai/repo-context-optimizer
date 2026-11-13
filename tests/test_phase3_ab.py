"""phase 3 prep: the cold-agent A/B harness prepares briefs and scores kit logs."""
import json
import sys
from pathlib import Path

import pytest

from rco import fixture
from rco.__init__ import __version__ as RCO_VERSION

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import phase3_ab as ab  # noqa: E402

HERE = Path(__file__).parent
FOREST = HERE / "fixtures" / "forest_basic"


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _repo(tmp_path, case="forest_basic"):
    return fixture.make_repo(tmp_path / case, _files(HERE / "fixtures" / case))


CONTROL_LOG = """\
| command | exit | stdout_bytes | PT |
|---|---|---|---|
| cat README.md | 0 | 2000 | 10:00:00 |
| find src -name '*.py' | 0 | 800 | 10:00:05 |
| sed -n '1,40p' src/app/cli.py | 0 | 400 | 10:00:10 |
| apply_patch src/app/cli.py | 0 | 20 | 10:00:20 |
SUMMARY turn=task commands=4 wrong_guesses=1 retries=1 start=10:00:00 end=10:00:30 stdout_bytes=3220 task_done=y
TOKENS source=proxy-bytes before_first_useful=9449
"""

TREATMENT_LOG = """\
| command | exit | stdout_bytes | PT |
|---|---|---|---|
| cat brief.md | 0 | 500 | 10:00:00 |
| apply_patch src/app/cli.py | 0 | 20 | 10:00:08 |
SUMMARY turn=task commands=2 wrong_guesses=0 retries=0 start=10:00:00 end=10:00:10 stdout_bytes=520 task_done=y
TOKENS source=proxy-bytes before_first_useful=1732
"""

PLATFORM_CONTROL = CONTROL_LOG.replace(
    "TOKENS source=proxy-bytes before_first_useful=9449",
    "TOKENS source=platform before_first_useful=3000",
)
PLATFORM_TREATMENT = TREATMENT_LOG.replace(
    "TOKENS source=proxy-bytes before_first_useful=1732",
    "TOKENS source=platform before_first_useful=1200",
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_prepare_writes_control_and_treatment_briefs(tmp_path):
    repo, sha = _repo(tmp_path)
    out = tmp_path / "ab"
    task = _write(tmp_path / "task.md", "Add a --quiet flag to the CLI.\n")
    assert ab.main([
        "prepare", "--repo", str(repo), "--sha", sha, "--for", "src/app/cli.py",
        "--budget", "8000", "--task-file", str(task), "--out", str(out),
    ]) == 0
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["schema"] == "rco.phase3_ab/1"
    assert manifest["cmd"] == "prepare"
    assert manifest["sha"] == sha
    assert manifest["budget"] == 8000
    assert manifest["used"] <= 8000
    assert manifest["for"] == ["src/app/cli.py"]
    assert manifest["rco_version"] == RCO_VERSION
    assert manifest["arms"] == ["control", "treatment"]
    control = (out / "control" / "brief.md").read_text()
    treatment = (out / "treatment" / "brief.md").read_text()
    pack_md = (out / "treatment" / "pack.md").read_text()
    pack_obj = json.loads((out / "treatment" / "pack.json").read_text())
    assert "Add a --quiet flag to the CLI." in control
    assert "Add a --quiet flag to the CLI." in treatment
    assert "# rco pack" in pack_md and pack_md in treatment
    assert "# rco pack" not in control
    assert pack_obj["schema"] == "rco.pack/1" and pack_obj["sha"] == sha
    assert not (repo / ".rco").exists()


def test_prepare_is_byte_identical_across_runs(tmp_path):
    repo, sha = _repo(tmp_path)
    task = _write(tmp_path / "task.md", "Add a --quiet flag to the CLI.\n")
    outs = []
    for name in ("a", "b"):
        out = tmp_path / name
        assert ab.main([
            "prepare", "--repo", str(repo), "--sha", sha, "--for", "src/app/cli.py",
            "--task-file", str(task), "--out", str(out),
        ]) == 0
        outs.append({
            "manifest": (out / "manifest.json").read_bytes(),
            "control": (out / "control" / "brief.md").read_bytes(),
            "treatment": (out / "treatment" / "brief.md").read_bytes(),
            "pack": (out / "treatment" / "pack.md").read_bytes(),
        })
    assert outs[0] == outs[1]


def test_prepare_usage_error_is_exit_2(tmp_path, capsys):
    assert ab.main(["prepare", "--out", str(tmp_path / "x")]) == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower() or "--repo" in err


def test_score_compares_arms_and_blocks_proxy_token_claims(tmp_path, capsys):
    control = _write(tmp_path / "control.log", CONTROL_LOG)
    treatment = _write(tmp_path / "treatment.log", TREATMENT_LOG)
    out = tmp_path / "score.json"
    assert ab.main([
        "score", "--control", str(control), "--treatment", str(treatment),
        "--first-useful", r"apply_patch", "--out", str(out),
    ]) == 0
    report = json.loads(out.read_text())
    assert report["schema"] == "rco.phase3_ab/1" and report["cmd"] == "score"
    assert report["first_useful_pattern"] == "apply_patch"
    assert report["control"]["n"] == 1 and report["treatment"]["n"] == 1
    c, t = report["control"]["median"], report["treatment"]["median"]
    assert (c["commands"], c["wrong_guesses"], c["retries"]) == (4, 1, 1)
    assert (t["commands"], t["wrong_guesses"], t["retries"]) == (2, 0, 0)
    assert c["reads_before_first_useful"] == 3
    assert t["reads_before_first_useful"] == 1
    assert c["stdout_bytes_before_first_useful"] == 3200
    assert t["stdout_bytes_before_first_useful"] == 500
    assert c["tokens_source"] == "proxy-bytes" and t["tokens_source"] == "proxy-bytes"
    assert report["delta"]["commands"] == -2
    assert report["delta"]["wrong_guesses"] == -1
    assert report["delta"]["reads_before_first_useful"] == -2
    assert report["savings_claimable"] is False
    assert any("platform" in b or "tokenizer" in b for b in report["claim_blockers"])
    # stdout is the same report
    assert json.loads(capsys.readouterr().out)["delta"]["commands"] == -2


def test_score_claimable_only_with_platform_tokens(tmp_path):
    control = _write(tmp_path / "c.log", PLATFORM_CONTROL)
    treatment = _write(tmp_path / "t.log", PLATFORM_TREATMENT)
    out = tmp_path / "score.json"
    assert ab.main([
        "score", "--control", str(control), "--treatment", str(treatment),
        "--first-useful", r"apply_patch", "--out", str(out),
    ]) == 0
    report = json.loads(out.read_text())
    assert report["savings_claimable"] is True
    assert report["claim_blockers"] == []
    assert report["delta"]["tokens_before_first_useful"] == -1800
    assert report["control"]["median"]["tokens_source"] == "platform"


def test_score_first_useful_override_ignores_mill_join(tmp_path):
    log = (
        "| command | exit | stdout_bytes | PT |\n"
        "|---|---|---|---|\n"
        "| mill join --link-file invite.txt | 0 | 700 | 10:00:00 |\n"
        "| cat README.md | 0 | 200 | 10:00:05 |\n"
        "| apply_patch src/app/cli.py | 0 | 20 | 10:00:09 |\n"
        "SUMMARY turn=task commands=3 wrong_guesses=0 retries=0 start=10:00:00 end=10:00:12\n"
    )
    p = _write(tmp_path / "c.log", log)
    out = tmp_path / "score.json"
    assert ab.main([
        "score", "--control", str(p), "--treatment", str(p),
        "--first-useful", r"apply_patch", "--out", str(out),
    ]) == 0
    report = json.loads(out.read_text())
    assert report["control"]["median"]["reads_before_first_useful"] == 2
    assert report["control"]["median"]["first_useful_action"] == "apply_patch src/app/cli.py"
    assert report["savings_claimable"] is False


def test_score_usage_error_is_exit_2(capsys):
    assert ab.main(["score", "--control", "missing.log"]) == 2
    err = capsys.readouterr().err
    assert "usage" in err.lower() or "--treatment" in err
