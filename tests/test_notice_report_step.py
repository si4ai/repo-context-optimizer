"""ci/notice_report.sh: a dead lister must fail the job, not look like a quiet repo."""

import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "notice_report.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"
QUIET = "no other open PRs: no cross-PR contract notice."


def test_workflow_runs_the_script_not_an_inline_copy():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "bash ci/notice_report.sh" in text
    assert "gh api --paginate" not in text


def test_notice_report_sh_change_selects_pytest():
    sys.path.insert(0, str(ROOT))
    from ci.test_select import load_map, select_jobs

    rules, jobs = load_map(ROOT / "ci" / "test-select.toml")
    assert "pytest" in jobs
    assert select_jobs(["ci/notice_report.sh"], rules) == {"pytest"}


def _write_exec(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


def _run(tmp_path: Path, gh_script: str, extra_env=None):
    work = tmp_path / "work"
    work.mkdir()
    (work / "ci").mkdir()
    (work / "ci" / "notice_heads.py").symlink_to(ROOT / "ci" / "notice_heads.py")
    summary = work / "summary.md"
    summary.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "gh", gh_script)
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_STEP_SUMMARY": str(summary),
            "EVENT_NAME": "pull_request",
            "PR_NUMBER": "35",
            "PR_BASE": "a" * 40,
            "PR_HEAD": "b" * 40,
            "PUSH_BEFORE": "",
            "PUSH_SHA": "",
        }
    )
    if extra_env:
        env.update(extra_env)
    proc = subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=work,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc, summary.read_text(encoding="utf-8")


def test_gh_api_failure_fails_the_job_not_a_quiet_repo(tmp_path):
    proc, summary = _run(
        tmp_path,
        "#!/bin/sh\necho 'gh: HTTP 502' >&2\nexit 1\n",
    )
    combined = proc.stdout + proc.stderr + summary
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    assert QUIET not in combined


def test_missing_head_sha_fails_the_job_not_a_quiet_repo(tmp_path):
    proc, summary = _run(
        tmp_path,
        '#!/bin/sh\nprintf \'%s\\n\' \'[{"number":9,"state":"open","head":{"ref":"x"}}]\'\nexit 0\n',
    )
    combined = proc.stdout + proc.stderr + summary
    assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
    assert QUIET not in combined
    assert "has no head sha" in proc.stderr


def test_no_other_open_prs_is_a_quiet_pass(tmp_path):
    proc, summary = _run(
        tmp_path,
        "#!/bin/sh\nprintf '%s\\n' '[]'\nexit 0\n",
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert QUIET in proc.stdout
    assert QUIET in summary


def test_no_usable_base_is_a_quiet_pass(tmp_path):
    proc, summary = _run(
        tmp_path,
        "#!/bin/sh\necho 'gh should not run' >&2\nexit 1\n",
        extra_env={"EVENT_NAME": "push", "PUSH_BEFORE": "0" * 40, "PUSH_SHA": "c" * 40},
    )
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "no usable base for this event: no cross-PR contract notice." in proc.stdout
    assert "no usable base for this event: no cross-PR contract notice." in summary
    assert "gh should not run" not in proc.stderr
