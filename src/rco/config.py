"""rco.toml settings for running rco: the store backend and test selection.

Read from rco.toml at the top of the working tree, because these settings
are about this checkout (where its store lives), not about a commit. Module
rules (`[[module]]`) in the same file are read from git at each SHA instead
(rco.modules), so they never change what a commit's Tree or Forest is.

    backend = "sqlite"          # or the name of an rco.backends entry point
    [store]
    path = ".rco/rco.db"        # sqlite only; relative to the working tree top
    [affected]
    ignore = ["docs/**"]        # added to every `rco affected --ignore`

A missing file means the defaults. A file that is not valid TOML is noted
on stderr and ignored, like a bad module rule. A setting with the wrong
type, or a backend that does not exist, is a usage error (exit 2), because
it names something rco cannot do.

Adapters: an installed distribution that registers an entry point in the
group `rco.backends` adds a backend by that name. The entry point is a
callable `factory(repo: Path, options: dict) -> Store`; options is the
`[store]` table. `rco.store.conformance.run_all` is the check an adapter
runs against itself.
"""
from __future__ import annotations

import subprocess
import sys
import tomllib
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Any, Mapping

from rco.output import UsageError

CONFIG_FILE = "rco.toml"
ENTRY_POINT_GROUP = "rco.backends"
DEFAULT_DB = ".rco/rco.db"
NOT_SHIPPED = {"mill": "the mill backend extra", "mcp": "the MCP backend"}


@dataclass(frozen=True)
class Config:
    backend: str = "sqlite"
    store: Mapping[str, Any] = field(default_factory=dict)
    affected_ignore: tuple[str, ...] = ()
    top: Path | None = None


def worktree_top(repo: Path) -> Path:
    p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
                       capture_output=True)
    if p.returncode != 0:
        raise UsageError(f"not a git repository: {repo}")
    return Path(p.stdout.decode().strip())


def parse(text: str, top: Path | None = None) -> Config:
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        print(f"rco: {CONFIG_FILE}: not valid TOML ({type(e).__name__}); settings ignored",
              file=sys.stderr)
        return Config(top=top)
    backend = doc.get("backend", "sqlite")
    if not isinstance(backend, str) or not backend.strip():
        raise UsageError(f"{CONFIG_FILE}: backend must be a non-empty string")
    store = doc.get("store", {})
    if not isinstance(store, dict):
        raise UsageError(f"{CONFIG_FILE}: [store] must be a table")
    if "path" in store and (not isinstance(store["path"], str) or not store["path"].strip()):
        raise UsageError(f"{CONFIG_FILE}: [store] path must be a non-empty string")
    aff = doc.get("affected", {})
    ignore = aff.get("ignore", []) if isinstance(aff, dict) else None
    if not isinstance(ignore, list) or not all(isinstance(g, str) for g in ignore):
        raise UsageError(f"{CONFIG_FILE}: [affected] ignore must be a list of globs")
    return Config(backend.strip(), dict(store), tuple(ignore), top)


def load(repo: Path) -> Config:
    top = worktree_top(repo)
    f = top / CONFIG_FILE
    if not f.is_file():
        return Config(top=top)
    try:
        text = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise UsageError(f"{CONFIG_FILE}: unreadable: {e}") from None
    return parse(text, top)


def db_path(cfg: Config) -> Path:
    rel = cfg.store.get("path", DEFAULT_DB)
    p = Path(rel)
    return p if p.is_absolute() else (cfg.top or Path(".")) / p


def open_store(repo: Path, create: bool):
    """The configured Store, or None when create is False and none exists yet.

    create=False is for read-only commands (pack, check): they never create
    a store, so a missing sqlite file gives None.
    """
    cfg = load(repo)
    if cfg.backend == "sqlite":
        from rco.store.sqlite import SqliteStore, ensure_excluded
        db = db_path(cfg)
        if not create:
            return SqliteStore(db) if db.exists() else None
        if "path" not in cfg.store:
            ensure_excluded(repo)  # the default .rco/ never shows up in git status
        db.parent.mkdir(parents=True, exist_ok=True)
        return SqliteStore(db)
    if cfg.backend in NOT_SHIPPED:
        raise UsageError(f"{CONFIG_FILE}: backend {cfg.backend!r} needs {NOT_SHIPPED[cfg.backend]}, "
                         f"which is not shipped yet; use sqlite or an {ENTRY_POINT_GROUP} adapter")
    eps = [ep for ep in metadata.entry_points(group=ENTRY_POINT_GROUP) if ep.name == cfg.backend]
    if not eps:
        raise UsageError(f"{CONFIG_FILE}: no backend {cfg.backend!r} (no {ENTRY_POINT_GROUP} "
                         f"entry point by that name is installed)")
    from rco.store import Store
    store = eps[0].load()(cfg.top, dict(cfg.store))
    if not isinstance(store, Store):
        raise UsageError(f"backend {cfg.backend!r} did not return a Store")
    return store
