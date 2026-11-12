"""`rco update`: publish a SHA to the store (Phase 2 step 3). Stub.

Order (README sequence diagram, verify/spin/rco_update.pml variant fixed):
write Trees for changed modules; check every module of NEW is in the
store (rule 2); write the sha index; set_pointer(OLD, NEW) only if NEW
descends from OLD (rule 1); queue prose only after a successful swap
(rule 4). A refused swap writes nothing more. No holds, no locks.
"""
from __future__ import annotations

from pathlib import Path

from rco.forest_model import DEFAULT_POINTER, UpdateResult
from rco.store import Store


def update(repo: Path, store: Store, new: str, name: str = DEFAULT_POINTER) -> UpdateResult:
    raise NotImplementedError("Phase 2 step 3: rco update")
