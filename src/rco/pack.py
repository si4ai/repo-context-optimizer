"""`rco pack` (Phase 2 step 5). Stub."""
from __future__ import annotations

from pathlib import Path
from typing import Sequence

from rco.forest_model import DEFAULT_BUDGET, Pack
from rco.store import Store


def pack(repo: Path, sha: str, for_paths: Sequence[str] | None = None,
         budget: int = DEFAULT_BUDGET, store: Store | None = None) -> Pack:
    """Use the store when its pointer covers sha; else build from git, writing nothing."""
    raise NotImplementedError("Phase 2 step 5: rco pack")


def pack_markdown(p: Pack) -> str:
    raise NotImplementedError("Phase 2 step 5: rco pack")
