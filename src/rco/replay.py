"""`rco replay`: a before/after estimate on your own repo, in one command (card 1319).

It replays the repo's last N commits that changed code. Each commit's diff
stands in for an agent's first useful edit, and the report compares what an
agent would read for that edit:

- without rco: the changed code files plus the in-repo files they import,
  one hop, plus importers when a public signature changed (meter.read_set). A lower bound: real agents also search.
- with rco: the `rco pack --for CHANGED` text PLUS the changed files, since
  the agent still opens them to edit. The pack outlines only the files the
  changed files import, so each changed file is counted once. When pack
  skips (nothing worth outlining), the agent sends nothing and reads the
  changed files only: a tie. metrics.measure uses the same definition (cards 1351 and 1359).

Files and the pack are read at the commit itself, as `rco metrics measure`
reads the head of a pull request. The pack is built from git (no store), so
replay needs no `rco update` and writes nothing. This is an estimate, not a
measurement of an agent: the pack is aimed at exactly the files the commit
changed (an oracle), and "file reads" stand in for tool calls.

For speed (card 1344), per-file work (extracted nodes, import statements) is
cached by path and bytes for the whole run (rco.memo), since most files are
the same blob from one commit to the next.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass
from pathlib import Path
from contextlib import contextmanager
from typing import Callable, Iterator

from rco import __version__, gitread, memo
from rco.affected import is_code
from rco.edges import MANIFEST_NAMES
from rco.diff import changed_paths
from rco.imports import file_graph
from rco.metrics import _regular
from rco.meter import (arms, call_site_in_pack, call_sites, changed_names,
                       interface_changed, merge_graphs, read_set)
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
        "which the agent still opens to edit; the pack outlines only the files "
        "they import, so each changed file is counted once",
        "when the pack skips (nothing worth outlining), nothing is sent: "
        "with rco equals without rco for that commit",
        "oracle: the pack targets exactly the files the commit changed; "
        "a real agent must guess them",
        "file reads stand in for tool calls (with rco: 1 pack call plus the changed files)",
        "files and pack are read at the commit itself, built from git with no store; "
        "the pack header reads as if that commit were HEAD with a clean working tree",
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
        scope = f"code commits since {o['since']}" if o.get("since") else "code commits"
        out = [f"# rco replay  (HEAD={o['head'][:7]}, last {s['n']} {scope})", ""]
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
        if s.get("call_site_commits"):
            out.append(f"The pack is missing a call site for a changed public name in "
                       f"{s['call_site_commits']} of {s['n']} commits "
                       f"({s['call_site_misses']} sites).")
        if s["pack_skipped"]:
            out.append(f"The pack skipped {s['pack_skipped']} of {s['n']} commits (nothing worth "
                       f"outlining), so nothing was sent: a tie.")
        if o["skipped"]:
            out.append(f"Not replayed: {o['skipped']} commit(s), a root commit or more changed "
                       f"code files than --max-files.")
        out += ["", HEADING_METHOD]
        out += [f"- {a}" for a in o["assumptions"]]
        out += ["", "## Per commit", "",
                "| commit | changed | without files | without tokens | with files "
                "| with tokens | pack tokens | imported files in pack |",
                "|---|---|---|---|---|---|---|---|"]
        for c in o["commits"]:
            w, r, cov = c["without"], c["with"], c["coverage"]
            pack_cell = "skipped" if r["pack_skipped"] else str(r["pack_tokens"])
            out.append(f"| {c['sha'][:7]} | {c['changed']} | {w['files']} | {w['tokens']} "
                       f"| {r['files']} | {r['tokens']} | {pack_cell} "
                       f"| {cov['imported_in_pack']} of {cov['imported']} |")
        return "\n".join(out) + "\n"


_DATE_ONLY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _code_commits(repo: Path, last: int | None,
                  since: str | None = None) -> list[tuple[str, str | None]]:
    """(sha, first parent or None) of the newest non-merge commits touching code.

    At most last of them (all when None); with since, only commits since then.
    A bare date means midnight local time, not git's current time of day.
    """
    args = ["log", "--no-merges", "--format=%H %P"]
    if since:
        args.append(f"--since={since} 00:00:00" if _DATE_ONLY.fullmatch(since) else f"--since={since}")
    out = gitread._git(repo, *args, "HEAD")
    found: list[tuple[str, str | None]] = []
    for line in out.decode().splitlines():
        if last is not None and len(found) >= last:
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


def _pack_at(repo: Path, sha: str, changed: list[str], budget: int):
    from rco.pack import NOOP_NOTES, pack, pack_markdown
    p = pack(repo, sha, changed, budget, as_if_head=True)
    skipped = any(n in p.notes for n in NOOP_NOTES)
    return p, pack_markdown(p, " ".join(changed)), skipped


def pack_text_at(repo: Path, sha: str, changed: list[str], budget: int) -> str:
    """The pack text replay counts for these changed files at sha."""
    return _pack_at(repo, sha, changed, budget)[1]


def _one(repo: Path, sha: str, parent: str, budget: int,
         count: Callable[[str], int]) -> dict | None:
    head_entries = gitread.ls_tree(repo, sha)
    tree = _regular(head_entries)
    changed = [p for p in changed_paths(gitread.ls_tree(repo, parent), head_entries)
               if p in tree and is_code(p)]
    if not changed:
        return None  # only deletions of code: nothing left to read
    code_paths = [p for p in tree if is_code(p)]
    raw = gitread.read_blobs(repo, (tree[p] for p in code_paths))
    code = {p: raw[tree[p]] for p in code_paths}
    # card 1350: manifests belong in the import graph, same as forest.read_sources
    manifest_paths = [p for p in tree
                      if p == "pyproject.toml" or p.rsplit("/", 1)[-1] in MANIFEST_NAMES]
    need = [p for p in manifest_paths if p not in code]
    if need:
        extra = gitread.read_blobs(repo, (tree[p] for p in need))
        graph_src = {**code, **{p: extra[tree[p]] for p in need}}
    else:
        graph_src = code
    graph = file_graph(sorted(tree), graph_src)
    parent_entries = gitread.ls_tree(repo, parent)
    base_tree = _regular(parent_entries)
    all_changed = changed_paths(parent_entries, head_entries)
    need_base = [p for p in all_changed if p in base_tree and is_code(p)]
    if need_base:
        raw_b = gitread.read_blobs(repo, (base_tree[p] for p in need_base))
        base_code = {p: raw_b[base_tree[p]] for p in need_base}
    else:
        base_code = {}
    interface = [p for p in changed
                 if interface_changed(p, base_code.get(p), code.get(p))]
    deleted = [p for p in need_base if p not in tree]
    for p in deleted:
        if interface_changed(p, base_code.get(p), None):
            interface.append(p)
    # a deleted file is only in the base graph; skip that work when nothing was deleted
    if deleted:
        base_code_paths = [p for p in base_tree if is_code(p)]
        missing = [p for p in base_code_paths if p not in base_code]
        if missing:
            raw_m = gitread.read_blobs(repo, (base_tree[p] for p in missing))
            base_all = {**base_code, **{p: raw_m[base_tree[p]] for p in missing}}
        else:
            base_all = dict(base_code)
        base_manifest = [p for p in base_tree
                         if p == "pyproject.toml" or p.rsplit("/", 1)[-1] in MANIFEST_NAMES]
        need_m = [p for p in base_manifest if p not in base_all]
        if need_m:
            extra_b = gitread.read_blobs(repo, (base_tree[p] for p in need_m))
            base_src = {**base_all, **{p: extra_b[base_tree[p]] for p in need_m}}
        else:
            base_src = base_all
        base_graph = file_graph(sorted(base_tree), base_src)
        importer_graph = merge_graphs(graph, base_graph)
    else:
        base_graph = {}
        importer_graph = graph
    _c, imported, importers = read_set(changed, graph, interface, importer_graph)
    imported = [p for p in imported if p in tree]
    importers = [p for p in importers if p in tree]
    reads = changed + imported + importers
    blobs_raw = gitread.read_blobs(repo, (tree[p] for p in reads))
    blobs = {p: blobs_raw[tree[p]] for p in reads}

    def text(paths: list[str]) -> str:
        return "".join(blobs[p].decode("utf-8", "replace") for p in paths)

    changed_tokens, imported_tokens = count(text(changed)), count(text(imported))
    importer_tokens = count(text(importers))
    p, pack_text, skipped = _pack_at(repo, sha, changed, budget)
    in_pack = {n.path for t in p.trees for n in t.nodes}
    pack_tokens = 0 if skipped else count(pack_text)
    without, with_ = arms(
        changed_tokens=changed_tokens, imported_tokens=imported_tokens,
        pack_tokens=pack_tokens, skipped=skipped,
        n_changed=len(changed), n_imported=len(imported),
        importer_tokens=importer_tokens, n_importers=len(importers),
    )
    names = {p: changed_names(p, base_code.get(p), code.get(p)) for p in interface}
    sites = 0
    sites_in = 0
    for imp in importers:
        called: set[str] = set()
        for src in interface:
            if src in graph.get(imp, ()) or src in base_graph.get(imp, ()):
                called |= set(names.get(src, ()))
        for n in call_sites(blobs[imp].decode("utf-8", "replace"), called):
            sites += 1
            if call_site_in_pack(pack_text, imp, n):
                sites_in += 1
    return {"sha": sha, "changed": len(changed),
            "without": {**without, "imported_files": len(imported),
                        "imported_tokens": imported_tokens,
                        "importer_files": len(importers),
                        "importer_tokens": importer_tokens},
            "with": with_,
            "coverage": {"changed_in_pack": sum(1 for x in changed if x in in_pack),
                         "imported_in_pack": sum(1 for x in imported if x in in_pack),
                         "imported": len(imported),
                         "importers": len(importers),
                         "call_sites": sites,
                         "call_sites_in_pack": sites_in}}


@contextmanager
def _no_scope() -> Iterator[None]:
    yield


def replay(repo: Path, last: int | None, budget: int, max_files: int,
           count: Callable[[str], int], tokenizer: str = "estimate chars/4",
           since: str | None = None, use_memo: bool = True) -> ReplayReport:
    if last is None and not since:
        raise UsageError("give --last or --since")
    if last is not None and last < 1:
        raise UsageError("--last must be at least 1")
    if not gitread.has_commit(repo):
        raise UsageError("no commits to replay")
    head = gitread.resolve(repo, "HEAD")
    commits, skipped = [], 0
    run_scope = memo.scope() if use_memo else _no_scope()
    with timed("replay"), run_scope:
        for sha, parent in _code_commits(repo, last, since):
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
        raise UsageError(f"no code commit to replay in the last {last if last else 'commits since ' + str(since)} "
                         f"({skipped} skipped: root or shallow-clone boundary commits, or over --max-files)")
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
        "pack_skipped": sum(1 for c in commits if c["with"]["pack_skipped"]),
        "call_site_misses": sum(c["coverage"]["call_sites"] - c["coverage"]["call_sites_in_pack"]
                                for c in commits),
        "call_site_commits": sum(1 for c in commits
                                 if c["coverage"]["call_sites"] > c["coverage"]["call_sites_in_pack"]),
    }
    return ReplayReport({
        "schema": SCHEMA,
        "rco_version": __version__,
        "head": head,
        "since": since,
        "tokenizer": "chars/4 (estimate)" if is_est else tokenizer,
        "tokenizer_is_estimate": is_est,
        "assumptions": assumptions(tokenizer, is_est),
        "skipped": skipped,
        "commits": commits,
        "summary": summary,
    })
