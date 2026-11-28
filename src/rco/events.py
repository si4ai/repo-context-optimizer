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
from rco.modules import for_path

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


def _with_for_cut(obj: dict, kept: list) -> dict:
    """Copy obj with a possibly shorter for list; set for_cut only when cut."""
    original = obj["for"]
    new = {**obj, "for": kept}
    dropped = len(original) - len(kept)
    if dropped:
        new["for_cut"] = dropped
    else:
        new.pop("for_cut", None)
    return new


def _fit(obj: dict) -> bytes:
    """canonical_json + newline in at most MAX_LINE bytes.

    The --for list is cut first (longest prefix that fits), then cmd, which
    argparse already bounds to a subcommand name, as a last resort.
    for_cut is counted in the line size when the list is cut.
    """
    line = _enc(obj)
    if len(line) > MAX_LINE and obj["for"]:
        lo, hi = 0, len(obj["for"])
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if len(_enc(_with_for_cut(obj, obj["for"][:mid]))) <= MAX_LINE:
                lo = mid
            else:
                hi = mid - 1
        obj = _with_for_cut(obj, obj["for"][:lo])
        line = _enc(obj)
    over = len(line) - MAX_LINE
    if over > 0 and obj.get("cmd"):
        raw = obj["cmd"].encode("utf-8")
        obj = {**obj, "cmd": raw[:max(0, len(raw) - over)].decode("utf-8", "ignore")}
        line = _enc(obj)
    return line


def log_for_paths(top: Path, for_paths: Sequence[str] | None) -> list[str] | None:
    """--for paths as repo paths: for_path, no leading ./, abs paths under top."""
    if for_paths is None:
        return None
    top_res = Path(top).resolve()
    out: list[str] = []
    for raw in for_paths:
        p = Path(raw)
        try:
            if p.is_absolute():
                logged = p.resolve().relative_to(top_res).as_posix()
            else:
                logged = for_path(str(raw))
                while logged.startswith("./"):
                    logged = logged[2:]
        except (ValueError, OSError):
            logged = for_path(str(raw))
            while logged.startswith("./"):
                logged = logged[2:]
        out.append(logged)
    return out


def line_for(cmd: str | None, exit_code: int, t0: float, start_wall: float,
             store: str | None, head: str | None, for_paths: Sequence[str] | None,
             top: Path | None = None) -> bytes:
    paths = log_for_paths(top, for_paths) if top is not None else (
        None if for_paths is None else [str(p) for p in for_paths]
    )
    obj = {
        "v": VERSION,
        "run": uuid.uuid4().hex,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(start_wall)),
        "cmd": cmd,
        "exit": int(exit_code),
        "store": store,
        "head": head,
        "ms": max(0, int((time.monotonic() - t0) * 1000)),
        "for": paths,
    }
    return _fit(obj)


def record(repo: Path, cmd: str | None, exit_code: int, t0: float, start_wall: float | None = None,
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
                        for_paths, top)
        fd = os.open(top / EVENTS_RELPATH, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            n = os.write(fd, data)
        finally:
            os.close(fd)
        if n != len(data):
            from rco.output import note
            note(f"events log short write: wrote {n} of {len(data)} bytes")
    except Exception as e:  # noqa: BLE001 - a log failure never breaks the command
        from rco.output import note
        note(f"events log not written: {type(e).__name__}: {e}")
