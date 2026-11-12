"""Scratch git repos for the self-test and the tests.

Commits use a fixed identity and fixed dates, so the same files always give
the same commit SHA.
"""
from __future__ import annotations

import contextlib
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator, Mapping

FIXED_DATE = "2026-01-01T00:00:00+0000"

DRIFT_FILES: dict[str, str] = {
    "pkg/__init__.py": '"""Drift fixture package."""\n',
    "pkg/core.py": (
        '"""Core of the drift fixture."""\n'
        "LIMIT = 10\n\n\n"
        "def run(argv: list[str]) -> int:\n"
        '    """Run the tool."""\n'
        "    return 0\n\n\n"
        "class Store:\n"
        '    """A store."""\n'
        "    name: str\n\n"
        "    def get(self, key: str) -> bytes | None:\n"
        "        return None\n"
    ),
    "pkg/cli.py": (
        "import argparse\n\n\n"
        "def make_parser() -> argparse.ArgumentParser:\n"
        "    p = argparse.ArgumentParser(prog='tool')\n"
        "    p.add_argument('--out', metavar='FILE')\n"
        "    return p\n"
    ),
    "db/schema.sql": "CREATE TABLE users (\n  id BIGINT PRIMARY KEY,\n  email TEXT NOT NULL\n);\n",
    "README.md": "# drift fixture\n",
}


def _env() -> dict[str, str]:
    env = dict(os.environ)
    env.update({
        "GIT_AUTHOR_NAME": "fixture", "GIT_AUTHOR_EMAIL": "fixture@example.com",
        "GIT_COMMITTER_NAME": "fixture", "GIT_COMMITTER_EMAIL": "fixture@example.com",
        "GIT_AUTHOR_DATE": FIXED_DATE, "GIT_COMMITTER_DATE": FIXED_DATE,
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull, "LC_ALL": "C",
    })
    return env


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                       env=_env(), check=True)
    return p.stdout.decode().strip()


def write_files(repo: Path, files: Mapping[str, str | bytes]) -> None:
    for rel, body in files.items():
        f = repo / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(body, bytes):
            f.write_bytes(body)
        else:
            f.write_text(body, encoding="utf-8")


def commit(repo: Path, files: Mapping[str, str | bytes], message: str = "fixture") -> str:
    """Write files, stage everything, commit; returns the commit SHA."""
    write_files(repo, files)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(root: Path, files: Mapping[str, str | bytes]) -> tuple[Path, str]:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q", "-b", "main")
    return root, commit(root, files)


@contextlib.contextmanager
def drift_repo() -> Iterator[tuple[Path, str]]:
    with tempfile.TemporaryDirectory(prefix="rco-selftest-") as d:
        yield make_repo(Path(d) / "repo", DRIFT_FILES)
