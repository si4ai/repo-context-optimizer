#!/usr/bin/env python3
"""phase 3 cold-agent A/B harness for rco pack (README roadmap, phase 3).

prepares control and treatment briefs, and scores mill cold-test kit logs.
this tool never launches agents. The A/B that counts waits until phase 2's
forest is in a released install (see docs/baseline/phase3-ab.md).

  python3 tools/phase3_ab.py prepare --repo REPO [--sha SHA] [--for FILE ...]
      [--budget N] --task-file PATH --out DIR
  python3 tools/phase3_ab.py score --control LOG [LOG ...]
      --treatment LOG [LOG ...] [--first-useful REGEX] [--out FILE]

prepare writes DIR/manifest.json, DIR/control/brief.md, and
DIR/treatment/{brief.md,pack.md,pack.json}. score prints one JSON object
on stdout (and to --out when given). A token-savings claim is refused
unless every scored log has a platform tokenizer count.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path, PurePosixPath

import phase0_baseline as p0
from rco import __version__, gitread
from rco.forest_model import DEFAULT_BUDGET, pack_to_obj
from rco.output import UsageError
from rco.pack import PackReport, pack, pack_markdown

SCHEMA = "rco.phase3_ab/1"
CLAIMABLE_SOURCES = ("platform", "tokenizer")
_TOKENS = re.compile(r"^TOKENS\s+(?P<kv>.*)$")

CONTROL_INSTRUCTIONS = (
    "You are a cold agent. Read this task, then explore the repository as needed. "
    "No rco pack is provided in this arm. Open raw files only as you need them."
)
TREATMENT_INSTRUCTIONS = (
    "You are a cold agent. Read this task, then read the rco pack below. "
    "Open raw files only on demand."
)


def _die_usage(msg: str) -> int:
    print(f"phase3_ab: {msg}", file=sys.stderr)
    return 2


def _dump(obj: dict, path: Path | None = None) -> str:
    text = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return text


def _brief(task: str, instructions: str, pack_md: str | None = None) -> str:
    body = task if task.endswith("\n") else task + "\n"
    out = f"# task\n\n{body}\n# instructions\n\n{instructions}\n"
    if pack_md is not None:
        out += "\n" + (pack_md if pack_md.endswith("\n") else pack_md + "\n")
    return out


def _resolve_sha(repo: Path, sha: str) -> str:
    return gitread.resolve(repo, sha)


def cmd_prepare(repo: Path, sha: str, for_paths: list[str], budget: int,
                task_file: Path, out: Path) -> dict:
    if not task_file.is_file():
        raise UsageError(f"--task-file not found: {task_file}")
    task = task_file.read_text()
    if not task.strip():
        raise UsageError("--task-file is empty")
    sha = _resolve_sha(repo, sha)
    p = pack(repo, sha, for_paths or None, budget)
    pack_md = pack_markdown(p, " ".join(for_paths))
    pack_obj = pack_to_obj(p)
    manifest = {
        "schema": SCHEMA,
        "cmd": "prepare",
        "sha": sha,
        "budget": budget,
        "used": p.used,
        "for": list(for_paths),
        "rco_version": __version__,
        "arms": ["control", "treatment"],
        "notes": list(p.notes),
        "stale": list(p.stale),
        "trimmed": list(p.trimmed),
        "pack_exit": PackReport(p).exit_code(),
    }
    (out / "control").mkdir(parents=True, exist_ok=True)
    (out / "treatment").mkdir(parents=True, exist_ok=True)
    (out / "control" / "brief.md").write_text(_brief(task, CONTROL_INSTRUCTIONS))
    (out / "treatment" / "brief.md").write_text(
        _brief(task, TREATMENT_INSTRUCTIONS, pack_md))
    (out / "treatment" / "pack.md").write_text(pack_md if pack_md.endswith("\n") else pack_md + "\n")
    _dump(pack_obj, out / "treatment" / "pack.json")
    _dump(manifest, out / "manifest.json")
    return manifest


def _command_rows(text: str) -> list[dict]:
    rows: list[dict] = []
    for raw in text.splitlines():
        line = raw.strip()
        m = p0._ROW_TABLE.match(line) or p0._ROW_FLAT.match(line)
        if not m or set(m.group("cmd")) <= set("-| "):
            continue
        if m.group("cmd").strip().lower() in ("command",):
            continue
        rows.append({
            "cmd": m.group("cmd").strip(),
            "exit": int(m.group("exit")),
            "bytes": p0._int_or_none(m.group("bytes")),
            "time": m.group("t"),
        })
    return rows


def _tokens_line(text: str) -> dict:
    for raw in text.splitlines():
        m = _TOKENS.match(raw.strip())
        if not m:
            continue
        kv = dict(p.split("=", 1) for p in m.group("kv").split() if "=" in p)
        return {
            "tokens_source": kv.get("source"),
            "tokens_before_first_useful": p0._int_or_none(kv["before_first_useful"])
            if "before_first_useful" in kv else None,
        }
    return {"tokens_source": None, "tokens_before_first_useful": None}


def _task_turn(parsed: dict) -> dict:
    turns = parsed.get("turns") or []
    for t in turns:
        if t.get("turn") == "task":
            return t
    return turns[0] if turns else {}


def score_log(text: str, first_useful_pattern: str | None) -> dict:
    parsed = p0.parse_cold_log(text)
    rows = _command_rows(text)
    tokens = _tokens_line(text)
    first_action = parsed.get("first_useful_action")
    reads = parsed.get("reads_before_first_useful")
    before_bytes = parsed.get("stdout_bytes_before_first_useful")
    if first_useful_pattern:
        rx = re.compile(first_useful_pattern)
        first = next((i for i, r in enumerate(rows)
                      if r["exit"] == 0 and rx.search(r["cmd"])), None)
        if first is None:
            first_action = None
            reads = None
            before_bytes = None
        else:
            before = rows[:first]
            first_action = rows[first]["cmd"]
            reads = len(before)
            known = [r["bytes"] for r in before if r["bytes"] is not None]
            before_bytes = sum(known)
    turn = _task_turn(parsed)
    return {
        "commands": turn.get("commands"),
        "wrong_guesses": turn.get("wrong_guesses"),
        "retries": turn.get("retries"),
        "seconds": turn.get("seconds"),
        "stdout_bytes": turn.get("stdout_bytes"),
        "task_done": turn.get("task_done"),
        "first_useful_action": first_action,
        "reads_before_first_useful": reads,
        "stdout_bytes_before_first_useful": before_bytes,
        "tokens_source": tokens["tokens_source"],
        "tokens_before_first_useful": tokens["tokens_before_first_useful"],
    }


def _median(values: list) -> int | float | None:
    known = [v for v in values if v is not None]
    if not known:
        return None
    return p0.percentile(known, 50)


def _arm(paths: list[str], first_useful_pattern: str | None) -> dict:
    logs = []
    for p in paths:
        path = Path(p)
        if not path.is_file():
            raise UsageError(f"log not found: {p}")
        rec = score_log(path.read_text(encoding="utf-8", errors="replace"),
                        first_useful_pattern)
        rec["source"] = PurePosixPath(p).name
        logs.append(rec)
    keys = (
        "commands", "wrong_guesses", "retries", "seconds", "stdout_bytes",
        "reads_before_first_useful", "stdout_bytes_before_first_useful",
        "tokens_before_first_useful",
    )
    median = {k: _median([lg[k] for lg in logs]) for k in keys}
    sources = [lg["tokens_source"] for lg in logs]
    src = sources[0] if sources and all(s == sources[0] for s in sources) else None
    if logs and all(lg.get("first_useful_action") == logs[0].get("first_useful_action")
                    for lg in logs):
        median["first_useful_action"] = logs[0].get("first_useful_action")
    else:
        median["first_useful_action"] = None
    median["tokens_source"] = src
    return {"n": len(logs), "logs": logs, "median": median}


def _delta(control: dict, treatment: dict) -> dict:
    keys = (
        "commands", "wrong_guesses", "retries", "seconds", "stdout_bytes",
        "reads_before_first_useful", "stdout_bytes_before_first_useful",
        "tokens_before_first_useful",
    )
    out = {}
    for k in keys:
        a, b = control["median"].get(k), treatment["median"].get(k)
        out[k] = None if a is None or b is None else b - a
    return out


def _claim(control: dict, treatment: dict) -> tuple[bool, list[str]]:
    blockers: list[str] = []
    logs = control["logs"] + treatment["logs"]
    if not control["logs"] or not treatment["logs"]:
        blockers.append("both arms need at least one log")
    for lg in logs:
        src = lg.get("tokens_source")
        if src not in CLAIMABLE_SOURCES:
            blockers.append(
                "tokens_source is not a platform tokenizer "
                "(need TOKENS source=platform or source=tokenizer)"
            )
            break
        if lg.get("tokens_before_first_useful") is None:
            blockers.append("missing tokens_before_first_useful on a scored log")
            break
    # unique, stable
    seen: list[str] = []
    for b in blockers:
        if b not in seen:
            seen.append(b)
    return (not seen, seen)


def cmd_score(control_paths: list[str], treatment_paths: list[str],
              first_useful: str | None, out: Path | None) -> dict:
    if not control_paths or not treatment_paths:
        raise UsageError("score needs --control and --treatment logs")
    control = _arm(control_paths, first_useful)
    treatment = _arm(treatment_paths, first_useful)
    claimable, blockers = _claim(control, treatment)
    report = {
        "schema": SCHEMA,
        "cmd": "score",
        "first_useful_pattern": first_useful,
        "control": control,
        "treatment": treatment,
        "delta": _delta(control, treatment),
        "savings_claimable": claimable,
        "claim_blockers": blockers,
    }
    text = _dump(report, out)
    sys.stdout.write(text)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("prepare", help="write control and treatment briefs")
    p.add_argument("--repo", required=True)
    p.add_argument("--sha", default="HEAD")
    p.add_argument("--for", dest="for_paths", nargs="+", default=[])
    p.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    p.add_argument("--task-file", required=True)
    p.add_argument("--out", required=True)

    s = sub.add_parser("score", help="compare control and treatment cold-kit logs")
    s.add_argument("--control", nargs="+", required=True)
    s.add_argument("--treatment", nargs="+", required=True)
    s.add_argument("--first-useful", default=None)
    s.add_argument("--out", default=None)

    try:
        args = ap.parse_args(argv)
    except SystemExit as e:
        code = e.code
        return 0 if not code else (code if isinstance(code, int) else 2)

    try:
        if args.cmd == "prepare":
            cmd_prepare(Path(args.repo), args.sha, list(args.for_paths),
                        args.budget, Path(args.task_file), Path(args.out))
            return 0
        cmd_score(list(args.control), list(args.treatment),
                  args.first_useful, Path(args.out) if args.out else None)
        return 0
    except UsageError as e:
        return _die_usage(str(e))


if __name__ == "__main__":
    sys.exit(main())
