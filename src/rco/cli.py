"""rco command line (tree-contract section 8)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from rco import __version__, build, canon, check, diff, gitread
from rco.model import Map
from rco.output import Exit, UsageError, emit


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # argparse would exit 2 itself; keep it one path
        raise UsageError(message)


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--repo", default=".", help="git repository (default: .)")
    p.add_argument("--format", choices=("markdown", "json"), default="markdown",
                   help="output format (default: markdown)")
    p.add_argument("--out", type=Path, default=None, help="write the report to FILE")


def make_parser() -> argparse.ArgumentParser:
    p = _Parser(prog="rco", description="Deterministic repo context for coding agents.")
    p.add_argument("--version", action="version", version=f"rco {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=_Parser)

    t = sub.add_parser("tree", help="print the Map (Trees) of a commit")
    t.add_argument("sha", nargs="?", default="HEAD")
    t.add_argument("--for", dest="for_paths", nargs="+", default=None, metavar="PATH",
                   help="only the modules that own these paths")
    _common(t)

    c = sub.add_parser("check", help="check a map against the code at a commit")
    c.add_argument("map", nargs="?", default=None, help="map JSON file (rco tree --format json)")
    c.add_argument("sha", nargs="?", default=None, help="commit (default: the map's sha)")
    c.add_argument("--selftest", action="store_true", help="run the planted-drift self-test")
    _common(c)

    d = sub.add_parser("diff", help="contract changes between two commits")
    d.add_argument("base", nargs="?", default=None)
    d.add_argument("head", nargs="?", default=None)
    _common(d)

    u = sub.add_parser("update", help="publish a commit to the store (not implemented yet)")
    u.add_argument("new", nargs="?", default="HEAD")
    u.add_argument("--name", default="main", help="pointer name (default: main)")
    _common(u)

    k = sub.add_parser("pack", help="Forest plus Trees for an agent (not implemented yet)")
    k.add_argument("sha", nargs="?", default="HEAD")
    k.add_argument("--for", dest="for_paths", nargs="+", default=None, metavar="FILE")
    k.add_argument("--budget", type=int, default=8000, help="estimated tokens (default: 8000)")
    _common(k)

    f = sub.add_parser("forest", help="print the computed Forest of a commit")
    f.add_argument("sha", nargs="?", default="HEAD")
    _common(f)
    return p


# Phase 2 commands that exist but are not built yet: exit 2 (usage), one line.
_NOT_YET = {"update": "step 3", "pack": "step 5"}


@dataclass(frozen=True)
class MapReport:
    map: Map

    def to_obj(self) -> dict:
        return canon.map_to_obj(self.map)

    def to_markdown(self) -> str:
        from rco.render import map_markdown
        return map_markdown(self.map)

    def exit_code(self) -> Exit:
        return Exit.OK


def _read_map(path: str) -> Map:
    try:
        return canon.map_from_obj(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise UsageError(f"unreadable map {path}: {e}") from None


def run(args: argparse.Namespace) -> Exit:
    repo = Path(args.repo)
    if args.cmd == "tree":
        sha = gitread.resolve(repo, args.sha)
        return emit(MapReport(build.build_map(repo, sha, args.for_paths)), args.format, args.out)
    if args.cmd == "check":
        if args.selftest:
            return emit(check.selftest(), args.format, args.out)
        if args.map is None:
            raise UsageError("check needs MAP or --selftest")
        m = _read_map(args.map)
        sha = gitread.resolve(repo, args.sha or m.sha)
        return emit(check.check(repo, sha, m), args.format, args.out)
    if args.cmd == "diff":
        return emit(diff.diff(repo, args.base, args.head), args.format, args.out)
    if args.cmd == "forest":
        from rco import forest
        sha = gitread.resolve(repo, args.sha)
        return emit(forest.ForestReport(forest.build_forest(repo, sha)), args.format, args.out)
    if args.cmd in _NOT_YET:
        raise UsageError(f"{args.cmd}: not implemented (Phase 2 {_NOT_YET[args.cmd]})")
    raise UsageError(f"unknown command {args.cmd}")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = make_parser().parse_args(argv)
        return int(run(args))
    except UsageError as e:
        print(f"rco: {e}", file=sys.stderr)
        return int(Exit.USAGE)
    except SystemExit as e:  # --help / --version
        return int(e.code or 0)
    except Exception as e:  # noqa: BLE001 - the exit-3 contract
        if os.environ.get("RCO_DEBUG") == "1":
            raise
        print(f"rco: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return int(Exit.INTERNAL)
