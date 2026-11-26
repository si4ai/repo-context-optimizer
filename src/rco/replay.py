"""`rco replay`: a before/after estimate on your own repo, in one command (card 1319).

It replays the repo's last N commits that changed code. Each commit's diff
stands in for an agent's first useful edit, and the report compares what an
agent would read for that edit:

- without rco: the changed code files plus the in-repo files they import,
  one hop (metrics.read_set). A lower bound: real agents also search.
- with rco: the `rco pack --for CHANGED` text PLUS the changed files, since
  the agent still opens them to edit. (metrics.measure counts the pack
  alone on its "with" side; replay does not reuse it, because that flatters
  rco.)

Files and the pack are read at the commit itself, as `rco metrics measure`
reads the head of a pull request. The pack is built from git (no store), so
replay needs no `rco update` and writes nothing. This is an estimate, not a
measurement of an agent: the pack is aimed at exactly the files the commit
changed (an oracle), and "file reads" stand in for tool calls.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from rco import __version__, gitread
from rco.affected import is_code
from rco.diff import changed_paths
from rco.imports import file_graph
from rco.metrics import _regular, read_set
from rco.output import Exit, UsageError, timed

SCHEMA = "rco.replay/1"
HEADING_METHOD = "## How this is estimated (method and limits)"
INSTALL_HINT = 'pip install "repo-context-optimizer[tokens]"'


def assumptions(tokenizer: str, is_estimate: bool) -> list[str]:
    tok = (f"tokens are chars/4 (estimate); install tiktoken for real counts: {INSTALL_HINT}"
           if is_estimate else f"tokens are counted with {tokenizer}")
    return [
        "an estimate from replaying commits, not a measurement of an agent",
        "each commit's diff stands in for the agent's first useful edit",
        "without rco: the changed code files plus the files they import, one hop "
        "(a lower bound: real agents also search)",
        "with rco: the rco pack for the changed files plus the changed files, "
        "which the agent still opens to edit",
        "oracle: the pack targets exactly the files the commit changed; "
        "a real agent must guess them",
        "file reads stand in for tool calls (with rco: 1 pack call plus the changed files)",
        "files and pack are read at the commit itself, built from git with no store",
        tok,
    ]


@dataclass(frozen=True)
class ReplayReport:
    obj: dict

    def to_obj(self) -> dict:
        return self.obj

    def exit_code(self) -> Exit:
        return Exit.OK

    def to_markdown(self) -> str:
        o, s = self.obj, self.obj["summary"]
        tok = "tokens (chars/4 estimate)" if o["tokenizer_is_estimate"] else "tokens"
        out = [f"# rco replay  (HEAD={o['head'][:7]}, last {s['n']} code commits)", ""]
        wins = s["median_with_tokens"] < s["median_without_tokens"]
        if wins:
            out.append(f"{tok.capitalize()} per commit, median: {s['median_without_tokens']} "
                       f"without rco, {s['median_with_tokens']} with rco "
                       f"({s['saved_pct']}% fewer).")
        else:
            out.append(f"No saving: median {tok} per commit is {s['median_with_tokens']} with rco "
                       f"and {s['median_without_tokens']} without, so rco does not save tokens "
                       f"on these commits by this estimate.")
        out.append(f"File reads per commit, median: {s['median_without_files']} without rco, "
                   f"{s['median_with_files']} with rco.")
        out.append(f"The pack cost more than the imported files it replaced in "
                   f"{s['worse_commits']} of {s['n']} commits.")
        if o["skipped"]:
            out.append(f"Not replayed: {o['skipped']} commit(s), a root commit or more changed "
                       f"code files than --max-files.")
        out += ["", HEADING_METHOD]
        out += [f"- {a}" for a in o["assumptions"]]
        out += ["", "## Per commit", "",
                "| commit | changed | without files | without tokens | with files "
                "| with tokens | pack tokens |",
                "|---|---|---|---|---|---|---|"]
        for c in o["commits"]:
            w, r = c["without"], c["with"]
            out.append(f"| {c['sha'][:7]} | {c['changed']} | {w['files']} | {w['tokens']} "
                       f"| {r['files']} | {r['tokens']} | {r['pack_tokens']} |")
        return "\n".join(out) + "\n"


def _code_commits(repo: Path, last: int) -> list[tuple[str, str | None]]:
    """(sha, first parent or None) of the newest `last` non-merge commits touching code."""
    out = gitread._git(repo, "log", "--no-merges", "--format=%H %P", "HEAD")
    found: list[tuple[str, str | None]] = []
    for line in out.decode().splitlines():
        if len(found) >= last:
            break
        parts = line.split()
        sha, parent = parts[0], (parts[1] if len(parts) > 1 else None)
        if parent is None:
            files = gitread._git(repo, "ls-tree", "-r", "--name-only", "-z", sha).split(b"\0")
        else:
            files = gitread._git(repo, "diff-tree", "--no-commit-id", "--name-only", "-r",
                                 "-z", "--no-renames", parent, sha).split(b"\0")
        if any(is_code(f.decode("utf-8", "surrogateescape")) for f in files if f):
            found.append((sha, parent))
    return found


def _median(xs: list[int]) -> float | int:
    m = statistics.median(xs)
    return int(m) if float(m).is_integer() else m


def _one(repo: Path, sha: str, parent: str, budget: int,
         count: Callable[[str], int]) -> dict | None:
    from rco.pack import pack, pack_markdown
    head_entries = gitread.ls_tree(repo, sha)
    tree = _regular(head_entries)
    changed = [p for p in changed_paths(gitread.ls_tree(repo, parent), head_entries)
               if p in tree and is_code(p)]
    if not changed:
        return None  # only deletions of code: nothing left to read
    code_paths = [p for p in tree if is_code(p)]
    raw = gitread.read_blobs(repo, (tree[p] for p in code_paths))
    code = {p: raw[tree[p]] for p in code_paths}
    graph = file_graph(sorted(tree), code)
    reads = [p for p in read_set(changed, graph) if p in tree]
    blobs_raw = gitread.read_blobs(repo, (tree[p] for p in reads))
    blobs = {p: blobs_raw[tree[p]] for p in reads}

    def text(paths: list[str]) -> str:
        return "".join(blobs[p].decode("utf-8", "replace") for p in paths)

    pack_text = pack_markdown(pack(repo, sha, changed, budget), " ".join(changed))
    without = count(text(reads))
    pack_tokens = count(pack_text)
    with_tokens = pack_tokens + count(text(changed))
    return {"sha": sha, "changed": len(changed),
            "without": {"files": len(reads), "tokens": without},
            "with": {"files": 1 + len(changed), "tokens": with_tokens,
                     "pack_tokens": pack_tokens}}


def replay(repo: Path, last: int, budget: int, max_files: int,
           count: Callable[[str], int], tokenizer: str = "estimate chars/4") -> ReplayReport:
    if last < 1:
        raise UsageError("--last must be at least 1")
    if not gitread.has_commit(repo):
        raise UsageError("no commits to replay")
    head = gitread.resolve(repo, "HEAD")
    commits, skipped = [], 0
    with timed("replay"):
        for sha, parent in _code_commits(repo, last):
            if parent is None:
                skipped += 1
                continue
            n_changed = sum(1 for p in changed_paths(gitread.ls_tree(repo, parent),
                                                     gitread.ls_tree(repo, sha)) if is_code(p))
            if n_changed > max_files:
                skipped += 1
                continue
            row = _one(repo, sha, parent, budget, count)
            if row is None:
                skipped += 1
                continue
            commits.append(row)
    if not commits:
        raise UsageError(f"no code commit to replay in the last {last} "
                         f"({skipped} skipped: root commits or over --max-files)")
    is_est = tokenizer.startswith("estimate")
    wo = [c["without"]["tokens"] for c in commits]
    wi = [c["with"]["tokens"] for c in commits]
    med_wo, med_wi = _median(wo), _median(wi)
    summary = {
        "n": len(commits),
        "median_without_tokens": med_wo,
        "median_with_tokens": med_wi,
        "median_without_files": _median([c["without"]["files"] for c in commits]),
        "median_with_files": _median([c["with"]["files"] for c in commits]),
        "saved_pct": round(100 * (med_wo - med_wi) / med_wo, 1) if med_wo else 0.0,
        "worse_commits": sum(1 for a, b in zip(wo, wi) if b > a),
    }
    return ReplayReport({
        "schema": SCHEMA,
        "rco_version": __version__,
        "head": head,
        "tokenizer": "chars/4 (estimate)" if is_est else tokenizer,
        "tokenizer_is_estimate": is_est,
        "assumptions": assumptions(tokenizer, is_est),
        "skipped": skipped,
        "commits": commits,
        "summary": summary,
    })
