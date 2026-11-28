"""Card 1343: the pack budget is counted in o200k tokens, or characters / 3.5."""
import math
from pathlib import Path

import pytest

tiktoken = pytest.importorskip("tiktoken")

from rco import gitread
from rco.forest_model import estimate_tokens
from rco.pack import MIN_BUDGET, pack, pack_markdown

REPO = Path(__file__).resolve().parents[1]
FORS = (
    "src/rco/pack.py",
    "src/rco/cli.py",
    "src/rco/replay.py",
    "src/rco/imports.py",
    "src/rco/build.py",
)
CAPS = (12000, 4000, 1000, MIN_BUDGET)


def _o200k(text: str) -> int:
    return len(tiktoken.get_encoding("o200k_base").encode(text, disallowed_special=()))


def test_tiktoken_o200k_is_available():
    assert _o200k("abcde") >= 1


def test_five_for_files_stay_within_budget():
    # done-when: rco pack --budget N for 5 --for files on this repo's HEAD is
    # at most N o200k tokens; header and footer residue is named in the note
    sha = gitread.resolve(REPO, "HEAD")
    for path in FORS:
        for cap in CAPS:
            p = pack(REPO, sha, [path], cap)
            text = pack_markdown(p, path)
            real = _o200k(text)
            assert p.used == real, (path, cap, p.used, real)
            residue = [n for n in p.notes if "header and footer" in n]
            if real > p.budget:
                assert residue, (path, cap, real, p.budget, p.notes)
                # no map content left when only the header and footer remain
                assert p.forest == () and p.trees == () and p.hop == ()
            else:
                assert real <= p.budget <= cap


def test_estimate_tokens_matches_o200k_when_installed():
    text = "# rco pack\nmodule rco.pack\n  def pack(repo, sha) -> Pack\n"
    assert estimate_tokens(text) == _o200k(text)
    assert estimate_tokens(text) > (len(text) + 3) // 4 or len(text) < 8


def test_fallback_is_characters_over_3_5(monkeypatch):
    import rco.forest_model as fm
    monkeypatch.setattr(fm, "_enc", None)
    monkeypatch.setattr(fm, "_enc_failed", True)
    assert fm.estimate_tokens("") == 0
    assert fm.estimate_tokens("a") == 1
    for n in (1, 3, 4, 7, 8, 35, 36, 100):
        assert fm.estimate_tokens("x" * n) == math.ceil(n / 3.5)
    # conservative: at least the old characters / 4 count
    assert fm.estimate_tokens("x" * 100) >= (100 + 3) // 4
