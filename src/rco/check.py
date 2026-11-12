"""Map checker (README Verification layer 2; tree-contract rco.check/1).

Re-extracts at SHA and compares with a given map. Every finding has a
path; findings are sorted, so output is byte-identical for the same input.
"""
from __future__ import annotations

import sys
import time
from collections import defaultdict
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal

from rco import build, canon
from rco.model import Kind, Map, Node, Tree, walk
from rco.output import Exit

SCHEMA_CHECK = "rco.check/1"
SCHEMA_SELFTEST = "rco.selftest/2"

Status = Literal["missing", "extra", "changed", "stale", "edge"]


@dataclass(frozen=True, slots=True)
class Finding:
    status: Status
    key: str
    path: str
    detail: str


def _shapes(t: Tree) -> dict[str, list[tuple[str, tuple]]]:
    out: dict[str, list[tuple[str, tuple]]] = defaultdict(list)
    for key, n in walk(t):
        out[key].append(n.shape())
    return {k: sorted(v) for k, v in out.items()}


def _path_of(key: str) -> str:
    return key.split(":", 2)[1]


def _fmt(shapes: list[tuple[str, tuple]]) -> str:
    return "; ".join(f"sig={s!r}" + (f" attrs={dict(a)!r}" if a else "") for s, a in shapes)


def compare(claimed: Map, actual: Map) -> tuple[Finding, ...]:
    """Every mismatch between a claimed map and a fresh one."""
    found: list[Finding] = []
    if claimed.sha != actual.sha:
        found.append(Finding("stale", "map:sha", "", f"map sha {claimed.sha} != {actual.sha}"))
    cm = {t.module: t for t in claimed.trees}
    am = {t.module: t for t in actual.trees}
    for mod in sorted(set(cm) | set(am)):
        c, a = cm.get(mod), am.get(mod)
        mkey = f"module:{mod}"
        if c is None:
            found.append(Finding("missing", mkey, mod, "module not in map"))
        elif a is None:
            found.append(Finding("extra", mkey, mod, "module not in code"))
        elif c.module_hash != a.module_hash:
            found.append(Finding("stale", mkey, mod, "module_hash"))
        cs = _shapes(c) if c else {}
        as_ = _shapes(a) if a else {}
        for key in sorted(set(cs) | set(as_)):
            if key not in as_:
                found.append(Finding("extra", key, _path_of(key), "claimed, not in code"))
            elif key not in cs:
                found.append(Finding("missing", key, _path_of(key), "in code, not in map"))
            elif cs[key] != as_[key]:
                found.append(Finding("changed", key, _path_of(key),
                                     f"{_fmt(cs[key])} -> {_fmt(as_[key])}"))
    return tuple(sorted(found, key=lambda f: (f.path, f.key, f.status)))


@dataclass(frozen=True, slots=True)
class CheckReport:
    sha: str
    map_sha: str
    findings: tuple[Finding, ...]

    def to_obj(self) -> dict:
        counts = {s: 0 for s in ("changed", "extra", "missing", "stale", "edge")}
        for f in self.findings:
            counts[f.status] += 1
        return {
            "schema": SCHEMA_CHECK, "sha": self.sha, "map_sha": self.map_sha,
            "counts": counts,
            "findings": [{"status": f.status, "key": f.key, "path": f.path, "detail": f.detail}
                         for f in self.findings],
        }

    def to_markdown(self) -> str:
        lines = [f"# rco check  (SHA={self.sha[:12]}, map={self.map_sha[:12]})", ""]
        if not self.findings:
            lines.append("map matches the code: no findings")
        for f in self.findings:
            lines.append(f"- {f.status}: `{f.key}` {f.detail}")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if self.findings else Exit.OK


def prose_name_findings(store, actual: Map) -> tuple[Finding, ...]:
    """Names cited in stored prose that are not in the module's Tree."""
    from rco.prose import flags as prose_flags
    found: list[Finding] = []
    for t in actual.trees:
        for name in prose_flags(store.get_prose(t.module), t):
            found.append(Finding("extra", f"prose:{t.module}:{name}", t.module,
                                 "cited in prose, not in the Tree"))
    return tuple(sorted(found, key=lambda f: (f.path, f.key, f.status)))


def check(repo: Path, sha: str, claimed: Map | None = None, store=None,
         claimed_forest=None) -> CheckReport:
    t0 = time.perf_counter()
    findings: tuple[Finding, ...] = ()
    map_sha = ""
    if claimed is not None:
        actual = build.build_map(repo, sha)
        findings = compare(claimed, actual)
        map_sha = claimed.sha
        if store is not None:
            findings = findings + prose_name_findings(store, actual)
    if claimed_forest is not None:
        from rco.forest import build_forest, compare_edges
        findings = findings + compare_edges(claimed_forest, build_forest(repo, sha))
        findings = tuple(sorted(findings, key=lambda f: (f.path, f.key, f.status)))
        if not map_sha:
            map_sha = claimed_forest.sha
    rep = CheckReport(sha=sha, map_sha=map_sha, findings=findings)
    print(f"rco check: {len(rep.findings)} findings in {time.perf_counter() - t0:.3f}s",
          file=sys.stderr)
    return rep


# ------------------------------------------------------------ planted drift

def _edit_first(nodes: tuple[Node, ...], pred, fn) -> tuple[tuple[Node, ...], bool]:
    """Apply fn to the first node (depth-first) matching pred; fn may return None to drop."""
    out = []
    done = False
    for n in nodes:
        if not done and pred(n):
            r = fn(n)
            done = True
            if r is not None:
                out.append(r)
            continue
        if not done and n.children:
            kids, done = _edit_first(n.children, pred, fn)
            n = replace(n, children=kids)
        out.append(n)
    return tuple(out), done


def _edit_map(m: Map, pred, fn) -> Map:
    trees = []
    done = False
    for t in m.trees:
        if not done:
            nodes, done = _edit_first(t.nodes, pred, fn)
            t = replace(t, nodes=nodes)
        trees.append(t)
    if not done:
        raise AssertionError("planted drift found no target")
    return replace(m, trees=tuple(trees))


def plants(m: Map) -> tuple[tuple[str, Status, Map], ...]:
    """The six planted drifts: (name, status the checker must report, map)."""
    k = lambda kind: (lambda n: n.kind is kind)  # noqa: E731
    first_mod = m.trees[0]
    added = Node(Kind.FUNCTION, "planted_extra", "", "() -> None")
    return (
        ("remove a flag", "missing", _edit_map(m, k(Kind.FLAG), lambda n: None)),
        ("rename a column", "extra",
         _edit_map(m, k(Kind.COLUMN), lambda n: replace(n, name=n.name + "_renamed"))),
        ("change a sig", "changed",
         _edit_map(m, k(Kind.FUNCTION), lambda n: replace(n, sig=n.sig + " # planted"))),
        ("add a function", "extra",
         _edit_map(m, k(Kind.PYMODULE),
                   lambda n: replace(n, children=n.children + (added,)))),
        ("drop a module", "missing", replace(m, trees=m.trees[1:])),
        ("alter a module_hash", "stale",
         replace(m, trees=(replace(first_mod, module_hash="sha256:" + "0" * 64),) + m.trees[1:])),
    )


@dataclass(frozen=True, slots=True)
class SelftestReport:
    rows: tuple[tuple[str, str, int, bool], ...]  # name, expected status, findings, caught

    def to_obj(self) -> dict:
        return {"schema": SCHEMA_SELFTEST,
                "plants": [{"name": n, "expect": s, "findings": c, "caught": ok}
                           for n, s, c, ok in self.rows]}

    def to_markdown(self) -> str:
        lines = ["# rco check --selftest", ""]
        for n, s, c, ok in self.rows:
            lines.append(f"- {'caught' if ok else 'MISSED'}: {n} (expect {s}, {c} findings)")
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK if self.rows and all(r[3] for r in self.rows) else Exit.FINDINGS


def selftest_on(m: Map) -> SelftestReport:
    if compare(m, m):
        raise AssertionError("an unchanged map must have no findings")
    rows = []
    for name, status, planted in plants(m):
        found = compare(planted, m)
        rows.append((name, status, len(found), any(f.status == status for f in found)))
    return SelftestReport(tuple(rows))


def selftest() -> SelftestReport:
    """Build the drift fixture's map in a scratch repo and plant six errors."""
    from rco import fixture
    t0 = time.perf_counter()
    with fixture.drift_repo() as (repo, sha):
        rep = selftest_on(build.build_map(repo, sha))
    print(f"rco check --selftest: {time.perf_counter() - t0:.3f}s", file=sys.stderr)
    return rep
