"""Behavior of ci/test_select.py, including a diff in a temporary git repo."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ci.test_select import load_map, match_glob, select_jobs  # noqa: E402

MAP = """
[[rule]]
paths = ["verify/spin/*.pml", "verify/spin/run.sh"]
jobs = ["spin"]

[[rule]]
paths = ["src/**/*.py", "tests/**/*.py"]
jobs = ["pytest"]

[[rule]]
paths = ["ci/test_select.py", "ci/test-select.toml"]
jobs = ["pytest"]

[[rule]]
paths = [".github/workflows/ci.yml"]
jobs = ["pytest", "spin"]
"""


def test_star_star_matches_zero_or_more_directories():
    assert match_glob("src/a.py", "src/**/*.py")
    assert match_glob("src/pkg/a.py", "src/**/*.py")
    assert not match_glob("src/a.txt", "src/**/*.py")


def test_single_star_stays_in_one_directory():
    assert match_glob("verify/spin/model.pml", "verify/spin/*.pml")
    assert not match_glob("verify/spin/sub/model.pml", "verify/spin/*.pml")
    assert match_glob("verify/spin/run.sh", "verify/spin/run.sh")


def test_union_of_matching_rules(tmp_path):
    rules, jobs = load_map(_map(tmp_path))
    assert jobs == ["pytest", "spin"]
    chosen = select_jobs([".github/workflows/ci.yml", "README.md"], rules)
    assert chosen == {"pytest", "spin"}
    assert select_jobs(["README.md"], rules) == set()
    assert select_jobs(["src/pkg/app.py"], rules) == {"pytest"}


def test_unknown_job_name_is_an_error(tmp_path):
    path = tmp_path / "map.toml"
    path.write_text('[[rule]]\npaths = ["README.md"]\njobs = ["lint"]\n', encoding="utf-8")
    with pytest.raises(Exception, match="unknown job name: lint"):
        load_map(path)


def test_temp_git_repo_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    _write(repo / "README.md", "base\n")
    _write(repo / "src" / "app.py", "print(1)\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "base")
    base = _rev(repo)
    _write(repo / "src" / "app.py", "print(2)\n")
    _write(repo / "README.md", "changed\n")
    _write(repo / "verify" / "spin" / "model.pml", "byte x\n")
    map_path = repo / "ci" / "test-select.toml"
    _write(map_path, MAP)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "head")
    head = _rev(repo)
    output = tmp_path / "output.txt"
    env = os.environ.copy()
    env["GITHUB_OUTPUT"] = str(output)
    proc = subprocess.run(
        [
            sys.executable,
            str(ROOT / "ci" / "test_select.py"),
            "--base",
            base,
            "--head",
            head,
            "--map",
            str(map_path),
        ],
        cwd=repo,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == ["pytest", "spin"]
    lines = output.read_text(encoding="utf-8").splitlines()
    assert lines == ["pytest=true", "spin=true"]


def _map(folder: Path) -> Path:
    path = folder / "map.toml"
    path.write_text(MAP, encoding="utf-8", newline="\n")
    return path


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")


def _git(repo: Path, *args: str) -> None:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise AssertionError(proc.stderr or proc.stdout)


def _rev(repo: Path) -> str:
    proc = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()
