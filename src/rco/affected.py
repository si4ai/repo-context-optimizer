"""Test selection from a diff: which tests can see a change (`rco affected`).

A test is selected when it is a changed file or reaches a changed file
through in-repo imports (Python, JS/TS, Go, Rust, shell; rco.imports and
rco.edges). The import graph is read at both BASE and HEAD, so a test that
imported a file the change deletes is still selected. Python imports also
resolve from the importing file's own directory, as pytest's default
rootdir-insertion mode sees them.

What the graph cannot see makes the answer "run everything" (`run_all`),
with each reason listed: a changed file that is not code (data, config, a
fixture, docs a test reads). `--ignore GLOB` drops paths a repo knows no
test reads. A changed `conftest.py` selects every test under its directory.
Imports computed at run time are not seen; that is the one gap.

Test files: Python `test_*.py` and `*_test.py` (pytest's default); Go
`*_test.go`; JS/TS `*.test.*`, `*.spec.*` or under `__tests__/`; Rust
`.rs` directly in a `tests/` directory.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from rco import gitread, imports
from rco.extract.langs import lang_of
from rco.modules import dir_of, glob_regex
from rco.output import Exit

SCHEMA_AFFECTED = "rco.affected/1"
_TEST_DIRS = {"tests", "test"}


def is_code(path: str) -> bool:
    return path.endswith(".py") or bool(lang_of(path))


def is_test(path: str) -> bool:
    parts = path.split("/")
    name, dirs = parts[-1], set(parts[:-1])
    if name.endswith(".py"):  # pytest's default python_files
        return name.startswith("test_") or name.endswith("_test.py")
    if name.endswith("_test.go"):
        return True
    lang = lang_of(path)
    if lang == "js":
        stem = name.split(".")
        return "__tests__" in dirs or (len(stem) > 2 and stem[-2] in ("test", "spec"))
    if lang == "rust":  # cargo integration tests: <crate>/tests/*.rs
        return len(parts) >= 2 and parts[-2] in _TEST_DIRS
    return False


@dataclass(frozen=True, slots=True)
class Affected:
    base: str
    head: str
    changed: tuple[str, ...]
    files: tuple[str, ...]  # changed files plus everything that imports them, transitively
    tests: tuple[str, ...]  # selected test files that exist at HEAD
    run_all: bool
    reasons: tuple[str, ...]  # why run_all, one line per path
    all_tests: tuple[str, ...] = ()  # every test file at HEAD (what run_all means)


def _reverse(graphs: Iterable[Mapping[str, tuple[str, ...]]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {}
    for g in graphs:
        for src, targets in g.items():
            for t in targets:
                rev.setdefault(t, set()).add(src)
    return rev


def select(changed: Sequence[str], head_paths: Sequence[str],
           graphs: Sequence[Mapping[str, tuple[str, ...]]],
           ignore: Sequence[str] = ()) -> tuple[tuple[str, ...], tuple[str, ...], bool,
                                                tuple[str, ...]]:
    """(files, tests, run_all, reasons) for changed paths; pure, for tests."""
    pats = [glob_regex(g) for g in ignore]
    kept = [p for p in changed if not any(r.fullmatch(p) for r in pats)]
    rev = _reverse(graphs)
    seen: set[str] = set()
    work = [p for p in kept if is_code(p)]
    while work:
        p = work.pop()
        if p in seen:
            continue
        seen.add(p)
        work.extend(sorted(rev.get(p, set()) - seen))
    at_head = set(head_paths)
    tests = {p for p in seen if is_test(p) and p in at_head}
    reasons = []
    for p in kept:
        name = p.rsplit("/", 1)[-1]
        if name == "conftest.py":
            d = dir_of(p)
            tests |= {t for t in at_head if is_test(t) and (d == "." or t.startswith(d + "/"))}
        elif not is_code(p):
            reasons.append(f"{p}: not code, so its readers are unknown")
    return (tuple(sorted(seen)), tuple(sorted(tests)), bool(reasons), tuple(sorted(reasons)))


def _code_sources(repo: Path, entries: tuple[gitread.Entry, ...]) -> tuple[list[str], dict[str, bytes]]:
    from rco.edges import MANIFEST_NAMES
    ents = [e for e in entries if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)
            and (is_code(e.path) or e.path.rsplit("/", 1)[-1] in MANIFEST_NAMES)]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return [e.path for e in entries], {e.path: data[e.blob] for e in ents}


def affected(repo: Path, base: str | None, head: str | None, snapshot: str | None = None,
             ignore: Sequence[str] = ()) -> Affected:
    from rco.diff import changed_paths, resolve_range
    t0 = time.perf_counter()
    base_sha, head_sha = resolve_range(repo, base, head, snapshot)
    be, he = gitread.ls_tree(repo, base_sha), gitread.ls_tree(repo, head_sha)
    changed = changed_paths(be, he)
    graphs = []
    for ents in (be, he):
        paths, sources = _code_sources(repo, ents)
        graphs.append(imports.file_graph(paths, sources, local=True))
    head_paths = [e.path for e in he]
    files, tests, run_all, reasons = select(changed, head_paths, graphs, ignore)
    all_tests = tuple(sorted(p for p in head_paths if is_test(p)))
    print(f"rco affected: {len(changed)} changed files, {len(tests)} tests"
          f"{' (run all)' if run_all else ''} in {time.perf_counter() - t0:.3f}s", file=sys.stderr)
    return Affected(base_sha, head_sha, tuple(changed), files, tests, run_all, reasons, all_tests)


@dataclass(frozen=True)
class AffectedReport:
    result: Affected
    as_list: bool = False

    def to_obj(self) -> dict:
        r = self.result
        return {"schema": SCHEMA_AFFECTED, "base": r.base, "head": r.head,
                "changed": list(r.changed), "files": list(r.files), "tests": list(r.tests),
                "run_all": r.run_all, "reasons": list(r.reasons)}

    def to_markdown(self) -> str:
        r = self.result
        if self.as_list:  # one path per line, for `pytest $(rco affected --list)`
            return "".join(f"{p}\n" for p in (r.all_tests if r.run_all else r.tests)) or "\n"
        out = [f"# rco affected {r.base[:12]}..{r.head[:12]}", ""]
        if r.run_all:
            out += ["**Run all tests.** The import graph cannot see who reads:", ""]
            out += [f"- {x}" for x in r.reasons] + [""]
        out += [f"## Tests ({len(r.tests)})", ""]
        out += [f"- `{t}`" for t in r.tests] or ["- none"]
        out += ["", f"## Changed ({len(r.changed)})", ""]
        out += [f"- `{p}`" for p in r.changed] or ["- none"]
        return "\n".join(out) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK
