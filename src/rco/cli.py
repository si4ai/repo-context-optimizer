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
from rco.forest_model import Forest
from rco.model import Map
from rco.output import Exit, UsageError, emit, note


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
    t.add_argument("sha", nargs="?", default="HEAD", help="commit (default: HEAD)")
    t.add_argument("--for", dest="for_paths", nargs="+", default=None, metavar="PATH",
                   help="only the modules that own these paths")
    _common(t)

    c = sub.add_parser("check", help="check a map or forest against the code at a commit")
    c.add_argument("map", nargs="?", default=None,
                   help="map JSON (rco tree --format json) or forest JSON (rco forest --format json)")
    c.add_argument("sha", nargs="?", default=None, help="commit (default: the map's sha)")
    c.add_argument("--selftest", action="store_true", help="run the planted-drift self-test")
    _common(c)

    d = sub.add_parser("diff", help="contract changes between two commits")
    d.add_argument("base", nargs="?", default=None,
                   help="base commit (default: merge-base of the default branch and HEAD)")
    d.add_argument("head", nargs="?", default=None, help="head commit (default: HEAD)")
    snap = d.add_mutually_exclusive_group()
    snap.add_argument("--staged", dest="snapshot", action="store_const", const="staged",
                      help="HEAD is the index; BASE defaults to HEAD")
    snap.add_argument("--worktree", dest="snapshot", action="store_const", const="worktree",
                      help="HEAD is the working tree (tracked and untracked, not ignored); "
                           "BASE defaults to HEAD")
    _common(d)

    a = sub.add_parser("affected", help="tests that can see the changes between two commits")
    a.add_argument("base", nargs="?", default=None,
                   help="base commit (default: merge-base of the default branch and HEAD)")
    a.add_argument("head", nargs="?", default=None, help="head commit (default: HEAD)")
    snap = a.add_mutually_exclusive_group()
    snap.add_argument("--staged", dest="snapshot", action="store_const", const="staged",
                      help="HEAD is the index; BASE defaults to HEAD")
    snap.add_argument("--worktree", dest="snapshot", action="store_const", const="worktree",
                      help="HEAD is the working tree; BASE defaults to HEAD")
    a.add_argument("--ignore", action="append", default=[], metavar="GLOB",
                   help="changed paths no test reads (repeatable)")
    a.add_argument("--list", dest="as_list", action="store_true",
                   help="print only test paths, one per line (every test when run_all)")
    _common(a)

    i = sub.add_parser("init", help="set up rco.toml, a CI report job and (with --hooks) a hook")
    i.add_argument("--hooks", action="store_true", help="also install a post-merge hook")
    i.add_argument("--remove-hooks", action="store_true", help="remove rco's post-merge hook")
    i.add_argument("--no-config", dest="config", action="store_false", help="skip rco.toml")
    i.add_argument("--no-ci", dest="ci", action="store_false", help="skip the CI workflow")
    _common(i)

    lv = sub.add_parser("live", help="check the map against captured real outputs")
    lv.add_argument("sha", nargs="?", default="HEAD", help="commit (default: HEAD)")
    lv.add_argument("--schema", metavar="FILE", help="a SQL schema dump")
    lv.add_argument("--openapi", metavar="FILE", help="an OpenAPI or Swagger JSON document")
    lv.add_argument("--help-text", dest="help_texts", action="append", default=[],
                    metavar="CMD=FILE", help="a captured `CMD --help` (repeatable)")
    _common(lv)

    u = sub.add_parser("update", help="publish a commit to the store (rco.toml backend; default .rco/rco.db)")
    u.add_argument("new", nargs="?", default="HEAD", help="commit to publish (default: HEAD)")
    u.add_argument("--name", default="main", help="pointer name (default: main)")
    _common(u)

    k = sub.add_parser("pack", help="Forest plus Trees for an agent, within a token budget")
    k.add_argument("sha", nargs="?", default="HEAD", help="commit (default: HEAD)")
    k.add_argument("--for", dest="for_paths", nargs="+", default=None, metavar="FILE",
                   help="paths whose owning modules get full Trees and one import hop")
    k.add_argument("--budget", type=int, default=8000, help="estimated tokens (default: 8000)")
    _common(k)

    r = sub.add_parser("triage", help="hint per branch: clean or a structural brief (never rebases)")
    r.add_argument("branches", nargs="+", metavar="BRANCH",
                   help="branch to hint about (at least one)")
    r.add_argument("--onto", default=None, help="target ref (default: the default branch)")
    _common(r)

    n = sub.add_parser("notice", help="cross-PR contract notice: other branches using this PR's changes")
    n.add_argument("others", nargs="+", metavar="BRANCH",
                   help="other branch to compare (at least one)")
    n.add_argument("--base", default=None, help="this PR's base (default: merge-base)")
    n.add_argument("--head", default=None, help="this PR's head (default: HEAD)")
    _common(n)

    pl = sub.add_parser("plan", help="merge plan: what each branch changes that another uses")
    pl.add_argument("first", metavar="BRANCH", help="a branch to compare")
    pl.add_argument("rest", nargs="+", metavar="BRANCH",
                    help="more branches (at least one more; two or more in total)")
    pl.add_argument("--base", default=None,
                    help="compare every pair against this commit "
                         "(default: each pair's own merge-base)")
    _common(pl)

    f = sub.add_parser("forest", help="print the computed Forest of a commit")
    f.add_argument("sha", nargs="?", default="HEAD", help="commit (default: HEAD)")
    _common(f)

    pn = sub.add_parser("pin", help="pin the contract of files (commit it as the intended contract)")
    pn.add_argument("sha", nargs="?", default="HEAD", help="commit (default: HEAD)")
    pn.add_argument("--for", dest="for_paths", nargs="+", required=True, metavar="FILE")
    _common(pn)

    dr = sub.add_parser("drift", help="contract drift from committed pins (a report, not a block)")
    dr.add_argument("pins", nargs="+", type=Path, metavar="PIN", help="pin file or directory of *.json pins")
    dr.add_argument("--at", default="HEAD", help="commit to check (default: HEAD)")
    _common(dr)
    return p



def _resolve_sha(repo: Path, rev: str) -> str | None:
    """Commit SHA, or None when rev is HEAD and the repo has no commits yet."""
    if rev == "HEAD" and not gitread.has_commit(repo, "HEAD"):
        return None
    return gitread.resolve(repo, rev)


def _warn_unmatched_for(repo: Path, sha: str | None, for_paths: list[str] | None) -> None:
    """a typo in --for must not look like success; print a warning on stderr."""
    if not for_paths:
        return
    if sha is None:
        note("no module owns the --for paths: " + ", ".join(for_paths))
        return
    from rco import modules
    layout = modules.layout_at(repo, sha)
    known = {layout.module_of(e.path) for e in gitread.ls_tree(repo, sha)
             if e.mode != gitread.MODE_SUBMODULE}
    missed = [p for p in for_paths if layout.module_of(p.strip("/")) not in known]
    if missed:
        note("no module owns the --for paths: " + ", ".join(missed))


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


def _read_claim(path: str) -> Map | Forest:
    """A claimed rco.map/1 or rco.forest/1 file."""
    try:
        obj = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise UsageError(f"unreadable map {path}: {e}") from None
    schema = obj.get("schema") if isinstance(obj, dict) else None
    try:
        if schema == "rco.map/1":
            return canon.map_from_obj(obj)
        if schema == "rco.forest/1":
            from rco.forest_model import forest_from_obj
            return forest_from_obj(obj)
    except (ValueError, KeyError, TypeError) as e:
        kind = "map" if schema == "rco.map/1" else "forest"
        raise UsageError(f"unreadable {kind} {path}: {e}") from None
    raise UsageError(f"unreadable map {path}: schema must be rco.map/1 or rco.forest/1")


def run(args: argparse.Namespace) -> Exit:
    repo = Path(args.repo)
    if args.cmd == "tree":
        sha = _resolve_sha(repo, args.sha)
        _warn_unmatched_for(repo, sha, args.for_paths)
        if sha is None:
            return emit(MapReport(Map("HEAD", ())), args.format, args.out)
        return emit(MapReport(build.build_map(repo, sha, args.for_paths)), args.format, args.out)
    if args.cmd == "check":
        if args.selftest:
            return emit(check.selftest(), args.format, args.out)
        if args.map is None:
            raise UsageError("check needs MAP or FOREST or --selftest")
        claim = _read_claim(args.map)
        if isinstance(claim, Forest):
            sha = gitread.resolve(repo, args.sha or claim.sha)
            return emit(check.check(repo, sha, claimed_forest=claim), args.format, args.out)
        m = claim
        sha = gitread.resolve(repo, args.sha or m.sha)
        from rco.config import open_store
        store = open_store(repo, create=False)  # read only: check never creates a store
        return emit(check.check(repo, sha, m, store), args.format, args.out)
    if args.cmd == "diff":
        return emit(diff.diff(repo, args.base, args.head, args.snapshot), args.format, args.out)
    if args.cmd == "affected":
        from rco import affected, config
        ignore = (*config.load(repo).affected_ignore, *args.ignore)
        res = affected.affected(repo, args.base, args.head, args.snapshot, ignore)
        return emit(affected.AffectedReport(res, args.as_list), args.format, args.out)
    if args.cmd == "init":
        from rco import init
        if args.hooks and args.remove_hooks:
            raise UsageError("init takes --hooks or --remove-hooks, not both")
        rep = init.init(repo, args.config, args.ci, args.hooks, args.remove_hooks)
        return emit(rep, args.format, args.out)
    if args.cmd == "live":
        from rco import live
        sha = gitread.resolve(repo, args.sha)
        rep = live.live(build.build_map(repo, sha), args.schema, args.openapi, args.help_texts)
        return emit(rep, args.format, args.out)
    if args.cmd == "update":
        from rco import update
        from rco.prose import drain_queue
        from rco.config import open_store
        store = open_store(repo, create=True)
        res = update.update(repo, store, args.new, args.name)
        drain_queue(repo, store, args.name)  # mined prose: queue is not a separate CLI step
        return emit(update.UpdateReport(res), args.format, args.out)
    if args.cmd == "triage":
        from rco import triage
        return emit(triage.triage(repo, args.branches, args.onto), args.format, args.out)
    if args.cmd == "plan":
        from rco import plan
        return emit(plan.plan(repo, [args.first, *args.rest], args.base), args.format, args.out)
    if args.cmd == "notice":
        from rco import triage
        return emit(triage.notice(repo, args.base, args.head, args.others), args.format, args.out)
    if args.cmd == "forest":
        from rco import forest
        sha = _resolve_sha(repo, args.sha)
        if sha is None:
            return emit(forest.ForestReport(Forest("HEAD", ())), args.format, args.out)
        return emit(forest.ForestReport(forest.build_forest(repo, sha)), args.format, args.out)
    if args.cmd == "pack":
        from rco import pack
        sha = _resolve_sha(repo, args.sha)
        _warn_unmatched_for(repo, sha, args.for_paths)
        if sha is None:
            return emit(pack.PackReport(pack.empty_pack(args.budget),
                                       " ".join(args.for_paths or ())), args.format, args.out)
        from rco.config import open_store
        store = open_store(repo, create=False)  # read only: pack never creates a store
        p = pack.pack(repo, sha, args.for_paths, args.budget, store)
        return emit(pack.PackReport(p, " ".join(args.for_paths or ())), args.format, args.out)
    if args.cmd == "pin":
        from rco import pin
        sha = gitread.resolve(repo, args.sha)
        return emit(pin.make_pin(repo, sha, args.for_paths), args.format, args.out)
    if args.cmd == "drift":
        from rco import pin
        files = pin.pin_files(args.pins)
        sha = gitread.resolve(repo, args.at)
        return emit(pin.drift(repo, files, sha), args.format, args.out)
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
