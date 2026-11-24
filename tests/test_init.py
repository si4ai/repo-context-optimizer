"""rco init: config, CI workflow and post-merge hook, never overwriting."""
import json
import os
import shutil
import subprocess

import pytest

from rco import cli, config, fixture, modules
from rco.init import HOOK_MARK, WORKFLOW, init, workflow_text
from rco.output import Exit


def _repo(tmp_path):
    return fixture.make_repo(tmp_path / "r", {"pkg/a.py": "def f():\n    pass\n"})


def test_init_writes_once_and_keeps_existing(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert cli.main(["init", "--repo", str(repo), "--format", "json"]) == Exit.OK
    steps = json.loads(capsys.readouterr().out)["steps"]
    assert [(s["path"], s["action"]) for s in steps] == [("rco.toml", "wrote"), (WORKFLOW, "wrote")]
    first = (repo / WORKFLOW).read_bytes()
    assert config.load(repo) == config.Config(top=config.worktree_top(repo))  # all commented
    assert modules.layout_from({"rco.toml": (repo / "rco.toml").read_bytes()}).notes == ()
    (repo / "rco.toml").write_text('backend = "sqlite"\n')
    rep = init(repo)
    assert [s.action for s in rep.steps] == ["kept", "kept"]
    assert (repo / "rco.toml").read_text() == 'backend = "sqlite"\n'
    assert (repo / WORKFLOW).read_bytes() == first


def test_workflow_is_stable_and_names_the_branch():
    a = workflow_text("trunk")
    assert a == workflow_text("trunk")
    assert "      - trunk\n" in a and "rco-trunk-" in a
    assert "${{ github.sha }}" in a and "{{{" not in a
    for line in a.splitlines():
        if "uses:" in line:
            assert "@" in line and len(line.split("@")[1].split()[0]) == 40  # pinned to a SHA


def test_hook_install_keep_and_remove_only_touches_ours(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert cli.main(["init", "--repo", str(repo), "--no-config", "--no-ci", "--hooks"]) == Exit.OK
    hook = repo / ".git" / "hooks" / "post-merge"
    assert HOOK_MARK in hook.read_text() and os.access(hook, os.X_OK)
    assert [s.action for s in init(repo, False, False, hooks=True).steps] == ["kept"]
    assert [s.action for s in init(repo, False, False, remove_hooks=True).steps] == ["removed"]
    assert not hook.exists()
    hook.write_text("#!/bin/sh\necho mine\n")
    rep = init(repo, False, False, hooks=True)
    assert rep.steps[0].action == "kept" and "another" in rep.steps[0].detail
    assert init(repo, False, False, remove_hooks=True).steps[0].action == "kept"
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    assert cli.main(["init", "--repo", str(repo), "--hooks", "--remove-hooks"]) == Exit.USAGE


@pytest.mark.skipif(shutil.which("sh") is None, reason="no sh to run the hook")
def test_hook_exits_0_without_rco_on_path(tmp_path):
    repo, sha = _repo(tmp_path)
    init(repo, False, False, hooks=True)
    empty = tmp_path / "empty-path"
    empty.mkdir()  # PATH with no rco on it, on any OS
    p = subprocess.run([shutil.which("sh"), str(repo / ".git" / "hooks" / "post-merge")], cwd=repo,
                       env={**os.environ, "PATH": str(empty)}, capture_output=True)
    assert p.returncode == 0


def test_hook_honours_core_hooks_path(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    fixture.git(repo, "config", "core.hooksPath", ".githooks")
    rep = init(repo, False, False, hooks=True)
    assert rep.steps[0].path == ".githooks/post-merge" and (repo / ".githooks/post-merge").exists()


def test_workflow_runs_the_shipped_notice_with_read_access_to_prs():
    a = workflow_text("main")
    assert "  pull-requests: read\n" in a
    assert "python -m rco.notice_report || code=$?" in a
    for var in ("GH_TOKEN", "EVENT_NAME", "PR_NUMBER", "PR_BASE", "PR_BASE_REF", "PR_HEAD"):
        assert f"          {var}: ${{{{ " in a, var
    assert "ci/notice_report.sh" not in a and "ci/notice_heads.py" not in a


def test_default_workflow_does_not_upload_metrics():
    a = workflow_text("main")
    assert a == workflow_text("main", metrics=False)
    assert "rco-metrics.json" not in a
    assert "upload-artifact" not in a
    assert "rco metrics" not in a


def test_metrics_workflow_writes_and_uploads_the_artifact():
    a = workflow_text("main", metrics=True)
    assert "rco pack" in a
    assert "rco metrics measure" in a
    assert "rco metrics check" in a
    assert "--out rco-report/rco-metrics.json --format json" in a
    assert "name: rco-report" in a
    assert "path: rco-report/" in a
    assert "if-no-files-found: error" in a
    for line in a.splitlines():
        if "uses:" in line:
            sha = line.split("@")[1].split()[0]
            assert len(sha) == 40, line
    # default contract steps are still present
    assert "python -m rco.notice_report || code=$?" in a
    assert "rco update --name" in a


def test_init_metrics_flag_writes_the_opt_in_workflow(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert cli.main(["init", "--repo", str(repo), "--no-config", "--metrics",
                     "--format", "json"]) == Exit.OK
    steps = json.loads(capsys.readouterr().out)["steps"]
    assert steps[0]["path"] == WORKFLOW and steps[0]["action"] == "wrote"
    text = (repo / WORKFLOW).read_text()
    assert "rco-report/rco-metrics.json" in text
    assert workflow_text("main", metrics=True) == text or "rco-metrics.json" in text
    # a second init keeps the file
    assert [s.action for s in init(repo, False, True, metrics=True).steps] == ["kept"]
    assert cli.main(["init", "--repo", str(repo), "--no-config", "--no-ci",
                     "--metrics"]) == Exit.USAGE
