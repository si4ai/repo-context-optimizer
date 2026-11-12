"""A small linear history for the update tests: SHAs A to E, one change each."""
from __future__ import annotations

from pathlib import Path

from rco import fixture

A_FILES = {
    "pkg/__init__.py": '"""Pkg."""\n',
    "pkg/core.py": "def run(x: int) -> int:\n    return x\n",
    "util/helpers.py": "def clamp(x: int) -> int:\n    return max(0, x)\n",
    "db/schema.sql": "CREATE TABLE users (id BIGINT PRIMARY KEY);\n",
    "README.md": "# h\n",
}
STEPS = [
    {"pkg/core.py": "def run(x: int) -> int:\n    return x + 1\n"},  # B: body only
    {"util/helpers.py": "def clamp(x: int, lo: int = 0) -> int:\n    return max(lo, x)\n"},  # C
    {"extra/mod.py": "VALUE = 1\n"},  # D: a new module
    {"db/schema.sql": "CREATE TABLE users (id BIGINT PRIMARY KEY, name TEXT);\n"},  # E
]


def make_history(root: Path) -> tuple[Path, list[str]]:
    repo, a = fixture.make_repo(root, A_FILES)
    shas = [a]
    for i, files in enumerate(STEPS):
        shas.append(fixture.commit(repo, files, f"step {i}"))
    return repo, shas
