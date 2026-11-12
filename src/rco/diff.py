"""rco diff (docs/design/tree-contract.md, rco.diff/1). Step-1 stub; step 6 owns it."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rco.output import Exit, UsageError

SCHEMA_DIFF = "rco.diff/1"


@dataclass(frozen=True, slots=True)
class Change:
    op: Literal["added", "removed", "changed", "doc"]
    key: str
    kind: str
    path: str
    before: str
    after: str
    callers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DiffReport:
    base: str
    head: str
    changes: tuple[Change, ...] = field(default_factory=tuple)

    def to_obj(self) -> dict:
        return {
            "schema": SCHEMA_DIFF, "base": self.base, "head": self.head,
            "changes": [
                {"op": c.op, "key": c.key, "kind": c.kind, "path": c.path,
                 "before": c.before, "after": c.after, "callers": list(c.callers)}
                for c in self.changes
            ],
        }

    def to_markdown(self) -> str:
        lines = [f"# rco diff  (base={self.base[:12]}, head={self.head[:12]})", ""]
        for c in self.changes:
            lines.append(f"- {c.op}: `{c.key}`")
        if not self.changes:
            lines.append("no contract changes")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if any(c.op != "doc" for c in self.changes) else Exit.OK


def diff(repo: Path, base: str | None, head: str | None) -> DiffReport:
    raise UsageError("rco diff is not implemented yet (card 1167 step 6)")
