"""rco metrics bypass (card 1317): code commits with no `rco pack` run before them.

The events file (card 1314) says when each `rco pack` ran and at which HEAD;
git says when each code commit was made. A commit is covered when a pack run
finished with exit 0 or 1, at a HEAD that is the commit's parent or an
ancestor of it, and finished (start plus duration) no later than the commit and
at most the window before it.
That shows a pack RAN before the commit, not that the agent read it.

The report holds counts and ids, never a clock reading, so the same events
and history give the same bytes.
"""
from __future__ import annotations

import calendar
import json
import time
from dataclasses import dataclass
from pathlib import Path

from rco import affected, gitread, update
from rco.output import Exit, UsageError

SCHEMA_BYPASS = "rco.bypass/1"
NOT_READ = "pack ran before the commit; reading it is not checked"


@dataclass(frozen=True)
class Event:
    run: str
    ts: int  # UTC epoch seconds
    cmd: str
    exit: int
    head: str | None
    ms: int = 0  # how long the run took

    @property
    def end(self) -> float:
        return self.ts + self.ms / 1000


@dataclass(frozen=True)
class Commit:
    sha: str
    parent: str | None
    ts: int
    files: tuple[str, ...]  # code paths the commit changed


@dataclass(frozen=True)
class Checked:
    sha: str
    files: int
    covered: bool
    run: str | None


def _event(obj: object) -> Event | None:
    if not isinstance(obj, dict):
        return None
    run, ts, cmd, code, head = (obj.get(k) for k in ("run", "ts", "cmd", "exit", "head"))
    if not (isinstance(run, str) and isinstance(ts, str) and isinstance(cmd, str)
            and isinstance(code, int) and not isinstance(code, bool)
            and (head is None or isinstance(head, str))):
        return None
    try:
        epoch = calendar.timegm(time.strptime(ts, "%Y-%m-%dT%H:%M:%SZ"))
    except ValueError:
        return None
    ms = obj.get("ms")
    dur = ms if isinstance(ms, int) and not isinstance(ms, bool) and ms > 0 else 0
    return Event(run, epoch, cmd, code, head, dur)


def read_events(path: Path) -> tuple[list[Event], int]:
    """(events, bad_lines). A line that does not parse as an event is counted, not fatal."""
    try:
        raw = Path(path).read_bytes()
    except OSError:
        raise UsageError("no events file; events are per machine, collect them first "
                         f"(looked at {path})") from None
    events: list[Event] = []
    bad = 0
    for line in raw.split(b"\n"):
        if not line.strip():
            continue
        try:
            ev = _event(json.loads(line))
        except ValueError:
            ev = None
        if ev is None:
            bad += 1
        else:
            events.append(ev)
    if not events:
        why = f" ({bad} unreadable lines)" if bad else ""
        raise UsageError(f"no events in the events file{why}; events are per machine, "
                         f"collect them first (looked at {path})")
    return events, bad


def code_commits(repo: Path, base: str | None, days: int) -> list[Commit]:
    """Non-merge commits that changed a code file, oldest first.

    base set: BASE..HEAD. base unset: commits from the last `days` days.
    """
    if not gitread.has_commit(repo, "HEAD"):
        return []
    since = int(time.time()) - days * 86400  # filtered here: git reads small epochs as dates
    span = [f"{gitread.resolve(repo, base)}..HEAD"] if base else ["HEAD"]
    out = gitread._git(repo, "log", "--no-merges", "--reverse", "--format=%H %P %ct",
                       *span).decode("utf-8", "replace")
    commits: list[Commit] = []
    for row in out.splitlines():
        parts = row.split()
        sha, ts, parents = parts[0], int(parts[-1]), parts[1:-1]
        if not base and ts < since:
            continue
        raw = gitread._git(repo, "diff-tree", "--root", "--no-commit-id", "--name-only", "-r",
                           "-z", "--no-renames", sha)
        paths = [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]
        code = tuple(p for p in paths if affected.is_code(p))
        if code:
            commits.append(Commit(sha, parents[0] if parents else None, ts, code))
    return commits


@dataclass(frozen=True)
class BypassReport:
    window_s: int
    pack_events: int
    bad_lines: int
    commits: tuple[Checked, ...]
    strict: bool = False

    @property
    def covered(self) -> int:
        return sum(1 for c in self.commits if c.covered)

    @property
    def uncovered(self) -> int:
        return len(self.commits) - self.covered

    def to_obj(self) -> dict:
        return {"schema": SCHEMA_BYPASS, "window_s": self.window_s, "pack_events": self.pack_events,
                "bad_lines": self.bad_lines, "covered": self.covered,
                "uncovered": self.uncovered,
                "commits": [{"sha": c.sha, "files": c.files, "covered": c.covered, "run": c.run}
                            for c in self.commits]}

    def to_markdown(self) -> str:
        lines = ["# rco metrics bypass", "",
                 f"- code commits: {len(self.commits)} "
                 f"({self.covered} covered, {self.uncovered} uncovered)",
                 f"- window: {self.window_s // 60} min before each commit",
                 f"- pack runs read: {self.pack_events} ({self.bad_lines} unreadable lines)"]
        bare = [c.sha[:7] for c in self.commits if not c.covered]
        if bare:
            lines += ["", "Uncovered commits:", *[f"- {s}" for s in bare]]
        return "\n".join([*lines, "", NOT_READ]) + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if self.strict and self.uncovered else Exit.OK


def bypass(repo: Path, events: list[Event], commits: list[Commit], window_s: int,
           bad_lines: int = 0, strict: bool = False) -> BypassReport:
    packs = sorted((e for e in events if e.cmd == "pack" and e.exit in (0, 1)),
                   key=lambda e: (e.ts, e.run))
    ancestor: dict[tuple[str, str], bool] = {}

    def below(head: str, parent: str) -> bool:
        key = (head, parent)
        if key not in ancestor:
            ancestor[key] = update.is_ancestor(repo, head, parent)
        return ancestor[key]

    checked: list[Checked] = []
    for c in commits:
        run = None
        if c.parent:
            for e in reversed(packs):  # the closest earlier run wins
                if e.head and 0 <= c.ts - e.end <= window_s and below(e.head, c.parent):
                    run = e.run
                    break
        checked.append(Checked(c.sha, len(c.files), run is not None, run))
    return BypassReport(window_s, sum(1 for e in events if e.cmd == "pack"), bad_lines, tuple(checked), strict)
