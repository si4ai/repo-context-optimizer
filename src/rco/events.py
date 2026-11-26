"""One JSON line per rco run in .rco/events.jsonl (card 1314).

Each run appends one line with a single O_APPEND write of at most 4096 bytes,
so concurrent runs on a local filesystem never tear or interleave lines. No
lock and no daemon. On a network filesystem O_APPEND is not guaranteed to be
atomic, so lines from runs at the same instant may interleave there.

The file is written only when .rco/ already exists at the top of the working
tree (the first `rco update` creates it), so read-only commands in a fresh
clone still write nothing. A log failure never changes the exit code: any
failure is one note on stderr.
"""
from __future__ import annotations

import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Sequence

from rco.canon import canonical_json

EVENTS_RELPATH = ".rco/events.jsonl"
VERSION = 1
MAX_LINE = 4096


def _head(top: Path) -> str | None:
    p = subprocess.run(["git", "-C", str(top), "rev-parse", "--verify", "-q", "HEAD"],
                       capture_output=True)
    if p.returncode != 0:
        return None
    return p.stdout.decode().strip() or None


def _store_pointer(repo: Path) -> str | None:
    from rco import config
    from rco.store.sqlite import read_pointer
    try:
        cfg = config.load(repo)
    except Exception:  # noqa: BLE001 - a bad rco.toml must not stop the log
        return None
    if cfg.backend != "sqlite":
        return None
    return read_pointer(config.db_path(cfg), "main")


def _enc(obj: dict) -> bytes:
    return canonical_json(obj) + b"\n"


def _fit(obj: dict) -> bytes:
    """canonical_json + newline in at most MAX_LINE bytes.

    The --for list is cut first (longest prefix that fits), then cmd, which
    argparse already bounds to a subcommand name, as a last resort.
    """
    line = _enc(obj)
    if len(line) > MAX_LINE and obj["for"]:
        lo, hi = 0, len(obj["for"])
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(_enc({**obj, "for": obj["for"][:mid]})) <= MAX_LINE:
                lo = mid
            else:
                hi = mid - 1
        obj = {**obj, "for": obj["for"][:lo]}
        line = _enc(obj)
    over = len(line) - MAX_LINE
    if over > 0:
        raw = obj["cmd"].encode("utf-8")
        obj = {**obj, "cmd": raw[:max(0, len(raw) - over)].decode("utf-8", "ignore")}
        line = _enc(obj)
    return line


def line_for(cmd: str, exit_code: int, t0: float, start_wall: float, store: str | None,
             head: str | None, for_paths: Sequence[str] | None) -> bytes:
    obj = {
        "v": VERSION,
        "run": uuid.uuid4().hex,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_wall)),
        "cmd": cmd,
        "exit": int(exit_code),
        "store": store,
        "head": head,
        "ms": max(0, int((time.monotonic() - t0) * 1000)),
        "for": None if for_paths is None else [str(p) for p in for_paths],
    }
    return _fit(obj)


def record(repo: Path, cmd: str, exit_code: int, t0: float, start_wall: float | None = None,
           for_paths: Sequence[str] | None = None) -> None:
    """One line for this run when .rco/ exists; never raises, never changes the exit."""
    try:
        from rco.config import worktree_top
        from rco.output import UsageError
        try:
            top = worktree_top(Path(repo))
        except UsageError:
            return  # not a git repository: nothing to log against
        if not (top / ".rco").is_dir():
            return  # read-only promise: never create .rco/
        if start_wall is None:
            start_wall = time.time() - (time.monotonic() - t0)
        data = line_for(cmd, exit_code, t0, start_wall, _store_pointer(top), _head(top),
                        for_paths)
        fd = os.open(top / EVENTS_RELPATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    except Exception as e:  # noqa: BLE001 - a log failure never breaks the command
        from rco.output import note
        note(f"events log not written: {type(e).__name__}: {e}")
