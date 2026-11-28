"""Test selection from a diff: which tests can see a change (`rco affected`).

A test is selected when it is a changed file or reaches a changed file
through in-repo imports (Python, JS/TS, Go, Rust, shell; rco.imports and
rco.edges). The import graph is read at both BASE and HEAD, so a test that
imported a file the change deletes is still selected. Python imports also
resolve from the importing file's own directory, as pytest's default
rootdir-insertion mode sees them.

A changed file is also read "by path" by every code file whose text names
its file name (`ci/report.sh`, `data/cases.json`): a test that runs a
script or opens a data file selects itself, and a helper that does so
selects the tests that import it. This is a text match, so it can select
too much, never too little, for files named in full.

What neither can see makes the answer "run everything" (`run_all`), with
each reason listed:

- test config and infra (`pyproject.toml`, `pytest.ini`, `tox.ini`,
  `setup.cfg`, a top-level `conftest.py`, lock and requirements files,
  `package.json`, `go.mod`, `Cargo.toml`, `Makefile`, `rco.toml`, CI
  workflows, and each language's build files in `BUILD_FILES`: `*.csproj`,
  `*.sln`, `*.props`, `build.gradle(.kts)`, `pom.xml` and so on; see
  `is_infra`), even when a code file names one (an infra file under
  `fixtures/`, `testdata/`, `test_data/`, `golden/` or `__tests__/` is test
  data; one under `test/` or `tests/` is not);
- a changed file that is not code, not docs, and named by no code file.

Docs (`*.md`, `*.rst`, `*.adoc`, `LICENSE` and the like, anything under a
`docs/` or `doc/` directory) that no code file names select nothing; a PR
of only such docs needs no tests (`select` is "none"). Files under a test
directory (`tests/`, `fixtures/`, `testdata/`, ...) are never docs.
`--ignore GLOB` drops paths a repo knows no test reads. A changed nested
`conftest.py` selects every test under its directory. Imports computed at
run time, and files opened by a computed name or a glob, are not seen.

Test files: Python `test_*.py` and `*_test.py` (pytest's default); Go
`*_test.go`; JS/TS `*.test.*`, `*.spec.*` or under `__tests__/`; Rust
`.rs` directly in a `tests/` directory.
"""
from __future__ import annotations

import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence

from rco import gitread, imports
from rco.extract.langs import lang_of
from rco.modules import dir_of, glob_regex
from rco.output import Exit, counted

SCHEMA_AFFECTED = "rco.affected/1"
_TEST_DIRS = {"tests", "test"}
# a file under one of these is test data, never docs
_FIXTURE_DIRS = {"testdata", "test_data", "fixtures", "__tests__", "golden"}
_TEST_DATA_DIRS = _TEST_DIRS | _FIXTURE_DIRS
_DOC_DIRS = {"docs", "doc"}
_DOC_EXT = (".md", ".markdown", ".rst", ".adoc")
_DOC_NAMES = {"LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS", "CONTRIBUTORS"}
# test config and infra: a change here may change how every test runs
# Build and test config per language: a change here may change how every test runs, so
# it selects every test. A "*.x" entry is a suffix. A language with no entry must be in
# NO_BUILD_FILES (tests/test_affected.py walks every language in LANG_OF).
BUILD_FILES: dict[str, tuple[str, ...]] = {
    "python": ("pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "pytest.ini", "noxfile.py",
               ".coveragerc", "sitecustomize.py", "Pipfile", "Pipfile.lock", "poetry.lock",
               "uv.lock", "requirements*.txt", "requirements*.in", "constraints*.txt",
               "constraints*.in"),
    "js": ("package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "tsconfig.json"),
    "go": ("go.mod", "go.sum"),
    "rust": ("Cargo.toml", "Cargo.lock"),
    "java": ("pom.xml", "build.gradle", "build.gradle.kts", "settings.gradle",
             "settings.gradle.kts", "gradle.properties", "libs.versions.toml", "*.gradle",
             "*.gradle.kts"),
    "php": ("composer.json", "composer.lock", "phpunit.xml", "phpunit.xml.dist", "phpunit.dist.xml"),
    "csharp": ("*.csproj", "*.sln", "*.slnx", "*.props", "*.targets", "global.json",
               "nuget.config", "NuGet.Config"),
}
NO_BUILD_FILES = frozenset({"sh"})  # languages whose files carry no build manifest
_INFRA_NAMES = {"Makefile", "rco.toml"} | {
    n for names in BUILD_FILES.values() for n in names if "*" not in n}
_INFRA_SUFFIX = tuple(n[1:] for names in BUILD_FILES.values() for n in names
                      if n.startswith("*") and "*" not in n[1:])
_INFRA_GLOBS = tuple(n.split("*") for names in BUILD_FILES.values() for n in names
                     if "*" in n[1:])
_INFRA_PREFIX = ("jest.config.", "vitest.config.", "babel.config.")
_INFRA_DIRS = (".github/workflows/",)


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
    if lang == "java":  # Maven/Gradle src/test/java, and the usual *Test class names
        return (name.removesuffix(".java").endswith(("Test", "Tests", "IT", "TestCase"))
                or name.startswith("Test") or "test" in dirs or "tests" in dirs)
    if lang == "csharp":  # xUnit, NUnit, MSTest: *Tests.cs, or a *.Tests project directory
        return (name.removesuffix(".cs").endswith(("Test", "Tests", "TestCase", "Fixture"))
                or any(d.lower() in ("test", "tests") or d.endswith((".Tests", ".Test", ".UnitTests"))
                       for d in dirs))
    if lang == "php":  # PHPUnit: tests/ or *Test.php
        return name.removesuffix(".php").endswith(("Test", "Tests", "TestCase")) or "tests" in dirs or "test" in dirs
    if lang == "rust":  # cargo integration tests: <crate>/tests/*.rs
        return len(parts) >= 2 and parts[-2] in _TEST_DIRS
    return False


def is_infra(path: str) -> bool:
    """Test config or infra: a change selects every test (`run_all`)."""
    parts = path.split("/")
    name = parts[-1]
    if _FIXTURE_DIRS & set(parts[:-1]) and not path.startswith(_INFRA_DIRS):
        return False  # a fixture repo's pyproject.toml is test data; a build file in test/ is not
    if path == "conftest.py" or name in _INFRA_NAMES or path.startswith(_INFRA_DIRS):
        return True
    if name.endswith(_INFRA_SUFFIX):
        return True
    if any(name.startswith(a) and name.endswith(b) for a, b in _INFRA_GLOBS):
        return True
    return name.startswith(_INFRA_PREFIX)


def is_doc(path: str) -> bool:
    """Prose no test needs unless one names it: never a file under a test directory."""
    parts = path.split("/")
    name, dirs = parts[-1], set(parts[:-1])
    if dirs & _TEST_DATA_DIRS or is_code(path):
        return False
    return (name.lower().endswith(_DOC_EXT) or name.split(".")[0] in _DOC_NAMES
            or bool(dirs & _DOC_DIRS))


def readers_by_path(changed: Sequence[str],
                    sources: Iterable[Mapping[str, bytes]]) -> dict[str, tuple[str, ...]]:
    """Changed path -> code files whose text names its file name (not the file itself)."""
    names: dict[str, list[str]] = {}
    for p in changed:
        name = p.rsplit("/", 1)[-1]
        if not name.startswith("__"):  # __init__.py and the like are named everywhere
            names.setdefault(name, []).append(p)
    if not names:
        return {}
    alt = b"|".join(re.escape(n.encode()) for n in sorted(names, key=len, reverse=True))
    # bounded so `a.sh` does not match `data.sh` or `a.sh.bak`; a trailing `.` ends a sentence
    rx = re.compile(rb"(?<![\w.-])(" + alt + rb")(?!\.?[\w-])")
    found: dict[str, set[str]] = {}
    for src in sources:
        for path, text in src.items():
            for m in set(rx.findall(text)):
                for p in names[m.decode()]:
                    if p != path:
                        found.setdefault(p, set()).add(path)
    return {p: tuple(sorted(r)) for p, r in sorted(found.items())}


@dataclass(frozen=True, slots=True)
class Selection:
    files: tuple[str, ...]
    tests: tuple[str, ...]
    run_all: bool
    reasons: tuple[str, ...]
    by_path: tuple[tuple[str, tuple[str, ...]], ...] = ()  # changed path -> code files naming it
    docs: tuple[str, ...] = ()  # changed docs no code file names: they select nothing


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
    by_path: tuple[tuple[str, tuple[str, ...]], ...] = ()
    docs: tuple[str, ...] = ()

    @property
    def select(self) -> str:
        """What a CI job runs: "all" tests, the listed "tests", or "none"."""
        return "all" if self.run_all else "tests" if self.tests else "none"


def _reverse(graphs: Iterable[Mapping[str, tuple[str, ...]]]) -> dict[str, set[str]]:
    rev: dict[str, set[str]] = {}
    for g in graphs:
        for src, targets in g.items():
            for t in targets:
                rev.setdefault(t, set()).add(src)
    return rev


def selection(changed: Sequence[str], head_paths: Sequence[str],
              graphs: Sequence[Mapping[str, tuple[str, ...]]], ignore: Sequence[str] = (),
              sources: Sequence[Mapping[str, bytes]] = ()) -> Selection:
    """Select tests for changed paths; pure, for tests. sources: code text read by path."""
    pats = [glob_regex(g) for g in ignore]
    kept = [p for p in changed if not any(r.fullmatch(p) for r in pats)]
    rev = _reverse(graphs)
    by_path = readers_by_path(kept, sources)
    at_head = set(head_paths)
    tests: set[str] = set()
    reasons, docs = [], []
    work = [p for p in kept if is_code(p)]
    for p in kept:
        work.extend(by_path.get(p, ()))
        name = p.rsplit("/", 1)[-1]
        if is_infra(p):
            reasons.append(f"{p}: test config or infra, so every test may see it")
        elif name == "conftest.py":
            d = dir_of(p)
            tests |= {t for t in at_head if is_test(t) and (d == "." or t.startswith(d + "/"))}
        elif is_code(p) or p in by_path:
            pass
        elif is_doc(p):
            docs.append(p)
        else:
            reasons.append(f"{p}: not code and no code file names it, so its readers are unknown")
    seen: set[str] = set()
    while work:
        p = work.pop()
        if p in seen:
            continue
        seen.add(p)
        work.extend(sorted(rev.get(p, set()) - seen))
    tests |= {p for p in seen if is_test(p) and p in at_head}
    return Selection(tuple(sorted(seen)), tuple(sorted(tests)), bool(reasons),
                     tuple(sorted(reasons)), tuple(by_path.items()), tuple(sorted(docs)))


def select(changed: Sequence[str], head_paths: Sequence[str],
           graphs: Sequence[Mapping[str, tuple[str, ...]]],
           ignore: Sequence[str] = (), sources: Sequence[Mapping[str, bytes]] = ()
           ) -> tuple[tuple[str, ...], tuple[str, ...], bool, tuple[str, ...]]:
    """(files, tests, run_all, reasons) for changed paths; pure, for tests."""
    s = selection(changed, head_paths, graphs, ignore, sources)
    return s.files, s.tests, s.run_all, s.reasons


def _code_sources(repo: Path, entries: tuple[gitread.Entry, ...]) -> tuple[list[str], dict[str, bytes]]:
    from rco.edges import is_manifest
    ents = [e for e in entries if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)
            and (is_code(e.path) or is_manifest(e.path))]
    data = gitread.read_blobs(repo, [e.blob for e in ents])
    return [e.path for e in entries], {e.path: data[e.blob] for e in ents}


def affected(repo: Path, base: str | None, head: str | None, snapshot: str | None = None,
             ignore: Sequence[str] = ()) -> Affected:
    from rco.diff import changed_paths, resolve_range
    t0 = time.perf_counter()
    base_sha, head_sha = resolve_range(repo, base, head, snapshot)
    be, he = gitread.ls_tree(repo, base_sha), gitread.ls_tree(repo, head_sha)
    changed = changed_paths(be, he)
    graphs, texts = [], []
    for ents in (be, he):
        paths, sources = _code_sources(repo, ents)
        graphs.append(imports.file_graph(paths, sources, local=True))
        texts.append(sources)
    head_paths = [e.path for e in he]
    s = selection(changed, head_paths, graphs, ignore, texts)
    all_tests = tuple(sorted(p for p in head_paths if is_test(p)))
    print(f"rco affected: {counted(len(changed), 'changed file')}, "
          f"{counted(len(s.tests), 'test')}"
          f"{' (run all)' if s.run_all else ''} in {time.perf_counter() - t0:.3f}s", file=sys.stderr)
    return Affected(base_sha, head_sha, tuple(changed), s.files, s.tests, s.run_all, s.reasons,
                    all_tests, s.by_path, s.docs)


@dataclass(frozen=True)
class AffectedReport:
    result: Affected
    as_list: bool = False

    def to_obj(self) -> dict:
        r = self.result
        return {"schema": SCHEMA_AFFECTED, "base": r.base, "head": r.head,
                "changed": list(r.changed), "files": list(r.files), "tests": list(r.tests),
                "run_all": r.run_all, "reasons": list(r.reasons), "select": r.select,
                "by_path": {p: list(rs) for p, rs in r.by_path}, "docs": list(r.docs)}

    def to_markdown(self) -> str:
        r = self.result
        if self.as_list:  # one path per line, for `pytest $(rco affected --list)`
            return "".join(f"{p}\n" for p in (r.all_tests if r.run_all else r.tests)) or "\n"
        out = [f"# rco affected {r.base[:12]}..{r.head[:12]}", ""]
        if r.run_all:
            out += ["**Run all tests.** The import graph cannot see who reads:", ""]
            out += [f"- {x}" for x in r.reasons] + [""]
        elif not r.tests:
            out += ["**No tests needed.** Every change is ignored or docs no code file names.", ""]
        out += [f"## Tests ({len(r.tests)})", ""]
        out += [f"- `{t}`" for t in r.tests] or ["- none"]
        if r.by_path:
            out += ["", f"## Read by path ({len(r.by_path)})", ""]
            out += [f"- `{p}`: named in " + ", ".join(f"`{x}`" for x in rs) for p, rs in r.by_path]
        if r.docs:
            out += ["", f"## Docs, no tests ({len(r.docs)})", ""]
            out += [f"- `{p}`" for p in r.docs]
        out += ["", f"## Changed ({len(r.changed)})", ""]
        out += [f"- `{p}`" for p in r.changed] or ["- none"]
        return "\n".join(out) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK
