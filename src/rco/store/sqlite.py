"""SQLite Store (Phase 2 step 1). Stub: the schema below is the step-0 draft.

One database at .rco/rco.db in WAL mode. First use adds .rco/ to
.git/info/exclude, never .gitignore. No stale column anywhere (rule 3).
"""
from __future__ import annotations

from pathlib import Path

SCHEMA_SQL = """
CREATE TABLE node (kind TEXT NOT NULL, key TEXT NOT NULL, json BLOB NOT NULL,
                   PRIMARY KEY (kind, key));
CREATE TABLE sha_index (sha TEXT NOT NULL, module TEXT NOT NULL,
                        module_hash TEXT NOT NULL, tree_hash TEXT NOT NULL,
                        PRIMARY KEY (sha, module));
CREATE TABLE pointer (name TEXT PRIMARY KEY, sha TEXT NOT NULL);
CREATE TABLE prose (module TEXT NOT NULL, tree_hash TEXT NOT NULL, seq INTEGER NOT NULL,
                    json BLOB NOT NULL, PRIMARY KEY (module, tree_hash));
CREATE TABLE queue (module TEXT NOT NULL, tree_hash TEXT NOT NULL,
                    PRIMARY KEY (module, tree_hash));
CREATE TABLE notice (seq INTEGER PRIMARY KEY, text TEXT NOT NULL);
"""

DB_RELPATH = ".rco/rco.db"


class SqliteStore:
    """Implements rco.store.Store over one SQLite file (step 1)."""

    def __init__(self, path: Path) -> None:
        raise NotImplementedError("Phase 2 step 1: SQLite store")


def open_repo_store(repo: Path) -> SqliteStore:
    """The default store of a repo: <repo>/.rco/rco.db (step 1)."""
    raise NotImplementedError("Phase 2 step 1: SQLite store")
