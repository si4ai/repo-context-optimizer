"""--help text and stderr progress labels for 0.1.3 wording fixes."""
from rco import cli, fixture
from rco.output import Exit


def test_help_names_positionals_and_requires_branches(capsys):
    assert cli.main(["pack", "--help"]) == 0
    pack = capsys.readouterr().out
    assert "commit (default: HEAD)" in pack
    assert "paths whose owning modules get full Trees" in pack

    assert cli.main(["tree", "--help"]) == 0
    assert "commit (default: HEAD)" in capsys.readouterr().out

    assert cli.main(["forest", "--help"]) == 0
    assert "commit (default: HEAD)" in capsys.readouterr().out

    assert cli.main(["diff", "--help"]) == 0
    diff = capsys.readouterr().out
    assert "base commit" in diff and "head commit" in diff

    assert cli.main(["plan", "--help"]) == 0
    plan = capsys.readouterr().out
    # two BRANCH positionals, so the first is required
    assert "BRANCH BRANCH" in plan
    assert "usage: rco plan" in plan
    # not the old all-optional form as the only branch marker
    assert "[BRANCH ...]" not in plan.split("options:")[0] or "BRANCH BRANCH" in plan

    assert cli.main(["triage", "--help"]) == 0
    tri = capsys.readouterr().out
    usage = tri.split("options:")[0]
    assert "BRANCH" in usage and "[BRANCH ...]" not in usage.replace("BRANCH [BRANCH ...]", "")
    # nargs=+ shows BRANCH [BRANCH ...] (first required), not [BRANCH ...] alone
    assert "BRANCH [BRANCH ...]" in usage or "BRANCH BRANCH" in usage

    assert cli.main(["notice", "--help"]) == 0
    note = capsys.readouterr().out
    usage = note.split("options:")[0]
    assert "BRANCH [BRANCH ...]" in usage or "BRANCH BRANCH" in usage
    assert usage.count("[BRANCH ...]") == 0 or "BRANCH [BRANCH ...]" in usage


def test_plan_triage_notice_progress_uses_the_command_name(tmp_path, capsys):
    repo, base = fixture.make_repo(tmp_path / "r", {
        "pkg/__init__.py": "",
        "pkg/a.py": "def run() -> int:\n    return 1\n",
        "pkg/b.py": "from pkg.a import run\n\n\ndef main() -> int:\n    return run()\n",
    })
    fixture.git(repo, "switch", "-q", "-c", "other")
    fixture.commit(repo, {"pkg/b.py": "from pkg.a import run\n\n\ndef main() -> int:\n    return run() + 1\n"})
    fixture.git(repo, "switch", "-q", "main")
    fixture.commit(repo, {"pkg/a.py": "def run(x: int = 0) -> int:\n    return x\n"})

    assert cli.main(["plan", "main", "other", "--repo", str(repo)]) in (0, 1)
    err = capsys.readouterr().err
    assert "rco plan:" in err
    assert "rco diff:" not in err

    assert cli.main(["triage", "other", "--onto", "main", "--repo", str(repo)]) in (0, 1)
    err = capsys.readouterr().err
    assert "rco triage:" in err
    assert "rco diff:" not in err

    assert cli.main(["notice", "other", "--base", base, "--head", "main", "--repo", str(repo)]) == 0
    err = capsys.readouterr().err
    assert "rco notice:" in err
    assert "rco diff:" not in err
