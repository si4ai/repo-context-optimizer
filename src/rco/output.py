"""Output contract (docs/design/tree-contract.md, section 8).

Step 1 ships the minimal form; step 5 owns this module.
"""
from __future__ import annotations

import os
import sys
from enum import IntEnum
from pathlib import Path
from typing import Literal, Protocol

from rco.canon import canonical_json


class Exit(IntEnum):
    OK = 0
    FINDINGS = 1
    USAGE = 2
    INTERNAL = 3


class UsageError(Exception):
    """Bad arguments, a bad rev, or an unreadable map: exit 2."""


class Report(Protocol):
    def to_obj(self) -> dict: ...

    def to_markdown(self) -> str: ...

    def exit_code(self) -> Exit: ...


def render(report: Report, fmt: Literal["markdown", "json"]) -> bytes:
    if fmt == "json":
        return canonical_json(report.to_obj()) + b"\n"
    if fmt == "markdown":
        return report.to_markdown().encode("utf-8")
    raise UsageError(f"unknown format {fmt!r}")


def emit(report: Report, fmt: Literal["markdown", "json"], out: Path | None) -> Exit:
    data = render(report, fmt)
    if out is None:
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()
    else:
        tmp = out.with_name(out.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, out)
    return report.exit_code()
