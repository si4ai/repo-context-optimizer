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
    # an overrun or pack findings must not fail the job
    assert "xargs -0 rco pack" not in a
    assert '"$code" -gt 1' in a
    # the warning is emitted by rco metrics measure, not inline python
    assert "python3 -c" not in a
    assert "rco metrics measure" in a
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


def _yaml_module():
    """PyYAML, installing the pinned wheel if this environment lacks it.

    The pytest CI job only installs pytest, and ci.yml is a watched
    file, so the test installs pyyaml==6.0.3 when import fails.
    """
    try:
        import yaml
        return yaml
    except ImportError:
        import sys
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "pyyaml==6.0.3"],
            stdout=subprocess.DEVNULL,
        )
        import yaml
        return yaml


def test_init_workflows_parse_as_yaml():
    """both default and --metrics rco.yml workflows parse as YAML."""
    yaml = _yaml_module()

    lint = shutil.which("actionlint")
    for metrics in (False, True):
        text = workflow_text("main", metrics=metrics)
        data = yaml.safe_load(text)
        assert data["name"] == "rco"
        steps = data["jobs"]["rco"]["steps"]
        runs = [s.get("run", "") for s in steps if isinstance(s, dict)]
        assert any("rco diff" in r and "rco affected" in r for r in runs)
        if metrics:
            assert any("rco pack" in r for r in runs)
            assert any("rco metrics measure" in r for r in runs)
            assert any("rco metrics check" in r for r in runs)
            assert all("python3 -c" not in r for r in runs)
        else:
            assert all("rco metrics" not in r for r in runs)
            assert all("rco pack" not in r for r in runs)
        if lint:
            proc = subprocess.run(
                [lint, "-"], input=text, text=True, capture_output=True)
            err = (proc.stderr or "") + (proc.stdout or "")
            assert proc.returncode == 0, err


# ---- rco init --agents (card 1316) --------------------------------------------

from rco.init import (AGENT_BEGIN, AGENT_BLOCK, AGENT_END, CURSOR_ENV, agent_block,  # noqa: E402
                      cursor_install_line)


def _agents(repo, capsys=None):
    rep = init(repo, config=False, ci=False, agents=True)
    return {s.path: (s.action, s.detail) for s in rep.steps}


def _strip_block(data: bytes, nl: bytes) -> bytes:
    """The file with rco's block and its one separating blank line removed."""
    begin = data.index(AGENT_BEGIN.encode())
    end = data.index(AGENT_END.encode(), begin) + len(AGENT_END) + len(nl)
    if data[end:end + len(nl)] == nl:
        end += len(nl)
    return data[:begin] + data[end:]


def test_agents_creates_agents_md_only(tmp_path):
    repo, _ = _repo(tmp_path)
    got = _agents(repo)
    assert got["AGENTS.md"] == ("wrote", "")
    assert (repo / "AGENTS.md").read_text() == agent_block(AGENT_BLOCK)
    for rel in ("CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md"):
        assert got[rel] == ("absent", "") and not (repo / rel).exists()
    assert ".aider.conf.yml" not in got and CURSOR_ENV not in got
    assert cli.main(["init", "--repo", str(repo), "--no-config", "--no-ci", "--agents"]) == Exit.OK


def test_agents_twice_one_block(tmp_path):
    repo, _ = _repo(tmp_path)
    (repo / "GEMINI.md").write_text("# notes\n")
    _agents(repo)
    first = {p: (repo / p).read_bytes() for p in ("AGENTS.md", "GEMINI.md")}
    again = _agents(repo)
    assert {a for a, _ in again.values() if a != "absent"} == {"kept"}
    assert again["AGENTS.md"] == ("kept", "current")
    for p, data in first.items():
        assert (repo / p).read_bytes() == data
        assert data.count(AGENT_BEGIN.encode()) == 1


def test_block_is_brought_up_to_date(tmp_path):
    repo, _ = _repo(tmp_path)
    old = f"{AGENT_BEGIN}\nold text\n{AGENT_END}\n\nmine\n"
    (repo / "AGENTS.md").write_text(old)
    assert _agents(repo)["AGENTS.md"] == ("updated", "")
    assert (repo / "AGENTS.md").read_text() == agent_block(AGENT_BLOCK) + "\nmine\n"


@pytest.mark.parametrize("nl", ["\n", "\r\n"])
def test_user_text_unchanged(tmp_path, nl):
    repo, _ = _repo(tmp_path)
    user = {"AGENTS.md": f"# rules{nl}{nl}Use tabs.{nl}",
            "CLAUDE.md": f"Be brief.{nl}",
            ".github/copilot-instructions.md": f"Keep diffs small.{nl}"}
    for rel, text in user.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_bytes(text.encode())
    got = _agents(repo)
    for rel, text in user.items():
        assert got[rel] == ("added", ""), rel
        data = (repo / rel).read_bytes()
        assert data.startswith(AGENT_BEGIN.encode())
        assert _strip_block(data, nl.encode()) == text.encode(), rel
        if nl == "\r\n":
            assert b"\n" not in data.replace(b"\r\n", b"")  # no bare LF added
    claude = (repo / "CLAUDE.md").read_bytes().decode()
    assert claude.startswith(f"{AGENT_BEGIN}{nl}@AGENTS.md{nl}{AGENT_END}{nl}")


def test_unterminated_block_untouched(tmp_path):
    repo, _ = _repo(tmp_path)
    text = f"intro\n{AGENT_BEGIN}\nhalf a block\n"
    (repo / "AGENTS.md").write_text(text)
    assert _agents(repo)["AGENTS.md"] == ("kept", "unterminated rco block")
    assert (repo / "AGENTS.md").read_text() == text


def test_frontmatter_stays_first(tmp_path):
    repo, _ = _repo(tmp_path)
    text = "---\ntitle: rules\n---\n# rules\n"
    (repo / "AGENTS.md").write_text(text)
    _agents(repo)
    data = (repo / "AGENTS.md").read_text()
    assert data.startswith("---\ntitle: rules\n---\n" + AGENT_BEGIN)
    assert _strip_block(data.encode(), b"\n").decode() == text


def test_aider_and_cursor_cases(tmp_path):
    repo, _ = _repo(tmp_path)
    (repo / ".aider.conf.yml").write_text("model: x\n")
    got = _agents(repo)
    assert got[".aider.conf.yml"] == ("kept", "add `read: [AGENTS.md]` by hand")
    assert (repo / ".aider.conf.yml").read_text() == "model: x\n"
    assert CURSOR_ENV not in got and not (repo / ".cursor").exists()
    (repo / ".aider.conf.yml").write_text("read: [AGENTS.md]\n")
    (repo / ".cursor").mkdir()
    got = _agents(repo)
    assert ".aider.conf.yml" not in got
    assert got[CURSOR_ENV] == ("wrote", "")
    env = json.loads((repo / CURSOR_ENV).read_text())
    from rco import __version__
    assert env == {"install": f"pip install repo-context-optimizer=={__version__} && rco update"}
    got = _agents(repo)
    assert got[CURSOR_ENV] == ("kept", f"exists; install line: {cursor_install_line()}")


def test_over_cap_warns(tmp_path):
    repo, _ = _repo(tmp_path)
    (repo / "AGENTS.md").write_text("x" * 20_000 + "\n")
    action, detail = _agents(repo)["AGENTS.md"]
    n = (repo / "AGENTS.md").stat().st_size
    assert action == "added"
    assert detail == f"warning: {n} bytes; devin injects the first 16 KiB, codex caps at 32 KiB"
    assert (repo / "AGENTS.md").read_text().startswith(AGENT_BEGIN)


def test_no_agents_flag_no_agent_steps(tmp_path):
    repo, _ = _repo(tmp_path)
    assert {s.path for s in init(repo).steps} == {"rco.toml", WORKFLOW}
    assert not (repo / "AGENTS.md").exists()
