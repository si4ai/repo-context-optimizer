"""Python import edges between in-repo modules (Phase 2 step 2). Stub."""
from __future__ import annotations

from typing import Mapping, Sequence

from rco.model import Module


def module_edges(modules: Sequence[Module], sources: Mapping[str, bytes]
                 ) -> dict[str, tuple[str, ...]]:
    """Module id -> sorted ids of in-repo modules it imports (itself excluded).

    sources maps repo-relative path to file bytes at one SHA. `import` and
    `from ... import`, relative too; external imports are ignored.
    """
    raise NotImplementedError("Phase 2 step 2: import graph")
