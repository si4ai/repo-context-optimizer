"""Mined prose: docstrings, package docstring, directory README (step 4). Stub."""
from __future__ import annotations

from typing import Mapping

from rco.model import Module


def mine(module: Module, sources: Mapping[str, bytes]) -> str:
    """Prose text, at most PROSE_CAP characters, redacted; "" when nothing is found."""
    raise NotImplementedError("Phase 2 step 4: mined prose")
