"""Select CI jobs for the paths changed between two commits.

Reads ci/test-select.toml. Each [[rule]] has paths (globs) and jobs.
A changed file runs the union of jobs from every rule it matches.
A file that matches nothing runs nothing. "**" is zero or more
directories. Job names other than pytest and spin are an error.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - py3.11+ is required
    tomllib = None  # type: ignore

KNOWN_JOBS = ("pytest", "spin")


class MapError(Exception):
    """The map file is missing a field or names an unknown job."""


def match_glob(path: str, pattern: str) -> bool:
    """Match a repo-relative path. ** is zero or more directories."""
    return _match(path.split("/"), pattern.split("/"))


def _match(path: list[str], pattern: list[str]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        if _match(path, pattern[1:]):
            return True
        return bool(path) and _match(path[1:], pattern)
    if not path or not _segment(path[0], pattern[0]):
        return False
    return _match(path[1:], pattern[1:])


def _segment(text: str, pattern: str) -> bool:
    i = 0
    j = 0
    star = -1
    mark = -1
    while i < len(text):
        if j < len(pattern) and (pattern[j] == "?" or pattern[j] == text[i]):
            i += 1
            j += 1
        elif j < len(pattern) and pattern[j] == "*":
            star = j
            mark = i
            j += 1
        elif star >= 0:
            mark += 1
            i = mark
            j = star + 1
        else:
            return False
    while j < len(pattern) and pattern[j] == "*":
        j += 1
    return j == len(pattern)


def load_map(path: Path) -> tuple[list[tuple[list[str], list[str]]], list[str]]:
    """Return rules and the sorted job names the map mentions."""
    if tomllib is None:
        raise MapError("python 3.11 or newer is required")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MapError("cannot read map: %s" % exc) from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise MapError("map is not valid toml: %s" % exc) from exc
    rules_raw = data.get("rule", [])
    if not isinstance(rules_raw, list):
        raise MapError("rule must be a list of tables")
    rules: list[tuple[list[str], list[str]]] = []
    seen: set[str] = set()
    for rule in rules_raw:
        if not isinstance(rule, dict):
            raise MapError("rule must be a table")
        paths = rule.get("paths")
        jobs = rule.get("jobs")
        if not isinstance(paths, list) or not all(isinstance(item, str) for item in paths):
            raise MapError("rule paths must be a list of strings")
        if not isinstance(jobs, list) or not all(isinstance(item, str) for item in jobs):
            raise MapError("rule jobs must be a list of strings")
        for job in jobs:
            if job not in KNOWN_JOBS:
                raise MapError("unknown job name: %s" % job)
            seen.add(job)
        rules.append((paths, jobs))
    return rules, sorted(seen)


def changed_paths(base: str, head: str) -> list[str]:
    """Repo-relative paths from git diff --name-only --no-renames -z BASE...HEAD."""
    proc = subprocess.run(
        ["git", "diff", "--name-only", "--no-renames", "-z", "%s...%s" % (base, head)],
        check=False,
        capture_output=True,
    )
    if proc.returncode != 0:
        err = proc.stderr.decode("utf-8", "replace").strip()
        raise MapError(err or "git diff failed")
    names = proc.stdout.decode("utf-8", "replace").split("\0")
    return [name for name in names if name]


def select_jobs(paths: list[str], rules: list[tuple[list[str], list[str]]]) -> set[str]:
    """Union of jobs from every rule a changed path matches."""
    chosen: set[str] = set()
    for path in paths:
        for globs, jobs in rules:
            if any(match_glob(path, glob) for glob in globs):
                chosen.update(jobs)
    return chosen


def write_output(jobs: list[str], chosen: set[str], dest: str) -> None:
    """Append job=true|false lines. One line per job named in the map."""
    with open(dest, "a", encoding="utf-8", newline="\n") as handle:
        for job in jobs:
            handle.write("%s=%s\n" % (job, "true" if job in chosen else "false"))


def run(base: str, head: str, map_path: Path) -> int:
    rules, jobs = load_map(map_path)
    chosen = select_jobs(changed_paths(base, head), rules)
    for job in sorted(chosen):
        print(job)
    output = os.environ.get("GITHUB_OUTPUT", "")
    if output:
        write_output(jobs, chosen, output)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="select CI jobs from changed paths")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--map", default="ci/test-select.toml")
    args = parser.parse_args(argv)
    try:
        return run(args.base, args.head, Path(args.map))
    except MapError as exc:
        print("test_select: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
