"""Pinned contracts (README roadmap, Phase 4): rco pin and rco drift.

A pin (rco.pin/1) is a reviewed contract a developer commits: the Tree
nodes of the pinned files at one SHA. It holds only those files, not
their whole package-grain module, and no module_hash, so an internal
edit never shows as drift.

rco drift re-extracts the pinned files at SHA (default HEAD) and compares
them with the pin by rco diff's comparison (model.walk keys and shapes):
added, removed, changed, and doc-only. Drift is a visible report, never a
block: exit 1 on contract drift, 0 when clean or doc-only (README Output
contract), and CI runs it report-only. Built on Phase 1 Trees only.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from rco import build, canon, diff, gitread
from rco.model import Map, Node, Tree
from rco.output import Exit, UsageError

SCHEMA_PIN = "rco.pin/1"
SCHEMA_DRIFT = "rco.drift/1"

_PIN_KEYS = frozenset({"schema", "sha", "paths", "nodes"})


@dataclass(frozen=True, slots=True)
class Pin:
    sha: str
    paths: tuple[str, ...]
    nodes: tuple[Node, ...]

    def to_obj(self) -> dict:
        return {"schema": SCHEMA_PIN, "sha": self.sha, "paths": list(self.paths),
                "nodes": [canon.node_to_obj(n) for n in self.nodes]}

    def to_markdown(self) -> str:
        from rco.render import map_markdown
        md = map_markdown(_as_map(self.sha, self.nodes))
        body = md.split("\n", 2)[2] if md.count("\n") >= 2 else ""
        head = f"# rco pin  (sha={self.sha[:12]}, files={len(self.paths)})\n\n"
        return head + "\n".join(f"- {p}" for p in self.paths) + "\n\n" + body

    def exit_code(self) -> Exit:
        return Exit.OK


def _as_map(sha: str, nodes: Sequence[Node]) -> Map:
    """One synthetic Tree; walk keys carry each top node's path, so the module id is moot."""
    return Map(sha=sha, trees=(Tree(module="pin", module_hash="", extractors=(), nodes=tuple(nodes)),))


def _pinned_nodes(repo: Path, sha: str, paths: Sequence[str]) -> tuple[Node, ...]:
    want = set(paths)
    m = build.build_map(repo, sha, sorted(want))
    return tuple(n for t in m.trees for n in t.nodes if n.path in want)


def make_pin(repo: Path, sha: str, paths: Sequence[str]) -> Pin:
    """The pin of paths at sha; every path must be a file at sha."""
    clean = sorted({p.strip("/") for p in paths})
    if not clean:
        raise UsageError("pin needs at least one --for FILE")
    files = {e.path for e in gitread.ls_tree(repo, sha) if e.mode != gitread.MODE_SUBMODULE}
    missing = [p for p in clean if p not in files]
    if missing:
        raise UsageError(f"not a file at {sha[:12]}: {', '.join(missing)}")
    return Pin(sha=sha, paths=tuple(clean), nodes=_pinned_nodes(repo, sha, clean))


def pin_from_obj(o: dict) -> Pin:
    if not isinstance(o, dict):
        raise ValueError("pin: expected an object")
    extra = set(o) - _PIN_KEYS
    if extra:
        raise ValueError(f"pin: unknown keys {sorted(extra)}")
    if o.get("schema") != SCHEMA_PIN:
        raise ValueError(f"pin: schema must be {SCHEMA_PIN}")
    return Pin(sha=str(o["sha"]), paths=tuple(str(p) for p in o["paths"]),
               nodes=tuple(canon.node_from_obj(n) for n in o["nodes"]))


def read_pin(path: Path) -> Pin:
    try:
        return pin_from_obj(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise UsageError(f"unreadable pin {path}: {e}") from None


def pin_files(args: Sequence[Path]) -> list[Path]:
    """Each PIN argument is a pin file or a directory of *.json pins (sorted)."""
    out: list[Path] = []
    for a in args:
        if a.is_dir():
            found = sorted(a.glob("*.json"))
            if not found:
                raise UsageError(f"no *.json pins in {a}")
            out.extend(found)
        else:
            out.append(a)
    return out


@dataclass(frozen=True, slots=True)
class PinDrift:
    pin: str
    pinned_sha: str
    paths: tuple[str, ...]
    changes: tuple[diff.Change, ...]


@dataclass(frozen=True, slots=True)
class DriftReport:
    sha: str
    pins: tuple[PinDrift, ...]

    def to_obj(self) -> dict:
        out = []
        for p in self.pins:
            counts = {op: 0 for op in diff.OPS}
            for c in p.changes:
                counts[c.op] += 1
            out.append({
                "pin": p.pin, "pinned_sha": p.pinned_sha, "paths": list(p.paths), "counts": counts,
                "changes": [{"op": c.op, "key": c.key, "kind": c.kind, "path": c.path,
                             "before": c.before, "after": c.after, "callers": list(c.callers)}
                            for c in p.changes],
            })
        return {"schema": SCHEMA_DRIFT, "sha": self.sha, "pins": out}

    def to_markdown(self) -> str:
        lines = [f"# rco drift  (sha={self.sha[:12]}, pins={len(self.pins)})"]
        for p in self.pins:
            n = sum(1 for c in p.changes if c.op != "doc")
            lines += ["", f"## {p.pin}  (pinned at {p.pinned_sha[:12]})"]
            if not p.changes:
                lines.append("no drift")
                continue
            lines.append(f"{n} contract drift(s), {len(p.changes) - n} doc-only")
            for c in p.changes:
                lines.append(f"- {c.op.upper()} {c.kind} `{diff._qn(c)}` ({c.path})")
                if c.before:
                    lines.append(f"  - pinned: `{diff._human(c.before)}`")
                if c.after:
                    lines.append(f"  - now: `{diff._human(c.after)}`")
                if c.callers:
                    lines.append(f"  - callers: {', '.join(c.callers)}")
            if n:
                lines.append(f"If the change is intended, review it and re-pin: "
                             f"`rco pin --for {' '.join(p.paths)} --format json --out {p.pin}`")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if any(c.op != "doc" for p in self.pins for c in p.changes) else Exit.OK


def drift(repo: Path, pins: Sequence[Path], sha: str) -> DriftReport:
    """Contract drift of every pin at sha; sha must be resolved."""
    he = gitread.ls_tree(repo, sha)
    out = []
    for f in pins:
        p = read_pin(f)
        now = _pinned_nodes(repo, sha, p.paths)
        rep = diff.diff_maps(_as_map(p.sha, p.nodes), _as_map(sha, now),
                             diff._python_files(repo, he))
        out.append(PinDrift(str(f), p.sha, p.paths, rep.changes))
    return DriftReport(sha=sha, pins=tuple(out))
