"""Computed Forest (Phase 2 step 2). Stub."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from rco.forest_model import Forest
from rco.model import Map


def compute_forest(m: Map, edges: Mapping[str, tuple[str, ...]],
                   sources: Mapping[str, bytes]) -> Forest:
    """Forest of m: layer, depends_on/used_by mirrors, entry points, owns. No prose.

    sources is used only for entry points ([project.scripts] in pyproject.toml).
    """
    raise NotImplementedError("Phase 2 step 2: computed Forest")


def build_forest(repo: Path, sha: str) -> Forest:
    """Forest of a commit, computed on the fly from git (no store)."""
    raise NotImplementedError("Phase 2 step 2: computed Forest")
