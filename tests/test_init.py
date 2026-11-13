"""rco init: config, CI workflow and post-merge hook, never overwriting."""
import json
import os
import subprocess

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


def test_hook_install_runs_and_remove_only_touches_ours(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert cli.main(["init", "--repo", str(repo), "--no-config", "--no-ci", "--hooks"]) == Exit.OK
    hook = repo / ".git" / "hooks" / "post-merge"
    assert HOOK_MARK in hook.read_text() and os.access(hook, os.X_OK)
    p = subprocess.run(["sh", str(hook)], cwd=repo, env={"PATH": "/usr/bin:/bin"},
                       capture_output=True)
    assert p.returncode == 0  # no rco on PATH: still exit 0
    assert [s.action for s in init(repo, False, False, hooks=True).steps] == ["kept"]
    assert [s.action for s in init(repo, False, False, remove_hooks=True).steps] == ["removed"]
    assert not hook.exists()
    hook.write_text("#!/bin/sh\necho mine\n")
    rep = init(repo, False, False, hooks=True)
    assert rep.steps[0].action == "kept" and "another" in rep.steps[0].detail
    assert init(repo, False, False, remove_hooks=True).steps[0].action == "kept"
    assert hook.read_text() == "#!/bin/sh\necho mine\n"
    assert cli.main(["init", "--repo", str(repo), "--hooks", "--remove-hooks"]) == Exit.USAGE


def test_hook_honours_core_hooks_path(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    fixture.git(repo, "config", "core.hooksPath", ".githooks")
    rep = init(repo, False, False, hooks=True)
    assert rep.steps[0].path == ".githooks/post-merge" and (repo / ".githooks/post-merge").exists()
