"""Output contract (docs/design/tree-contract.md, section 8; README Output contract).

stdout carries only the report. Progress, timings and notes go to stderr
(note, timed). --out FILE is written atomically: FILE.tmp in the same
directory, flushed and fsynced, then os.replace, so a reader sees the old
file or the new one, never a partial write. Exit codes:

  0 OK        clean
  1 FINDINGS  check mismatch, or diff found a contract change
  2 USAGE     bad args, bad rev, unreadable map, unwritable --out
  3 INTERNAL  uncaught exception (one stderr line; traceback only with RCO_DEBUG=1)
"""
from __future__ import annotations

import contextlib
import os
import sys
import time
from enum import IntEnum
from pathlib import Path
from typing import Iterator, Literal, Protocol

from rco.canon import canonical_json

FORMATS = ("markdown", "json")


class Exit(IntEnum):
    OK = 0
    FINDINGS = 1
    USAGE = 2
    INTERNAL = 3


class UsageError(Exception):
    """Bad arguments, a bad rev, an unreadable map or an unwritable --out: exit 2."""


class Report(Protocol):
    def to_obj(self) -> dict: ...

    def to_markdown(self) -> str: ...

    def exit_code(self) -> Exit: ...


def note(msg: str) -> None:
    """One line on stderr: progress, timings and notes never go to stdout."""
    print(f"rco: {msg}", file=sys.stderr, flush=True)


@contextlib.contextmanager
def timed(label: str) -> Iterator[None]:
    """Report the elapsed wall time of a block on stderr."""
    t0 = time.perf_counter()
    try:
        yield
    finally:
        note(f"{label} {time.perf_counter() - t0:.3f}s")


def render(report: Report, fmt: Literal["markdown", "json"]) -> bytes:
    """The report bytes: canonical JSON plus one newline, or markdown ending in one newline."""
    if fmt == "json":
        return canonical_json(report.to_obj()) + b"\n"
    if fmt == "markdown":
        md = report.to_markdown()
        return (md if md.endswith("\n") else md + "\n").encode("utf-8")
    raise UsageError(f"unknown format {fmt!r} (use markdown or json)")


def write_atomic(out: Path, data: bytes) -> None:
    """Write FILE.tmp beside FILE, fsync, then os.replace; never leave a partial FILE."""
    if out.is_dir():
        raise UsageError(f"--out {out} is a directory")
    if not out.parent.is_dir():
        raise UsageError(f"--out {out}: directory {out.parent} does not exist")
    tmp = out.with_name(out.name + ".tmp")
    try:
        with open(tmp, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, out)
    except PermissionError as e:
        tmp.unlink(missing_ok=True)
        raise UsageError(f"--out {out}: {e.strerror}") from None
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def emit(report: Report, fmt: Literal["markdown", "json"], out: Path | None) -> Exit:
    """Render the report to stdout (out=None) or atomically to out; return its exit code."""
    code = Exit(report.exit_code())
    data = render(report, fmt)
    if out is None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        write_atomic(Path(out), data)
        note(f"wrote {out} ({len(data)} bytes)")
    return code
