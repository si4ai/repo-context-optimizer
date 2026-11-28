"""Shared with/without token counts for replay and metrics measure (card 1351).

`without` is the changed files plus the in-repo files they import (one hop).
`with` is the pack plus the changed files (the files the agent still opens).
A skipped pack (nothing to add) is a tie: with equals without.
"""
from __future__ import annotations


def arms(*, changed_tokens: int, imported_tokens: int, pack_tokens: int,
         skipped: bool, n_changed: int, n_imported: int) -> tuple[dict, dict]:
    """Return (without, with) file and token counts under the shared definition."""
    without = {"files": n_changed + n_imported,
               "tokens": changed_tokens + imported_tokens}
    if skipped:
        with_ = {"files": without["files"], "tokens": without["tokens"],
                 "pack_tokens": 0, "pack_skipped": True,
                 "changed_tokens": changed_tokens}
        return without, with_
    return without, {"files": n_changed + 1,
                     "tokens": pack_tokens + changed_tokens,
                     "pack_tokens": pack_tokens, "pack_skipped": False,
                     "changed_tokens": changed_tokens}


def pack_is_skipped(pack_text: str) -> bool:
    """True when there is no pack, or the pack is a nothing-to-add note."""
    if not pack_text.strip():
        return True
    from rco.pack import NOOP_NOTES
    return any(n in pack_text for n in NOOP_NOTES)
