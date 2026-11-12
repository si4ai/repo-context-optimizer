"""Path to module (tree-contract section 4): Phase 0's package grain."""
from __future__ import annotations

from typing import Iterable

from rco.model import Module


def module_of(path: str) -> str:
    """The directory holding the file; files at the repo root map to "."."""
    head, sep, _ = path.rpartition("/")
    return head if sep else "."


def group(paths: Iterable[str]) -> tuple[Module, ...]:
    by: dict[str, list[str]] = {}
    for p in paths:
        by.setdefault(module_of(p), []).append(p)
    return tuple(Module(id=m, files=tuple(sorted(fs))) for m, fs in sorted(by.items()))
