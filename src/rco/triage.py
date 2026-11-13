"""rco triage and rco notice (README `rco triage`, Phase 5; card 1189).

Hints only. Neither command rebases, merges, pushes or writes a ref; both
read the git object store through gitread and rco diff.

triage BRANCH... --onto TARGET
    Upstream changes are the contract diff merge-base(TARGET, BRANCH) ->
    TARGET. A branch is clean when it changed no file TARGET also changed
    and none of its changed Python files references a changed name.
    Otherwise it gets a structural brief. Exit 1 when any brief is emitted.

notice OTHER... [--base BASE] [--head HEAD]
    The cross-PR contract notice: this PR's contract diff (rco diff BASE
    HEAD) and, per other open branch, the files it changed that use a
    changed name. It names branches (PRs), never desks: mapping a PR to a
    desk needs a per-desk GitHub identity (see the mail backend in the README).
    Always exit 0, so a CI job running it stays a visible hint, not a block.

References come from the rco diff caller index (Python names only), so a
CLI, SQL or route change is seen through file overlap, not callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from rco import diff, gitread
from rco.output import Exit, UsageError

SCHEMA_TRIAGE = "rco.triage/1"
SCHEMA_NOTICE = "rco.notice/1"
CLEAN = "clean: no changed file, no changed contract used"


@dataclass(frozen=True, slots=True)
class BranchHint:
    branch: str
    sha: str
    base: str
    overlap: tuple[str, ...]                          # files both sides changed
    impacted: tuple[tuple[str, tuple[str, ...]], ...]  # (file, names it references)
    changes: tuple[diff.Change, ...]                   # contract changes (no doc-only)

    @property
    def clean(self) -> bool:
        return not self.overlap and not self.impacted

    def to_obj(self) -> dict:
        return {
            "branch": self.branch, "sha": self.sha, "base": self.base, "clean": self.clean,
            "overlap": list(self.overlap),
            "impacted": [{"file": f, "names": list(n)} for f, n in self.impacted],
            "changes": [{"op": c.op, "key": c.key, "kind": c.kind, "path": c.path,
                         "before": c.before, "after": c.after} for c in self.changes],
        }

    def brief(self, upstream: str) -> list[str]:
        if self.clean:
            return [f"- {CLEAN}"]
        out = ["### STRUCTURAL UPDATE REQUIRED", f"contracts changed on {upstream}."]
        if self.changes:
            out += ["", "#### contract changes (upstream):"]
            for c in self.changes:
                sign = {"added": "+ ADDED", "removed": "- REMOVED"}.get(c.op, "~ CHANGED")
                after = c.after or c.before
                out.append(f"{sign}: `{diff._qn(c)}` `{diff._human(after)}` ({c.path})")
        if self.overlap:
            out += ["", "#### files changed on both sides:"]
            out += [f"- `{p}`" for p in self.overlap]
        if self.impacted:
            out += ["", "#### impacted files in your branch:"]
            out += [f"- `{f}` (calls {', '.join(f'`{n}`' for n in names)})"
                    for f, names in self.impacted]
        out += ["", "#### task:", "update the impacted files to the new contracts.",
                "do not re-read or modify unaffected modules."]
        return out


@dataclass(frozen=True, slots=True)
class TriageReport:
    schema: str
    target: str
    branches: tuple[BranchHint, ...]

    def to_obj(self) -> dict:
        return {"schema": self.schema, "target": self.target,
                "branches": [b.to_obj() for b in self.branches]}

    def to_markdown(self) -> str:
        title = "rco triage" if self.schema == SCHEMA_TRIAGE else "rco notice"
        lines = [f"# {title}  (target={self.target[:12]}, hint only: nothing is rebased or pushed)"]
        for b in self.branches:
            lines += ["", f"## {b.branch} ({b.sha[:12]})", *b.brief(self.target[:12])]
        return "\n".join(lines) + "\n"

    def exit_code(self) -> Exit:
        if self.schema == SCHEMA_NOTICE:
            return Exit.OK
        return Exit.OK if all(b.clean for b in self.branches) else Exit.FINDINGS


def _changed(repo: Path, a: str, b: str) -> tuple[list[str], tuple[gitread.Entry, ...]]:
    eb = gitread.ls_tree(repo, b)
    return diff.changed_paths(gitread.ls_tree(repo, a), eb), eb


def _name(c: diff.Change) -> str:
    return diff._qn(c).rsplit(".", 1)[-1]


_PY = frozenset(k.value for k in diff._PY_KINDS)


def _hint(repo: Path, branch: str, base: str, upstream: tuple[diff.Change, ...],
          upstream_paths: set[str]) -> BranchHint:
    sha = gitread.resolve(repo, branch)
    mine, entries = _changed(repo, base, sha)
    contract = tuple(c for c in upstream if c.op != "doc")
    names = {_name(c) for c in contract if c.kind in _PY}
    files = [(p, d) for p, d in diff.code_sources(repo, entries).items()
             if p in set(mine) and p.endswith(".py")]
    idx = diff.caller_index_bare(files, names)
    by_file: dict[str, set[str]] = {}
    for n, locs in idx.items():
        for loc in locs:
            by_file.setdefault(loc.rsplit(":", 1)[0], set()).add(n)
    impacted = tuple((f, tuple(sorted(ns))) for f, ns in sorted(by_file.items()))
    overlap = tuple(sorted(set(mine) & upstream_paths))
    return BranchHint(branch, sha, base, overlap, impacted, contract)


def _need(branches: Sequence[str]) -> None:
    if not branches:
        raise UsageError("triage needs at least one BRANCH")


def triage(repo: Path, branches: Sequence[str], onto: str | None = None) -> TriageReport:
    _need(branches)
    target = gitread.resolve(repo, onto or gitread.default_branch(repo))
    hints = []
    for br in branches:
        base = gitread.merge_base(repo, target, gitread.resolve(repo, br))
        up = diff.diff(repo, base, target)
        up_paths, _ = _changed(repo, base, target)
        hints.append(_hint(repo, br, base, up.changes, set(up_paths)))
    return TriageReport(SCHEMA_TRIAGE, target, tuple(hints))


def notice(repo: Path, base: str | None, head: str | None, others: Sequence[str]) -> TriageReport:
    _need(others)
    mine = diff.diff(repo, base, head)
    head_sha = gitread.resolve(repo, mine.head)
    paths, _ = _changed(repo, mine.base, head_sha)
    hints = []
    for br in others:
        b = gitread.merge_base(repo, head_sha, gitread.resolve(repo, br))
        hints.append(_hint(repo, br, b, mine.changes, set(paths)))
    return TriageReport(SCHEMA_NOTICE, head_sha, tuple(hints))
