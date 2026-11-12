"""check_role_trailer reports commits that lack a known Role: trailer.

The command is a fact report: it exits 0 even when it finds something.
--strict fails on a commit with no known role and passes when every
commit names a configured role. A git failure exits 2.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_role_trailer as crt  # noqa: E402

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "builder",
    "GIT_AUTHOR_EMAIL": "builder@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "builder",
    "GIT_COMMITTER_EMAIL": "builder@users.noreply.github.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(repo: Path, *args: str, env: dict[str, str] | None = None) -> str:
    merged = os.environ.copy()
    merged.update(_GIT_ENV)
    if env:
        merged.update(env)
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, env=merged, text=True
    )
    return proc.stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str, env: dict[str, str] | None = None) -> str:
    _git(repo, "add", "-A", env=env)
    _git(repo, "commit", "--allow-empty", "-qm", message, env=env)
    return _git(repo, "rev-parse", "HEAD", env=env).strip()


def _base_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "ci/hygiene.toml", 'roles = ["builder", "auditor"]\n')
    _write(repo, "README.md", "demo\n")
    return repo, _commit(repo, "base\n\nRole: builder\n")


def test_missing_role_is_a_finding_and_default_exit_is_zero(tmp_path: Path, capsys) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", "hello\n")
    _commit(repo, "add notes\n")
    code = crt.main(
        ["--root", str(repo), "--base", base, "--head", "HEAD", "--json"]
    )
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    kinds = {item["kind"] for item in payload["findings"]}
    assert "missing role trailer" in kinds
    assert (
        crt.main(["--root", str(repo), "--base", base, "--head", "HEAD", "--strict"])
        == 1
    )


def test_known_role_is_clean_under_strict(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", "hello\n")
    _commit(repo, "add notes\n\nRole: auditor\n")
    assert (
        crt.main(["--root", str(repo), "--base", base, "--head", "HEAD", "--strict"])
        == 0
    )


def test_unknown_role_and_role_flag(tmp_path: Path, capsys) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", "hello\n")
    _commit(repo, "add notes\n\nRole: gardener\n")
    code = crt.main(
        [
            "--root",
            str(repo),
            "--base",
            base,
            "--head",
            "HEAD",
            "--json",
            "--role",
            "builder",
            "--role",
            "auditor",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    kinds = {item["kind"] for item in payload["findings"]}
    assert "no known role" in kinds
    assert "unknown role" in kinds
    # The same trailer passes when the flag lists it.
    assert (
        crt.main(
            [
                "--root",
                str(repo),
                "--base",
                base,
                "--head",
                "HEAD",
                "--strict",
                "--role",
                "gardener",
            ]
        )
        == 0
    )


def test_bot_author_may_omit_trailer(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", "hello\n")
    bot = {
        "GIT_AUTHOR_NAME": "dependabot[bot]",
        "GIT_AUTHOR_EMAIL": "49699333+dependabot[bot]@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "dependabot[bot]",
        "GIT_COMMITTER_EMAIL": "49699333+dependabot[bot]@users.noreply.github.com",
    }
    _commit(repo, "chore: bump example\n", env=bot)
    assert (
        crt.main(["--root", str(repo), "--base", base, "--head", "HEAD", "--strict"])
        == 0
    )


def test_crash_on_missing_git_repo(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert crt.main(["--root", str(empty), "--base", "HEAD"]) == 2
