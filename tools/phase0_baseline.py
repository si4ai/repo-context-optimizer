#!/usr/bin/env python3
"""Phase 0 baseline for repo-context-optimizer (README roadmap, Phase 0).

Measures, before any rco code exists, the numbers later phases compare
against. Standard library only; git is the only outside program.

  python3 tools/phase0_baseline.py merges  --repo MILL [--rev main] [--last N]
  python3 tools/phase0_baseline.py extract --repo MILL [--rev HEAD]
  python3 tools/phase0_baseline.py cold    LOG [LOG ...]
  python3 tools/phase0_baseline.py all     --repo MILL [--cold LOG ...]

Every subcommand prints one JSON object on stdout.

merges   For each first-parent commit on REV (squash merges and release
         merge commits alike, diffed against the first parent), counts the
         modules it touches at two grains (see module_of), and reports the
         distribution, median and p90. The root commit is skipped.
extract  Parses every tracked *.py file at REV with ast.parse and builds a
         body-stripped public skeleton (a stand-in for a Phase 1 Tree).
         Reports wall time, per-file p50/p99, tracemalloc peak, and sizes in
         estimated tokens (characters / 4, the README's estimator).
cold     Reads cold-agent logs written by the mill's cold-test kit (rows of
         command / exit / stdout bytes / time, plus "SUMMARY turn=..."
         lines) and reports per-turn commands, wrong guesses, retries,
         seconds and stdout bytes, plus reads and bytes before the first
         useful action (the first successful `mill join`).
"""
from __future__ import annotations

import argparse
import ast
import json
import math
import re
import resource
import subprocess
import sys
import time
import tracemalloc
from pathlib import Path, PurePosixPath

CHARS_PER_TOKEN = 4  # README: estimated tokens = characters / 4


# ---------------------------------------------------------------- pure parts

def module_of(path: str, grain: str = "coarse") -> str:
    """Map a repo-relative path to a module name.

    coarse: the first path part; under a Python package root (a top-level
            dir whose files are code, here agent_coord) the first two parts,
            so agent_coord/server/x.py -> agent_coord/server and
            agent_coord/config.py -> agent_coord/config.py. Files at the
            repo root are their own module (README.md, pyproject.toml).
    package: the directory holding the file (its Python package), so
            agent_coord/server/api/mill/rpc.py -> agent_coord/server/api/mill.
            Root files map to "." .
    """
    parts = PurePosixPath(path).parts
    if grain == "package":
        return str(PurePosixPath(*parts[:-1])) if len(parts) > 1 else "."
    if grain != "coarse":
        raise ValueError(f"unknown grain {grain!r}")
    if len(parts) == 1:
        return parts[0]
    if parts[0] in PACKAGE_ROOTS:
        return "/".join(parts[:2])
    return parts[0]


PACKAGE_ROOTS = frozenset({"agent_coord"})


def percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in 0..100) of a non-empty list."""
    if not values:
        raise ValueError("percentile of empty list")
    s = sorted(values)
    k = math.ceil(q * len(s) / 100)
    return s[min(len(s), max(1, k)) - 1]


def summarize(values: list[float]) -> dict:
    if not values:
        return {"n": 0}
    s = sorted(values)
    return {
        "n": len(s),
        "min": s[0],
        "p50": percentile(s, 50),
        "p90": percentile(s, 90),
        "p99": percentile(s, 99),
        "max": s[-1],
        "mean": round(sum(s) / len(s), 2),
    }


def histogram(values: list[int]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in sorted(values):
        out[str(v)] = out.get(str(v), 0) + 1
    return out


def est_tokens(text: str) -> int:
    return -(-len(text) // CHARS_PER_TOKEN)


def _first_doc_line(node) -> str | None:
    doc = ast.get_docstring(node, clean=True)
    if not doc:
        return None
    return doc.strip().splitlines()[0]


def _is_public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def _stub_body(node) -> list[ast.stmt]:
    first = _first_doc_line(node)
    body: list[ast.stmt] = []
    if first:
        body.append(ast.Expr(ast.Constant(first)))
    body.append(ast.Expr(ast.Constant(...)))
    return body


def _skeleton_stmts(stmts: list[ast.stmt], in_class: bool) -> list[ast.stmt]:
    out: list[ast.stmt] = []
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if _is_public(st.name):
                st.body = _stub_body(st)
                out.append(st)
        elif isinstance(st, ast.ClassDef):
            if _is_public(st.name):
                inner = _skeleton_stmts(st.body, in_class=True)
                first = _first_doc_line(st)
                head = [ast.Expr(ast.Constant(first))] if first else []
                st.body = head + (inner or [ast.Expr(ast.Constant(...))])
                out.append(st)
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            targets = st.targets if isinstance(st, ast.Assign) else [st.target]
            names = [t.id for t in targets if isinstance(t, ast.Name)]
            if names and all(n == "__all__" or (n.isupper() and _is_public(n)) for n in names):
                out.append(st)
            elif in_class and isinstance(st, ast.AnnAssign) and names and _is_public(names[0]):
                st.value = None
                st.simple = 1
                out.append(st)
    return out


def skeleton(source: str, filename: str = "<src>") -> str:
    """Body-stripped public skeleton of a module.

    Keeps public classes and functions with signatures and type hints, the
    first docstring line of the module, classes and functions, UPPER_CASE
    constants and __all__, and annotated class fields. Drops imports,
    bodies and private names. A stand-in for a Phase 1 Tree, not one.
    """
    tree = ast.parse(source, filename=filename)
    body = _skeleton_stmts(tree.body, in_class=False)
    first = _first_doc_line(tree)
    if first:
        body.insert(0, ast.Expr(ast.Constant(first)))
    return ast.unparse(ast.Module(body=body, type_ignores=[]))


_ROW_TABLE = re.compile(r"^\|\s*`?(?P<cmd>.+?)`?\s*\|\s*(?P<exit>-?\d+)\s*\|\s*~?(?P<bytes>\d+|[^|]*?)\s*\|\s*(?P<t>\d\d:\d\d:\d\d)\s*\|\s*$")
_ROW_FLAT = re.compile(r"^(?P<t>\d\d:\d\d:\d\d)\s+exit=(?P<exit>-?\d+)\s+stdout_bytes=(?P<bytes>\S+)\s+(?P<cmd>.+)$")
_SUMMARY = re.compile(r"^SUMMARY\s+(?P<kv>.*)$")


def _secs(t: str) -> int:
    h, m, s = (int(x) for x in t.split(":"))
    return h * 3600 + m * 60 + s


def _int_or_none(s: str) -> int | None:
    s = s.strip().lstrip("~")
    return int(s) if s.isdigit() else None


def parse_cold_log(text: str) -> dict:
    """Parse one cold-test kit log into command rows and SUMMARY turns."""
    rows: list[dict] = []
    turns: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = _SUMMARY.match(line)
        if m:
            kv = dict(p.split("=", 1) for p in m.group("kv").split() if "=" in p)
            turn = {"turn": kv.get("turn")}
            for k in ("commands", "wrong_guesses", "retries", "stdout_bytes"):
                turn[k] = _int_or_none(kv[k]) if k in kv else None
            if "start" in kv and "end" in kv:
                try:
                    turn["seconds"] = _secs(kv["end"]) - _secs(kv["start"])
                except ValueError:
                    turn["seconds"] = None
            turn["task_done"] = kv.get("task_done")
            turn["rows"] = len(rows) - sum(t["rows"] for t in turns)
            turns.append(turn)
            continue
        m = _ROW_TABLE.match(line) or _ROW_FLAT.match(line)
        if m and not set(m.group("cmd")) <= set("-| "):
            if m.group("cmd").strip().lower() in ("command",):
                continue
            rows.append({
                "cmd": m.group("cmd").strip(),
                "exit": int(m.group("exit")),
                "bytes": _int_or_none(m.group("bytes")),
                "time": m.group("t"),
            })
    first = next((i for i, r in enumerate(rows)
                  if r["exit"] == 0 and re.search(r"\bmill join\b.*--link-(file|stdin)", r["cmd"])), None)
    before = rows[:first] if first is not None else rows
    known = [r["bytes"] for r in before if r["bytes"] is not None]
    return {
        "rows": len(rows),
        "turns": turns,
        "first_useful_action": "mill join" if first is not None else None,
        "reads_before_first_useful": len(before) if first is not None else None,
        "stdout_bytes_before_first_useful": sum(known) if first is not None else None,
        "unmeasured_rows_before_first_useful": sum(1 for r in before if r["bytes"] is None) if first is not None else None,
    }


# ---------------------------------------------------------------- git parts

def git(repo: str, *args: str) -> str:
    return subprocess.run(["git", "-C", repo, *args], check=True,
                          capture_output=True, text=True).stdout


def first_parent_changes(repo: str, rev: str) -> list[dict]:
    out = git(repo, "log", "--first-parent", "--diff-merges=first-parent",
              "--no-renames", "--name-only", "--format=%x00%H %P", rev)
    commits = []
    for chunk in out.split("\x00")[1:]:
        lines = [ln for ln in chunk.splitlines() if ln.strip()]
        sha, *parents = lines[0].split()
        commits.append({"sha": sha, "parents": parents, "files": lines[1:]})
    return commits


def cmd_merges(repo: str, rev: str, last: int | None) -> dict:
    commits = [c for c in first_parent_changes(repo, rev) if c["parents"]]
    if last:
        commits = commits[:last]
    res = {"repo_rev": git(repo, "rev-parse", rev).strip(), "commits": len(commits),
           "release_merges": sum(1 for c in commits if len(c["parents"]) > 1),
           "oldest": commits[-1]["sha"] if commits else None}
    for grain in ("coarse", "package"):
        counts = [len({module_of(f, grain) for f in c["files"]}) for c in commits]
        freq: dict[str, int] = {}
        for c in commits:
            for mod in {module_of(f, grain) for f in c["files"]}:
                freq[mod] = freq.get(mod, 0) + 1
        res[grain] = {"modules_per_merge": summarize(counts), "histogram": histogram(counts),
                      "most_touched": sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:12]}
        py = [len({module_of(f, grain) for f in c["files"] if f.endswith(".py")}) for c in commits]
        res[grain]["python_modules_per_merge"] = summarize(py)
    files = [len(c["files"]) for c in commits]
    res["files_per_merge"] = summarize(files)
    tracked = git(repo, "ls-tree", "-r", "--name-only", res["repo_rev"]).splitlines()
    for grain in ("coarse", "package"):
        total = len({module_of(f, grain) for f in tracked})
        hits = [round(1 - len({module_of(f, grain) for f in c["files"]}) / total, 4) for c in commits]
        res[grain]["modules_at_rev"] = total
        res[grain]["cache_hit_rate"] = {"note": "share of REV's modules a merge left untouched",
                                         **summarize(hits), "p10": percentile(hits, 10)}
    return res


def cmd_extract(repo: str, rev: str) -> dict:
    sha = git(repo, "rev-parse", rev).strip()
    paths = [p for p in git(repo, "ls-tree", "-r", "--name-only", sha).splitlines() if p.endswith(".py")]
    sources = {p: git(repo, "cat-file", "blob", f"{sha}:{p}") for p in paths}  # read once, outside timing
    per_file: list[float] = []
    failed: list[str] = []
    t0 = time.perf_counter()
    for p, src in sources.items():
        a = time.perf_counter()
        try:
            ast.parse(src, filename=p)
        except SyntaxError:
            failed.append(p)
        per_file.append((time.perf_counter() - a) * 1000)
    parse_total = time.perf_counter() - t0

    skel_ms: list[float] = []
    skel_tokens: dict[str, int] = {}
    t1 = time.perf_counter()
    for p, src in sources.items():
        a = time.perf_counter()
        try:
            skel_tokens[p] = est_tokens(skeleton(src, p))
        except SyntaxError:
            pass
        skel_ms.append((time.perf_counter() - a) * 1000)
    skel_total = time.perf_counter() - t1

    tracemalloc.start()
    trees = [ast.parse(src, filename=p) for p, src in sources.items() if p not in failed]
    _, peak_all = tracemalloc.get_traced_memory()
    del trees
    tracemalloc.reset_peak()
    for p, src in sources.items():
        if p not in failed:
            ast.parse(src, filename=p)
    _, peak_one = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    raw_chars = sum(len(s) for s in sources.values())
    by_pkg: dict[str, int] = {}
    for p, n in skel_tokens.items():
        by_pkg[module_of(p, "package")] = by_pkg.get(module_of(p, "package"), 0) + n
    by_coarse: dict[str, int] = {}
    for p, n in skel_tokens.items():
        by_coarse[module_of(p, "coarse")] = by_coarse.get(module_of(p, "coarse"), 0) + n
    return {
        "repo_rev": sha, "python": sys.version.split()[0],
        "files": len(paths), "syntax_errors": failed,
        "lines": sum(s.count("\n") for s in sources.values()),
        "raw_chars": raw_chars, "raw_est_tokens": -(-raw_chars // CHARS_PER_TOKEN),
        "parse_seconds_total": round(parse_total, 3),
        "parse_ms_per_file": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in summarize(per_file).items()},
        "parse_plus_skeleton_seconds_total": round(skel_total, 3),
        "parse_plus_skeleton_ms_per_file": {k: (round(v, 3) if isinstance(v, float) else v) for k, v in summarize(skel_ms).items()},
        "tracemalloc_peak_mb_all_trees_held": round(peak_all / 2**20, 1),
        "tracemalloc_peak_mb_one_tree_at_a_time": round(peak_one / 2**20, 1),
        "process_max_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "skeleton_est_tokens_total": sum(skel_tokens.values()),
        "skeleton_est_tokens_per_file": summarize(list(skel_tokens.values())),
        "skeleton_est_tokens_per_package": summarize(list(by_pkg.values())),
        "skeleton_est_tokens_per_coarse_module": dict(sorted(by_coarse.items(), key=lambda kv: -kv[1])),
        "_by_package": by_pkg,
    }


def cmd_pack_per_merge(merges_repo: str, rev: str, last: int | None, by_pkg: dict[str, int]) -> dict:
    """Skeleton tokens of the Python packages each merge touched (sizes at REV)."""
    commits = [c for c in first_parent_changes(merges_repo, rev) if c["parents"]]
    if last:
        commits = commits[:last]
    out = {"note": "Tree sizes at REV of the Python packages each merge touched; "
                   "an upper bound for a `--for` pack of that merge's files"}
    for label, keep in (("all_python", lambda f: True),
                        ("code_only_no_tests", lambda f: not f.startswith("tests/"))):
        sizes = []
        for c in commits:
            pk = {module_of(f, "package") for f in c["files"] if f.endswith(".py") and keep(f)}
            sizes.append(sum(by_pkg.get(p, 0) for p in pk))
        out[label] = {"est_tokens": summarize(sizes),
                      "merges_within_8000": sum(1 for s in sizes if s <= 8000)}
    return out


KIT_FILES = ("docs/JOIN.md", "docs/protocol/skill.md", "docs/protocol/models/grok-bot/SKILL.md")


def cmd_kit(repo: str, rev: str) -> dict:
    """Size of the cold-test kit's reading at REV (what a tester is handed).

    tools/cold_measure_head.py hands a tester JOIN.md plus the mill-desk
    SKILL (docs/protocol/skill.md); Grok Bot testers also read the Grok Bot
    guide. This is the fixed read before any command runs.
    """
    sha = git(repo, "rev-parse", rev).strip()
    files = {}
    for p in KIT_FILES:
        try:
            files[p] = est_tokens(git(repo, "cat-file", "blob", f"{sha}:{p}"))
        except subprocess.CalledProcessError:
            files[p] = None
    return {"repo_rev": sha, "est_tokens": files,
            "join_plus_skill": sum(v or 0 for v in list(files.values())[:2]),
            "with_grok_bot_guide": sum(v or 0 for v in files.values())}


def cmd_cold(paths: list[str]) -> dict:
    logs = []
    for p in paths:
        r = parse_cold_log(Path(p).read_text(encoding="utf-8", errors="replace"))
        r["source"] = "/".join(PurePosixPath(p).parts[-2:]) if "uploads" in p else "/".join(PurePosixPath(p).parts[-3:])
        logs.append(r)
    join = [t for lg in logs for t in lg["turns"] if t["turn"] == "join"]
    agg = {}
    for k in ("commands", "wrong_guesses", "retries", "seconds", "stdout_bytes"):
        vals = [t[k] for t in join if t.get(k) is not None]
        agg[k] = summarize(vals)
    reads = [lg["reads_before_first_useful"] for lg in logs if lg["reads_before_first_useful"] is not None]
    return {"logs": logs, "join_turns": agg, "reads_before_first_useful": summarize(reads),
            "tokens": "not in the logs: the kit records stdout bytes, not platform token counts"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("merges", "extract", "all"):
        s = sub.add_parser(name)
        s.add_argument("--repo", required=True)
        s.add_argument("--rev", default="HEAD")
        s.add_argument("--last", type=int)
        if name == "all":
            s.add_argument("--cold", nargs="*", default=[])
    c = sub.add_parser("cold")
    c.add_argument("logs", nargs="+")
    a = ap.parse_args(argv)
    if a.cmd == "merges":
        out = cmd_merges(a.repo, a.rev, a.last)
    elif a.cmd == "extract":
        out = cmd_extract(a.repo, a.rev)
        out.pop("_by_package")
    elif a.cmd == "cold":
        out = cmd_cold(a.logs)
    else:
        ex = cmd_extract(a.repo, a.rev)
        out = {"merges": cmd_merges(a.repo, a.rev, a.last), "extract": ex,
               "pack_per_merge": cmd_pack_per_merge(a.repo, a.rev, a.last, ex.pop("_by_package")),
               "kit": cmd_kit(a.repo, a.rev),
               "cold": cmd_cold(a.cold) if a.cold else None}
    json.dump(out, sys.stdout, indent=1, sort_keys=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
