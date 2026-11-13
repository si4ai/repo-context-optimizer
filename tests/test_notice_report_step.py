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


def _git(cwd: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid",
           "GIT_AUTHOR_DATE": "2026-01-01T00:00:00Z", "GIT_COMMITTER_DATE": "2026-01-01T00:00:00Z"}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True, env=env).stdout.strip()


def _commit(work: Path, rel: str, text: str, msg: str) -> str:
    f = work / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    _git(work, "add", rel)
    _git(work, "commit", "-q", "-m", msg)
    return _git(work, "rev-parse", "HEAD")


def _repo(tmp_path: Path) -> dict:
    """main: fork -> tip (main moved on); pr: fork -> head; other: fork -> other."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-q", "-b", "main")
    fork = _commit(work, "pkg/core.py", "def run(a):\n    return a\n", "fork")
    tip = _commit(work, "pkg/later.py", "def later():\n    return 1\n", "main moved on")
    _git(work, "checkout", "-q", "-b", "pr", fork)
    head = _commit(work, "pkg/core.py", "def run(a, b):\n    return a\n", "pr changes run")
    _git(work, "checkout", "-q", "-b", "other", fork)
    other = _commit(work, "pkg/use.py", "from pkg.core import run\n\nrun(1)\n", "other uses run")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-q", "origin", f"{other}:refs/pull/9/head")
    _git(work, "checkout", "-q", "--detach", head)
    return {"work": work, "fork": fork, "tip": tip, "head": head, "other": other}


def _run(tmp_path: Path, gh_script: str, extra_env=None, repo=None):
    repo = repo or _repo(tmp_path)
    work = repo["work"]
    (work / "ci").mkdir()
    (work / "ci" / "notice_heads.py").symlink_to(ROOT / "ci" / "notice_heads.py")
    (work / "src").symlink_to(ROOT / "src")
    summary = tmp_path / "summary.md"
    summary.write_text("", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _write_exec(bin_dir / "gh", gh_script)
    _write_exec(bin_dir / "python", f"#!/bin/sh\nexec {sys.executable} \"$@\"\n")
    env = os.environ.copy()
    env.update(
        {
            "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
            "GITHUB_REPOSITORY": "example/repo",
            "GITHUB_STEP_SUMMARY": str(summary),
            "EVENT_NAME": "pull_request",
            "PR_NUMBER": "35",
            "PR_BASE": repo["tip"],
            "PR_BASE_REF": "main",
            "PR_HEAD": repo["head"],
            "PUSH_BEFORE": "",
            "PUSH_SHA": "",
            "PUSH_REF_NAME": "",
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


def _pulls_gh(pulls: list) -> str:
    import json
    body = json.dumps(pulls).replace("'", "")
    return f"#!/bin/sh\nprintf '%s\\n' '{body}'\nexit 0\n"


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
        '#!/bin/sh\nprintf \'%s\\n\' \'[{"number":9,"state":"open","base":{"ref":"main"},"head":{"ref":"x"}}]\'\nexit 0\n',
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


def test_notice_base_is_the_merge_base_not_the_base_tip(tmp_path):
    repo = _repo(tmp_path)
    proc, _ = _run(tmp_path, _pulls_gh([]), repo=repo)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert f"notice base: {repo['fork']}" in proc.stdout
    assert repo["tip"] not in proc.stdout


def test_notice_runs_on_merge_base_and_lists_only_same_base_prs(tmp_path):
    repo = _repo(tmp_path)
    pulls = [
        {"number": 9, "state": "open", "base": {"ref": "main"},
         "head": {"sha": repo["other"], "ref": "other"}},
        {"number": 12, "state": "open", "base": {"ref": "release/9"},
         "head": {"sha": "e" * 40, "ref": "elsewhere"}},
    ]
    proc, summary = _run(tmp_path, _pulls_gh(pulls), repo=repo)
    assert proc.returncode == 0, (proc.returncode, proc.stdout, proc.stderr)
    assert "## pr-9" in summary
    assert "pr-12" not in summary
    # with the base tip as BASE, pkg/later.py would show as a change of this PR
    assert "later" not in summary
    assert "pkg/use.py" in summary


def test_workflow_passes_the_base_ref_and_pushed_ref():
    text = WORKFLOW.read_text(encoding="utf-8")
    assert "PR_BASE_REF: ${{ github.event.pull_request.base.ref }}" in text
    assert "PUSH_REF_NAME: ${{ github.ref_name }}" in text
