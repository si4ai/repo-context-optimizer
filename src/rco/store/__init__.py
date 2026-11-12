"""Store protocol and the in-memory fake (docs/design/forest-contract.md, section 5).

The README's five calls are put_node, get_node, list_by_sha, set_pointer and
post_notice. The protocol adds the reads and writes those five imply:
get_pointer, index_sha, prose and queue. set_pointer is the only pointer
write and is one atomic compare-and-swap (README rule 1). No call stores a
stale flag (rule 3).
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from rco.canon import canonical_json
from rco.forest_model import Prose, prose_from_obj, prose_to_obj


class NodeKind(str, Enum):
    TREE = "tree"  # canonical Tree JSON (rco.tree/1), keyed by ModuleHash
    EDGES = "edges"  # {"depends_on": [...]} for a ModuleHash, keyed by ModuleHash


@dataclass(frozen=True, slots=True, order=True)
class IndexRow:
    """One module of a SHA. list_by_sha returns rows sorted by module."""

    module: str
    module_hash: str
    tree_hash: str


class StoreConflict(Exception):
    """A write that would change content already stored under the same key."""


@runtime_checkable
class Store(Protocol):
    # README five calls
    def put_node(self, kind: NodeKind, key: str, obj: dict) -> None:
        """Idempotent. Same key, same bytes: no-op. Same key, other bytes: StoreConflict."""

    def get_node(self, kind: NodeKind, key: str) -> dict | None: ...

    def list_by_sha(self, sha: str) -> tuple[IndexRow, ...]:
        """Every module of sha, sorted; () when sha is not indexed."""

    def set_pointer(self, name: str, old: str | None, new: str) -> bool:
        """Atomic CAS: move name from old to new; old None means "absent". True if it moved."""

    def post_notice(self, text: str) -> None:
        """Optional notice (stale Forest, affected PR). A store that cannot post prints it."""

    # supporting calls
    def get_pointer(self, name: str) -> str | None: ...

    def index_sha(self, sha: str, rows: tuple[IndexRow, ...]) -> None:
        """Write the whole index of sha at once. Same rows again: no-op; other rows: StoreConflict."""

    def put_prose(self, module: str, prose: Prose) -> None:
        """Keyed by (module, prose.tree_hash); a rewrite for the same key replaces the text."""

    def get_prose(self, module: str, tree_hash: str | None = None) -> Prose | None:
        """Exact TreeHash, or with None the most recently written prose for module."""

    def enqueue(self, module: str, tree_hash: str) -> None:
        """Queue a prose rewrite. Idempotent (debounce: one row per key)."""

    def queued(self) -> tuple[tuple[str, str], ...]:
        """Queued (module, tree_hash) rows, sorted."""

    def dequeue(self, module: str, tree_hash: str) -> None: ...


class MemoryStore:
    """In-memory Store for tests and for steps that run before SQLite lands."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.nodes: dict[tuple[str, str], bytes] = {}
        self.index: dict[str, tuple[IndexRow, ...]] = {}
        self.pointers: dict[str, str] = {}
        self.prose: dict[tuple[str, str], tuple[int, Prose]] = {}
        self.queue: set[tuple[str, str]] = set()
        self.notices: list[str] = []
        self._seq = 0

    def put_node(self, kind: NodeKind, key: str, obj: dict) -> None:
        b = canonical_json(obj)
        with self._lock:
            have = self.nodes.get((NodeKind(kind).value, key))
            if have is not None and have != b:
                raise StoreConflict(f"{NodeKind(kind).value} {key}: different content")
            self.nodes[(NodeKind(kind).value, key)] = b

    def get_node(self, kind: NodeKind, key: str) -> dict | None:
        b = self.nodes.get((NodeKind(kind).value, key))
        return None if b is None else json.loads(b)

    def list_by_sha(self, sha: str) -> tuple[IndexRow, ...]:
        return self.index.get(sha, ())

    def set_pointer(self, name: str, old: str | None, new: str) -> bool:
        with self._lock:
            if self.pointers.get(name) != old:
                return False
            self.pointers[name] = new
            return True

    def post_notice(self, text: str) -> None:
        self.notices.append(text)

    def get_pointer(self, name: str) -> str | None:
        return self.pointers.get(name)

    def index_sha(self, sha: str, rows: tuple[IndexRow, ...]) -> None:
        rows = tuple(sorted(rows))
        with self._lock:
            have = self.index.get(sha)
            if have is not None and have != rows:
                raise StoreConflict(f"sha index {sha}: different rows")
            self.index[sha] = rows

    def put_prose(self, module: str, prose: Prose) -> None:
        prose_from_obj(prose_to_obj(prose))  # same validation as a backend round trip
        with self._lock:
            self._seq += 1
            self.prose[(module, prose.tree_hash)] = (self._seq, prose)

    def get_prose(self, module: str, tree_hash: str | None = None) -> Prose | None:
        if tree_hash is not None:
            hit = self.prose.get((module, tree_hash))
            return None if hit is None else hit[1]
        mine = [v for (m, _), v in self.prose.items() if m == module]
        return max(mine, key=lambda v: v[0])[1] if mine else None

    def enqueue(self, module: str, tree_hash: str) -> None:
        with self._lock:
            self.queue.add((module, tree_hash))

    def queued(self) -> tuple[tuple[str, str], ...]:
        return tuple(sorted(self.queue))

    def dequeue(self, module: str, tree_hash: str) -> None:
        with self._lock:
            self.queue.discard((module, tree_hash))
