"""A commit sha pinned in docs/baseline is 40 hex digits (a 12-digit short id is also fine)."""
import re
from pathlib import Path

BASELINE = Path(__file__).resolve().parent.parent / "docs" / "baseline"
_HEX = re.compile(r"`([0-9a-f]{12,64})`")


def _bad(text: str) -> list[str]:
    """Tokens of hex digits that are neither a short id (12 digits) nor a full sha (40 digits)."""
    return [f"{tok} ({len(tok)} digits)" for tok in _HEX.findall(text) if len(tok) not in (12, 40)]


def test_pinned_shas_in_baseline_docs_are_short_ids_or_full_shas():
    bad = [f"{doc.name}: {b}" for doc in sorted(BASELINE.glob("*.md"))
           for b in _bad(doc.read_text(encoding="utf-8"))]
    assert not bad, bad


def test_the_check_catches_a_41_digit_sha():
    assert _bad("`" + "a" * 41 + "`") and not _bad("`" + "a" * 40 + "` and `" + "b" * 12 + "`")
