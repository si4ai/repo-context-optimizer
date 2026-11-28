"""The repo pins LF checkout so goldens stay stable on Windows."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_gitattributes_pins_lf():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "* text=auto eol=lf" in text


def test_fixtures_and_goldens_are_lf():
    raw = subprocess.check_output(
        ["git", "-C", str(ROOT), "ls-files", "-z", "tests/fixtures", "tests/golden"]
    )
    paths = [p.decode() for p in raw.split(b"\0") if p]
    assert paths, "expected tracked fixtures or goldens"
    for rel in paths:
        data = (ROOT / rel).read_bytes()
        assert b"\r" not in data, rel
