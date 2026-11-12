"""Prose for Forest entries (Phase 2 step 4). Stub.

Mined prose is the default (zero tokens). The LLM path (prose/llm.py) is a
hook, off by default.
"""
from __future__ import annotations

from pathlib import Path

from rco.store import Store


def drain_queue(repo: Path, store: Store) -> int:
    """Write prose for queued (module, tree_hash) rows of published SHAs; returns rows done."""
    raise NotImplementedError("Phase 2 step 4: prose queue")
