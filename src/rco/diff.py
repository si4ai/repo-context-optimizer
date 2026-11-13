"""rco diff [BASE] [HEAD] (README `rco diff`; tree-contract rco.diff/1).

Builds the Trees of the modules whose files differ between BASE and HEAD
(path, mode or blob), and compares them key by key (model.walk keys):

- added    the key is only at HEAD
- removed  the key is only at BASE
- changed  the key is on both sides and its shapes (sig, attrs) differ
- doc      the shapes are equal and only the docstring line differs; it
           never sets exit 1

FILE nodes are the outline fallback, not a contract, so they are not
diffed; a file that stops parsing shows up as its removed members. Two
nodes that share a key are compared as a sorted list of shapes.

Callers are `file:line` at HEAD from a Python ast index of Name (load)
and Attribute references to the changed name (the last qualname part).
Phase 1 indexes Python names only, so CLI, SQL and route changes have no
callers. Line numbers live only here, never in a Tree.

Output is deterministic: changes sorted by (path, key, op), callers sorted.
"""
from __future__ import annotations

import ast
import json
import sys
import time
import warnings
from collections import defaultdict
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Iterator, Literal, Mapping

from rco import build, canon, gitread
from rco.model import Kind, Map, Node, walk
from rco.output import Exit, UsageError

SCHEMA_DIFF = "rco.diff/1"

Op = Literal["added", "removed", "changed", "doc"]
OPS: tuple[str, ...] = ("added", "removed", "changed", "doc")

# Kinds whose callers come from the Python name index.
_PY_KINDS = frozenset({Kind.CLASS, Kind.FUNCTION, Kind.METHOD, Kind.FIELD, Kind.CONSTANT})


@dataclass(frozen=True, slots=True)
class Change:
    op: Op
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
        counts = {op: 0 for op in OPS}
        for c in self.changes:
            counts[c.op] += 1
        return {
            "schema": SCHEMA_DIFF, "base": self.base, "head": self.head, "counts": counts,
            "changes": [
                {"op": c.op, "key": c.key, "kind": c.kind, "path": c.path,
                 "before": c.before, "after": c.after, "callers": list(c.callers)}
                for c in self.changes
            ],
        }

    def to_markdown(self) -> str:
        n = sum(1 for c in self.changes if c.op != "doc")
        docs = len(self.changes) - n
        lines = [f"# rco diff  (base={self.base[:12]}, head={self.head[:12]})", ""]
        if not self.changes:
            lines.append("no contract changes")
            return "\n".join(lines) + "\n"
        lines.append(f"{n} contract change(s), {docs} doc-only")
        path = None
        for c in self.changes:
            if c.path != path:
                path = c.path
                lines += ["", f"## {path}"]
            lines.append(f"- {c.op.upper()} {c.kind} `{_qn(c)}`")
            if c.before:
                lines.append(f"  - before: `{_human(c.before)}`")
            if c.after:
                lines.append(f"  - after: `{_human(c.after)}`")
            if c.callers:
                lines.append(f"  - callers: {', '.join(c.callers)}")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if any(c.op != "doc" for c in self.changes) else Exit.OK


def _qn(c: Change) -> str:
    return c.key[len(c.kind) + 1 + len(c.path) + 1:]


def _human(js: str) -> str:
    """A shape or doc JSON string as one readable line (markdown only)."""
    o = json.loads(js)
    items = o if isinstance(o, list) else [o]
    out = []
    for it in items:
        if "doc" in it:
            out.append(it["doc"] or "(none)")
            continue
        s = it.get("sig", "")
        attrs = it.get("attrs", {})
        if attrs:
            s += (" " if s else "") + "[" + ", ".join(f"{k}={v}" for k, v in sorted(attrs.items())) + "]"
        out.append(s or "-")
    return " | ".join(out)


# ------------------------------------------------------------ comparison

@dataclass(frozen=True, slots=True)
class _Entry:
    kind: Kind
    path: str
    name: str                  # last qualname part
    qn: str                    # qualname within the file, for qualified callers
    shapes: tuple[str, ...]    # sorted canonical JSON of each node's shape
    docs: tuple[str, ...]      # sorted doc lines


def _shape_json(n: Node) -> str:
    return canon.canonical_json({"sig": n.sig, "attrs": dict(n.attrs)}).decode("utf-8")


def _index(m: Map) -> dict[str, _Entry]:
    acc: dict[str, list[Node]] = defaultdict(list)
    meta: dict[str, tuple[Kind, str, str]] = {}
    for t in m.trees:
        for top in t.nodes:
            prefix_len = len(top.path) + 1
            for key, n in walk(replace(t, nodes=(top,))):
                if n.kind is Kind.FILE:
                    continue
                acc[key].append(n)
                qn = key[len(n.kind.value) + 1 + prefix_len:]
                meta[key] = (n.kind, top.path, qn)
    return {k: _Entry(meta[k][0], meta[k][1], meta[k][2].rsplit(".", 1)[-1], meta[k][2],
                      tuple(sorted(_shape_json(n) for n in ns)),
                      tuple(sorted(n.doc for n in ns)))
            for k, ns in acc.items()}


def _join(items: tuple[str, ...]) -> str:
    return items[0] if len(items) == 1 else "[" + ",".join(items) + "]"


def _docs_json(docs: tuple[str, ...]) -> str:
    return _join(tuple(canon.canonical_json({"doc": d}).decode("utf-8") for d in docs))


def compare(base: Map, head: Map) -> tuple[tuple[str, Op, _Entry, _Entry | None], ...]:
    """(key, op, entry, base entry or None) for every difference, unsorted."""
    b, h = _index(base), _index(head)
    out = []
    for key in set(b) | set(h):
        be, he = b.get(key), h.get(key)
        if be is None:
            out.append((key, "added", he, None))
        elif he is None:
            out.append((key, "removed", be, be))
        elif be.shapes != he.shapes:
            out.append((key, "changed", he, be))
        elif be.docs != he.docs:
            out.append((key, "doc", he, be))
    return tuple(out)


# ------------------------------------------------------------ callers

def code_sources(repo: Path, entries: tuple[gitread.Entry, ...]) -> dict[str, bytes]:
    """path -> bytes of every code file (Python and the bundled languages) plus manifests."""
    from rco.edges import MANIFEST_NAMES
    from rco.extract.langs import lang_of
    ents = [e for e in entries if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)
            and (e.path.endswith(".py") or lang_of(e.path)
                 or e.path.rsplit("/", 1)[-1] in MANIFEST_NAMES)]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return {e.path: data[e.blob] for e in ents}


def callers_of(changes: Iterable[tuple[str, _Entry]], head: Map, sources: Mapping[str, bytes],
               in_files: Iterable[str] | None = None) -> dict[str, tuple[str, ...]]:
    """key -> qualified "file:line" references at HEAD (rco.refs)."""
    from rco import refs
    want = {key: refs.Target(e.path, e.qn) for key, e in changes if e.kind in _PY_KINDS}
    found = refs.references(set(want.values()), head, sources, in_files)
    return {key: found.get(t, ()) for key, t in want.items()}


# ------------------------------------------------------------ entry point

def changed_paths(a: tuple[gitread.Entry, ...], b: tuple[gitread.Entry, ...]) -> list[str]:
    sa = {(e.path, e.mode, e.blob) for e in a}
    sb = {(e.path, e.mode, e.blob) for e in b}
    return sorted({p for p, _, _ in sa ^ sb})


def diff_maps(base: Map, head: Map, head_sources: Mapping[str, bytes] | None = None) -> DiffReport:
    """Diff two Maps; head_sources (path -> bytes of code files at HEAD) give callers."""
    found = compare(base, head)
    callers = callers_of(((k, e) for k, op, e, _ in found if op != "doc"), head,
                         head_sources or {})
    changes = []
    for key, op, e, be in found:
        if op == "added":
            before, after = "", _join(e.shapes)
        elif op == "removed":
            before, after = _join(e.shapes), ""
        elif op == "changed":
            before, after = _join(be.shapes), _join(e.shapes)
        else:
            before, after = _docs_json(be.docs), _docs_json(e.docs)
        cs = callers.get(key, ()) if op != "doc" else ()
        changes.append(Change(op, key, e.kind.value, e.path, before, after, cs))
    changes.sort(key=lambda c: (c.path, c.key, c.op))
    return DiffReport(base=base.sha, head=head.sha, changes=tuple(changes))


def resolve_range(repo: Path, base: str | None, head: str | None,
                  snapshot: str | None = None) -> tuple[str, str]:
    """(base, head) SHAs. HEAD defaults to HEAD; BASE to merge-base(default branch, HEAD).

    With snapshot "staged" or "worktree", HEAD is that snapshot
    (gitread.snapshot) and BASE defaults to HEAD, like `git diff --cached`
    and `git diff HEAD`.
    """
    if snapshot is not None:
        if head is not None:
            raise UsageError(f"--{snapshot} takes at most one commit (BASE)")
        head_sha = gitread.snapshot(repo, snapshot)
        base = base or "HEAD"
    else:
        head_sha = gitread.resolve(repo, head or "HEAD")
    if base is None:
        return gitread.merge_base(repo, gitread.resolve(repo, gitread.default_branch(repo)),
                                  head_sha), head_sha
    return gitread.resolve(repo, base), head_sha


def diff(repo: Path, base: str | None, head: str | None,
         snapshot: str | None = None) -> DiffReport:
    """Contract changes from BASE to HEAD.

    HEAD defaults to HEAD; BASE to merge-base(default branch, HEAD). With
    snapshot "staged" or "worktree", HEAD is that snapshot
    (gitread.snapshot) and BASE defaults to HEAD, like `git diff --cached`
    and `git diff HEAD`.
    """
    t0 = time.perf_counter()
    base_sha, head_sha = resolve_range(repo, base, head, snapshot)
    be, he = gitread.ls_tree(repo, base_sha), gitread.ls_tree(repo, head_sha)
    paths = changed_paths(be, he)
    bm = build.build_map(repo, base_sha, paths)
    hm = build.build_map(repo, head_sha, paths)
    report = diff_maps(bm, hm, code_sources(repo, he))
    print(f"rco diff: {len(paths)} changed files, {len(report.changes)} changes "
          f"in {time.perf_counter() - t0:.3f}s", file=sys.stderr)
    return report
