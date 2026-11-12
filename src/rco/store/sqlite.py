"""SQLite Store (Phase 2 step 1; docs/design/forest-contract.md, section 5).

One database at .rco/rco.db in WAL mode. First use adds .rco/ to
.git/info/exclude, never .gitignore. No stale column anywhere (rule 3).
Every write is one transaction; set_pointer is a conditional UPDATE (or an
insert-if-absent for the first SHA), so two processes racing from the same
OLD leave exactly one winner (rule 1).
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from rco.canon import canonical_json
from rco.forest_model import Prose, prose_from_obj, prose_to_obj
from rco.output import UsageError
from rco.store import IndexRow, NodeKind, StoreConflict

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS node (kind TEXT NOT NULL, key TEXT NOT NULL, json BLOB NOT NULL,
                   PRIMARY KEY (kind, key));
CREATE TABLE IF NOT EXISTS sha_index (sha TEXT NOT NULL, module TEXT NOT NULL,
                        module_hash TEXT NOT NULL, tree_hash TEXT NOT NULL,
                        PRIMARY KEY (sha, module));
CREATE TABLE IF NOT EXISTS pointer (name TEXT PRIMARY KEY, sha TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS prose (module TEXT NOT NULL, tree_hash TEXT NOT NULL,
                    seq INTEGER NOT NULL, json BLOB NOT NULL, PRIMARY KEY (module, tree_hash));
CREATE TABLE IF NOT EXISTS queue (module TEXT NOT NULL, tree_hash TEXT NOT NULL,
                    PRIMARY KEY (module, tree_hash));
CREATE TABLE IF NOT EXISTS notice (seq INTEGER PRIMARY KEY, text TEXT NOT NULL);
"""

DB_RELPATH = ".rco/rco.db"
EXCLUDE_LINE = "/.rco/"
_TABLES = ("node", "sha_index", "pointer", "prose", "queue", "notice")


class SqliteStore:
    """Implements rco.store.Store over one SQLite file."""

    def __init__(self, path: Path, timeout: float = 30.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(self.path), timeout=timeout, isolation_level=None,
                                   check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=FULL")
        with self._tx() as c:
            for stmt in SCHEMA_SQL.split(";"):
                if stmt.strip():
                    c.execute(stmt)

    def close(self) -> None:
        self._db.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        """One write transaction; BEGIN IMMEDIATE takes the write lock up front."""
        with self._lock:
            self._db.execute("BEGIN IMMEDIATE")
            try:
                yield self._db
            except BaseException:
                self._db.execute("ROLLBACK")
                raise
            self._db.execute("COMMIT")

    def _one(self, sql: str, args: tuple = ()):
        with self._lock:
            return self._db.execute(sql, args).fetchone()

    # README five calls
    def put_node(self, kind: NodeKind, key: str, obj: dict) -> None:
        k = NodeKind(kind).value
        b = canonical_json(obj)
        with self._tx() as c:
            row = c.execute("SELECT json FROM node WHERE kind=? AND key=?", (k, key)).fetchone()
            if row is None:
                c.execute("INSERT INTO node(kind, key, json) VALUES (?,?,?)", (k, key, b))
            elif bytes(row[0]) != b:
                raise StoreConflict(f"{k} {key}: different content")

    def get_node(self, kind: NodeKind, key: str) -> dict | None:
        row = self._one("SELECT json FROM node WHERE kind=? AND key=?", (NodeKind(kind).value, key))
        return None if row is None else json.loads(bytes(row[0]))

    def list_by_sha(self, sha: str) -> tuple[IndexRow, ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT module, module_hash, tree_hash FROM sha_index WHERE sha=? ORDER BY module",
                (sha,)).fetchall()
        return tuple(IndexRow(*r) for r in rows)

    def set_pointer(self, name: str, old: str | None, new: str) -> bool:
        with self._tx() as c:
            if old is None:
                cur = c.execute("INSERT OR IGNORE INTO pointer(name, sha) VALUES (?,?)", (name, new))
            else:
                cur = c.execute("UPDATE pointer SET sha=? WHERE name=? AND sha=?", (new, name, old))
            return cur.rowcount == 1

    def post_notice(self, text: str) -> None:
        with self._tx() as c:
            c.execute("INSERT INTO notice(text) VALUES (?)", (text,))

    # supporting calls
    def get_pointer(self, name: str) -> str | None:
        row = self._one("SELECT sha FROM pointer WHERE name=?", (name,))
        return None if row is None else row[0]

    def index_sha(self, sha: str, rows: tuple[IndexRow, ...]) -> None:
        rows = tuple(sorted(rows))
        with self._tx() as c:
            have = tuple(IndexRow(*r) for r in c.execute(
                "SELECT module, module_hash, tree_hash FROM sha_index WHERE sha=? ORDER BY module",
                (sha,)).fetchall())
            if have:
                if have != rows:
                    raise StoreConflict(f"sha index {sha}: different rows")
                return
            c.executemany("INSERT INTO sha_index(sha, module, module_hash, tree_hash) "
                          "VALUES (?,?,?,?)", [(sha, r.module, r.module_hash, r.tree_hash)
                                               for r in rows])

    def put_prose(self, module: str, prose: Prose) -> None:
        b = canonical_json(prose_to_obj(prose))
        with self._tx() as c:
            have = c.execute("SELECT json FROM prose WHERE module=? AND tree_hash=?",
                             (module, prose.tree_hash)).fetchone()
            if have is not None and bytes(have[0]) == b:
                return  # idempotent: same prose again keeps its seq (P4)
            seq = c.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM prose").fetchone()[0]
            c.execute("INSERT OR REPLACE INTO prose(module, tree_hash, seq, json) VALUES (?,?,?,?)",
                      (module, prose.tree_hash, seq, b))

    def get_prose(self, module: str, tree_hash: str | None = None) -> Prose | None:
        if tree_hash is None:
            row = self._one("SELECT json FROM prose WHERE module=? ORDER BY seq DESC LIMIT 1",
                            (module,))
        else:
            row = self._one("SELECT json FROM prose WHERE module=? AND tree_hash=?",
                            (module, tree_hash))
        return None if row is None else prose_from_obj(json.loads(bytes(row[0])))

    def enqueue(self, module: str, tree_hash: str) -> None:
        with self._tx() as c:
            c.execute("INSERT OR IGNORE INTO queue(module, tree_hash) VALUES (?,?)",
                      (module, tree_hash))

    def queued(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            rows = self._db.execute(
                "SELECT module, tree_hash FROM queue ORDER BY module, tree_hash").fetchall()
        return tuple((m, t) for m, t in rows)

    def dequeue(self, module: str, tree_hash: str) -> None:
        with self._tx() as c:
            c.execute("DELETE FROM queue WHERE module=? AND tree_hash=?", (module, tree_hash))

    # not in the protocol: a canonical dump for the idempotence property (P4)
    def dump(self) -> bytes:
        out = {}
        with self._lock:
            for t in _TABLES:
                rows = self._db.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall()
                out[t] = [[bytes(v).decode("utf-8") if isinstance(v, bytes) else v for v in r]
                          for r in rows]
        return canonical_json(out)


def _git_path(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", "-C", str(repo), "rev-parse", *args], capture_output=True)
    if p.returncode != 0:
        raise UsageError(f"not a git repository: {repo}")
    return p.stdout.decode().strip()


def ensure_excluded(repo: Path) -> Path:
    """Add /.rco/ to the repo's info/exclude (shared by worktrees); never .gitignore."""
    top = Path(_git_path(repo, "--show-toplevel"))
    excl = Path(_git_path(repo, "--git-path", "info/exclude"))
    if not excl.is_absolute():
        excl = (top / excl).resolve()
    text = excl.read_text(encoding="utf-8") if excl.exists() else ""
    if EXCLUDE_LINE not in text.splitlines():
        excl.parent.mkdir(parents=True, exist_ok=True)
        sep = "" if not text or text.endswith("\n") else "\n"
        excl.write_text(f"{text}{sep}{EXCLUDE_LINE}\n", encoding="utf-8")
    return top


def open_repo_store(repo: Path) -> SqliteStore:
    """The default store of a repo: <worktree top>/.rco/rco.db."""
    top = ensure_excluded(repo)
    return SqliteStore(top / DB_RELPATH)
