# Phase 1 Tree contract

Card 1167 (`repo-context-optimizer/phase1-trees`). This is the shared
contract that the Phase 1 steps build against. Step 1 lands it in the repo
as `docs/design/tree-contract.md`, together with the frozen types, the stubs
and the Python Tree builder. Steps 2 to 6 can then run in parallel, and each
one edits only the files it owns (see [Ownership](#ownership)).

Scope comes from the README: Roadmap Phase 1, Concepts / Tree, Fingerprints,
Output contract, Secrets, and Verification layer 2. Phase 1 uses zero LLM
calls and zero network, and needs only the standard library plus git. The
same SHA in always gives the same bytes out.

Out of scope for Phase 1: the store (SQLite under `.rco/`), the pointer,
`rco update`, the Forest, import edges, `rco pack`, and `rco triage`.
Phase 1 computes every result on the fly from git.

## 1. Module layout

The repo has no Python package yet. `ci/test-select.toml` already routes
`src/**/*.py` to pytest, so the package goes under `src/` and the tests stay
in `tests/` (the same layout `tools/` uses today).

```
pyproject.toml                 step 1 (new). name repo-context-optimizer,
                               requires-python >=3.12, no runtime deps,
                               [project.scripts] rco = "rco.cli:main",
                               [tool.pytest.ini_options] pythonpath = ["src"],
                               testpaths = ["tests"]
src/rco/__init__.py            step 1. __version__ = "0.1.0.dev0"
src/rco/__main__.py            step 1. python -m rco -> cli.main()
src/rco/model.py               step 1. FROZEN types (section 2)
src/rco/canon.py               step 1. canonical JSON + hashes (section 3)
src/rco/gitread.py             step 1. read a SHA from git (section 5)
src/rco/modules.py             step 1. path -> module id (section 4)
src/rco/build.py               step 1. build_tree / build_map
src/rco/extract/__init__.py    step 1. registry, lists all four extractors
src/rco/extract/python.py      step 1. Python code contracts
src/rco/extract/sql.py         step 1 stub -> step 2
src/rco/extract/routes.py      step 1 stub -> step 3
src/rco/extract/cli.py         step 1 stub -> step 4
src/rco/redact.py              step 1 stub (identity) -> step 5
src/rco/output.py              step 1 minimal (Exit, emit) -> step 5
src/rco/check.py               step 1. map checker + planted-drift self-test
src/rco/diff.py                step 1 stub -> step 6
src/rco/cli.py                 step 1. argparse; tree, check, diff
src/rco/render.py              step 1. Markdown rendering of a Map
src/rco/fixture.py             step 1. scratch git repos (fixed identity and
                               dates) for the self-test and the tests
tests/test_<module>.py         one test file per src module
tests/fixtures/<name>/...      fixture repo trees (plain files)
tests/golden/<name>.json       expected canonical Map JSON per fixture
docs/design/tree-contract.md   this file
```

Step 1 also adds `pyproject.toml`, `tests/fixtures/**` and `tests/golden/**`
to the pytest rule in `ci/test-select.toml`, so changing a fixture runs the
tests. CI stays as it is: `pip install pytest`, then `pytest`. The
`pythonpath` setting means CI never needs to pip-install rco.

## 2. Data model (`rco/model.py`, frozen in step 1)

```python
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

class Kind(str, Enum):
    # order of declaration == sort order (KIND_ORDER)
    FILE = "file"            # outline fallback: unknown language, LFS pointer, parse error
    PYMODULE = "pymodule"    # one per .py file; children below
    SRCMODULE = "srcmodule"  # one per JS/TS, Go, Rust, shell or PowerShell file
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    FIELD = "field"          # annotated class attribute
    CONSTANT = "constant"    # UPPER_CASE module or class constant
    EXPORT = "export"        # __all__
    COMMAND = "command"      # CLI command or subcommand
    FLAG = "flag"            # CLI option or positional, child of COMMAND
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"        # child of TABLE or VIEW
    INDEX = "index"          # child of TABLE
    PROCEDURE = "procedure"  # stored procedure or function
    ROUTE = "route"          # HTTP route

KIND_ORDER: dict[Kind, int] = {k: i for i, k in enumerate(Kind)}

@dataclass(frozen=True, slots=True)
class Node:
    kind: Kind
    name: str                 # local name: "run", "--out", "users", "GET /api/v1/doing"
    path: str = ""            # repo-relative POSIX file path; set on top-level nodes only
    sig: str = ""             # canonical shape string (per kind, section 6)
    doc: str = ""             # first docstring or comment line; informational, redacted
    attrs: tuple[tuple[str, str], ...] = ()   # shape extras, sorted by key, unique keys
    children: tuple[Node, ...] = ()           # sorted with node_sort_key

@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str                 # repo-relative POSIX path
    blob: str                 # git blob id (hex)
    data: bytes               # raw bytes; extractors decode as UTF-8 with errors="replace"

@dataclass(frozen=True, slots=True)
class Module:
    id: str                   # section 4
    files: tuple[str, ...]    # sorted paths

@dataclass(frozen=True, slots=True)
class Tree:
    module: str
    module_hash: str          # "sha256:<hex>", section 3
    extractors: tuple[tuple[str, str], ...]   # (name, version), sorted by name; includes ("redact", v)
    nodes: tuple[Node, ...]   # top-level nodes, sorted
    # tree_hash is computed (canon.tree_hash), never stored on the object

@dataclass(frozen=True, slots=True)
class Map:
    sha: str                  # full 40-hex commit the map was built from
    trees: tuple[Tree, ...]   # sorted by module id
    notes: tuple[str, ...] = ()   # e.g. "skipped submodule vendor/x"; sorted
```

No node carries line numbers, timestamps or absolute paths. Line numbers
belong to the caller index of `rco diff` (step 6), which is not part of any
Tree.

### Ordering

```python
def node_sort_key(n: Node) -> tuple[int, str, str, str]:
    return (KIND_ORDER[n.kind], n.name, n.path, n.sig)
```

`build.py` sorts every `nodes` and `children` tuple with this key before
hashing, so extractors may return nodes in any order. `attrs` is sorted by
key. Strings compare by code point (Python `str` order).

### Identity (used by `check` and `diff`)

```python
def qualname(parent: str, n: Node) -> str  # "Cls.meth", "users.email", "rco diff --out"
def node_key(path: str, qn: str, kind: Kind) -> str:
    return f"{kind.value}:{path}:{qn}"
```

`model.walk(tree) -> Iterator[tuple[str, Node]]` yields `(key, node)`
depth-first. Each child inherits its top-level parent's `path`. The
qualname separator is `.` for Python and SQL members, and a space for CLI
(`rco diff` and `rco diff --out`). ROUTE and PROCEDURE are always top-level. A PYMODULE is a container: its
children's qualnames start fresh (`run`, `Store.get`), because the path
already names the file.
Two nodes can share a key (for example `typing.overload`); when they do,
both are kept, and `check` and `diff` compare them as a sorted list of
shapes.

**Shape** = `(sig, attrs)`. A change in shape is a contract change. A
change in `doc` alone is reported as `doc` and does not count as a finding
(exit 0).

## 3. Serialization and hashes (`rco/canon.py`)

```python
SCHEMA_TREE = "rco.tree/1"
SCHEMA_MAP = "rco.map/1"
SCHEMA_MODULEHASH = "rco.modulehash/1"

def node_to_obj(n: Node) -> dict      # omits fields equal to their default ("" or ())
def tree_to_obj(t: Tree) -> dict      # {"schema","module","module_hash","extractors","nodes"}
def map_to_obj(m: Map) -> dict        # {"schema","sha","notes","modules":[{tree obj + "tree_hash"}]}
def map_from_obj(o: dict) -> Map      # strict: unknown schema or keys -> ValueError

def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")

def sha256_tag(b: bytes) -> str:      # "sha256:" + hexdigest
def tree_hash(t: Tree) -> str:        # sha256_tag(canonical_json(tree_to_obj(t) minus "module_hash"))
def module_hash(module: Module, blobs: dict[str, str],
                extractors: tuple[tuple[str, str], ...]) -> str:
    return sha256_tag(canonical_json({
        "schema": SCHEMA_MODULEHASH, "module": module.id,
        "files": [[p, blobs[p]] for p in module.files],
        "extractors": [list(e) for e in extractors]}))
```

TreeHash leaves out `module_hash`, so an edit to a function body moves
ModuleHash but not TreeHash; only a contract change moves TreeHash.

In JSON, `attrs` becomes an object and `extractors` a list of
`[name, version]` pairs. The commit SHA appears only in the Map, never in
ModuleHash or TreeHash (README Fingerprints). A file the CLI writes is the
canonical JSON plus one trailing `\n`. `--format json` on stdout is the same
bytes. Hashes are always computed without that trailing `\n`.

ForestHash is Phase 2 and is not defined here.

## 4. Modules (`rco/modules.py`)

```python
def module_of(path: str) -> str    # package grain: the directory holding the file; root files -> "."
def group(paths: Iterable[str]) -> tuple[Module, ...]   # sorted by id
```

Phase 1 uses Phase 0's `package` grain (`tools/phase0_baseline.py
module_of(..., "package")`). Manifest detection, clustering and the
override file all come later (README Concepts / Module; the override format
is still an open question). Every tracked file belongs to exactly one
module, so the "file in no module" check passes by construction.

## 5. Reading git (`rco/gitread.py`)

All reads go through the git object store at a resolved SHA, never through
the working tree, so a dirty checkout cannot change the output.

```python
@dataclass(frozen=True, slots=True)
class Entry:
    path: str; mode: str; blob: str

def resolve(repo: Path, rev: str) -> str              # full 40-hex; bad rev -> UsageError
def ls_tree(repo: Path, sha: str) -> tuple[Entry, ...] # git ls-tree -r -z --full-tree; sorted by path
def read_blobs(repo: Path, blobs: Iterable[str]) -> dict[str, bytes]  # one git cat-file --batch
def merge_base(repo: Path, a: str, b: str) -> str
def default_branch(repo: Path) -> str                 # refs/remotes/origin/HEAD, else main, else master
```

- mode `160000` (submodule): skipped and recorded as the note
  `skipped submodule <path>`.
- mode `120000` (symlink): a FILE node with `attrs (("link","1"),)`. The
  target is not followed.
- A blob that starts with `version https://git-lfs.github.com/spec/v1`:
  a FILE node with `attrs (("lfs","pointer"),)`. It is not parsed.
- Every git call sets `LC_ALL=C` and `GIT_CONFIG_NOSYSTEM=1`, and uses
  `-c core.quotepath=off`.

## 6. Extractor plugin interface (`rco/extract/`)

```python
from typing import ClassVar, Protocol

class Extractor(Protocol):
    name: ClassVar[str]        # "python" | "sql" | "routes" | "cli"
    version: ClassVar[str]     # bump on ANY change to output bytes; feeds ModuleHash
    def claims(self, path: str) -> bool: ...          # by path only, e.g. suffix
    def extract(self, src: SourceFile) -> list[Node]: ...  # top-level nodes for this file

# rco/extract/__init__.py
EXTRACTORS: tuple[Extractor, ...] = (CliExtractor(), PythonExtractor(),
                                     RoutesExtractor(), SqlExtractor())  # sorted by name
def versions() -> tuple[tuple[str, str], ...]   # sorted (name, version) + ("redact", redact.VERSION)
```

Registration is a fixed tuple. Phase 1 has no entry points, which keeps
output independent of what is installed. Step 1 ships all four entries, so
steps 2 to 4 only replace their own module. A stub has `version = "0"`
and returns `[]`.

Rules every extractor follows. Tests enforce them:

1. **Pure.** Output depends only on `src.path` and `src.data`: no file
   system, environment, clock, network, randomness or subprocess. Running
   it twice gives equal results.
2. **Never raises on bad input.** On a parse failure it returns
   `[Node(Kind.FILE, basename, path, attrs=(("error","parse"),))]`.
   Several extractors can claim the same file (`.py` is claimed by
   python, routes and cli). Their nodes are concatenated, and duplicate
   FILE error nodes collapse into one.
3. **No line numbers and no positions** anywhere in a Node.
4. **Top-level nodes set `path`; children leave it as `""`.**
5. Strings come out raw. `build.py` applies `redact.redact_node`
   centrally, after extraction and before hashing. Extractors never redact.
6. A file no extractor claims produces one FILE node with no attrs. This
   is the outline fallback: an unknown language is not an error.

`build.py`:

```python
def build_tree(module: Module, files: Sequence[SourceFile]) -> Tree
def build_map(repo: Path, sha: str, only: Sequence[str] | None = None) -> Map
    # only = paths; keeps the modules that own them. None = every module.
```

### Per-kind shape (`sig`, `attrs`)

| Kind | Owner | name | sig | attrs (shape) |
|---|---|---|---|---|
| PYMODULE | python | dotted module (`src/` stripped, `__init__` dropped) | `""` | none |
| SRCMODULE | js, go, rust, shell | the file path | `""` | `lang` (`js`, `ts`, `go`, `rust`, `sh`, `powershell`); `module`=`esm` or `script` (JS/TS); `package` (Go) |
| CLASS | python | class name | `(Base1, Base2)` from `ast.unparse` of bases and keywords, or `""` | `decorators` = comma-joined unparsed decorators |
| FUNCTION / METHOD | python | def name | `(args) -> ret` from `ast.unparse(node.args)` and `returns`; a default that is not a literal of 40 chars or fewer becomes `...` | `async`=`1`; `decorators` |
| FIELD | python | attribute | annotation, unparsed | `value` (literal or `...`) |
| CONSTANT | python | NAME | annotation or `""` | `value` (literal of 80 chars or fewer, else `...`) |
| EXPORT | python | `__all__` | JSON list of names, in source order | none |
| COMMAND | cli | full command path, e.g. `rco diff` | `""` | `dynamic`=`1` when names are not literal |
| FLAG | cli | `--out` or the positional `dest` | sorted `k=v;...` over the literal kwargs action, choices, metavar, nargs, required, type | `default` |
| TABLE / VIEW | sql | table name, lower-cased unless quoted | `""` | none |
| COLUMN | sql | column name, lower-cased unless quoted | normalized type plus constraints, upper-case keywords, unquoted identifiers inside CHECK lower-cased, single spaces | `default` |
| INDEX | sql | index name; a table-level PRIMARY KEY is `PRIMARY`; an unnamed UNIQUE takes its first column's name | `UNIQUE (a,b)`, `(a,b)`, `PRIMARY (a,b)`, `FULLTEXT (a)` or `SPATIAL (a)` | none |
| PROCEDURE | sql | name, lower-cased unless quoted | `(IN a INT, OUT b TEXT)` normalized; a function appends ` RETURNS <type>` | none |
| ROUTE | routes | `METHOD /path` (method upper-case; one node per method; `WS` for a websocket, `ANY` when the methods cannot be resolved or are aiohttp `*`) | `""` | none |

SQL names: every unquoted identifier (table, view, column, index,
procedure, parameter, and a name inside a CHECK clause) is lower-cased.
A quoted name keeps its case and loses its quotes.

Routes quick-skip: `routes.extract` returns `[]` without parsing a file in
which no quote is directly followed by `/`, since no route literal is
possible there. Rule 2 still holds: the python extractor emits the
parse-error FILE node for such a file.

Public filter (python): Phase 0's `_is_public` rule, with private
classes, functions and members dropped. Only public classes get methods.
`doc` is the first line of the docstring for python, a `COMMENT` or
leading `--` line for sql, `help=` for cli, and the handler's first
docstring line for routes. Bump `version` whenever this table's output
changes for a kind you own.

The Python sig depends on `ast.unparse`, so golden files are pinned to the
CI Python (3.12). The tests check that output is byte-identical across two
runs and on a reordered input, not across Python versions.

## 7. Redaction (`rco/redact.py`)

```python
VERSION: str            # "0" in the step-1 stub; feeds ModuleHash via versions()
PLACEHOLDER = "<redacted>"
def redact_text(s: str) -> str          # pure, idempotent
def redact_node(n: Node) -> Node        # recursive; touches doc, sig of CONSTANT/FIELD/
                                        # FUNCTION/METHOD/CLASS/FLAG/COLUMN/PROCEDURE,
                                        # and attrs keys "value", "default", and "decorators"
```

Redaction never touches `kind`, `name`, `path`, route paths, flag names, or
table and column names (README Secrets). Step 5 adds the README list (PEM
blocks, bearer and API tokens, high-entropy strings, credential URLs,
credential-like assignments). Test secrets are assembled at runtime from
fragments so the repo's hygiene report does not flag the fixtures.

From redact VERSION 2 (step 5), `redact_text` also runs over FUNCTION and METHOD sigs, so a short secret literal kept as a parameter default never reaches a Tree; only secret-shaped substrings change.

From redact VERSION 3, `redact_text` also runs over CLASS, FLAG, COLUMN and PROCEDURE sigs and the `decorators` attr, so a secret-shaped literal in a class keyword, a decorator call, a flag choices or metavar, a CHECK clause, or a routine parameter DEFAULT never reaches a Tree.

## 8. Output contract (`rco/output.py`, `rco/cli.py`)

```python
class Exit(IntEnum):
    OK = 0          # clean
    FINDINGS = 1    # check mismatch, or diff found a contract change
    USAGE = 2       # bad args, bad rev, unreadable map
    INTERNAL = 3    # uncaught exception: one line on stderr, no traceback unless RCO_DEBUG=1

class UsageError(Exception): ...

class Report(Protocol):
    def to_obj(self) -> dict: ...        # rendered with canon.canonical_json
    def to_markdown(self) -> str: ...    # deterministic, ends with "\n"
    def exit_code(self) -> Exit: ...

def emit(report: Report, fmt: Literal["markdown", "json"], out: Path | None) -> Exit
    # out=None -> stdout. Otherwise write FILE.tmp in the same dir, then os.replace.
```

Global flags on every subcommand: `--repo PATH` (default `.`), `--format
markdown|json` (default `markdown`), and `--out FILE`. stdout carries only
the report. Progress, timings and notes go to stderr. The CLI catches
`UsageError` and argparse errors and exits 2. It catches any other
exception and exits 3.

Phase 1 commands:

| Command | Report | Exit 1 when |
|---|---|---|
| `rco tree [SHA] [--for PATHS...]` | the Map (`rco.map/1`) | never |
| `rco check MAP or FOREST [SHA]` | `rco.check/1` | any finding |
| `rco check --selftest` | planted-drift result (`rco.selftest/2`) | any planted drift missed |
| `rco diff [BASE] [HEAD]` | `rco.diff/1` | any added, removed or changed shape |

`SHA` defaults to `HEAD`. `rco diff` defaults follow the README:
`BASE = merge-base(default branch, HEAD)` and `HEAD = HEAD`. For `rco
check`, `SHA` defaults to the map's or forest's own `sha`.

### `rco.check/1` (step 1)

```python
@dataclass(frozen=True, slots=True)
class Finding:
    status: Literal["missing", "extra", "changed", "stale", "edge"]
    key: str        # node key, or "module:<id>" for stale
    path: str
    detail: str     # "sig: '(a)' -> '(a, b)'", "module_hash", "map sha != SHA"
```

The checker re-extracts at SHA, rebuilding the map with `build_map`, and
compares the result with the given map. `extra` means the map claims a node
the code does not have. `missing` means the code has a node the map lacks.
`changed` means the key is the same but the shape differs. `stale` means
the module hash or the SHA does not match. When the file is an
`rco.forest/1`, `rco check` compares that Forest's edges with the Forest
computed from the code (`rco forest`) and reports `edge` findings. Findings are sorted by
`(path, key, status)`. The self-test builds the map from
`fixture.DRIFT_FILES` (a wheel-safe in-memory fixture in `src/rco/fixture.py`) and plants these errors: remove a flag, rename a
column, change a sig, add a function, drop a module, and alter a
module_hash. Every plant lands on a real node from the python, cli and
sql extractors; there are no stand-in nodes (`rco.selftest/2` dropped the
`standin` field). It fails if any planted error goes unflagged. Elapsed time
goes to stderr only. The time limit is recorded, with the RCO SHA, in
`docs/baseline/phase1-checker.md`. Comparing against real outputs (`--help`,
the live schema, the route table) is a later step of the checker.

### `rco.diff/1` (step 6)

```python
@dataclass(frozen=True, slots=True)
class Change:
    op: Literal["added", "removed", "changed", "doc"]
    key: str
    kind: str
    path: str
    before: str      # canonical JSON of shape, "" for added
    after: str       # "" for removed
    callers: tuple[str, ...]   # sorted "file:line" at HEAD; Python names only in Phase 1
```

Changes are sorted by `(path, key, op)`. A `doc` change never sets exit 1.
The caller index is built from Python `ast` Name and Attribute references
at HEAD. It lives only in the diff output and never in a Tree.

## Ownership

Each step's PR edits only its own files, plus its tests and fixtures. The
one exception is version bumps, which happen inside the extractor's own
module.

| Step | Owns |
|---|---|
| 1 | everything in section 1 marked step 1, `pyproject.toml`, `ci/test-select.toml`, this doc |
| 2 | `src/rco/extract/sql.py`, `tests/test_extract_sql.py`, `tests/fixtures/sql_*` |
| 3 | `src/rco/extract/routes.py`, `tests/test_extract_routes.py`, `tests/fixtures/routes_*` |
| 4 | `src/rco/extract/cli.py`, `tests/test_extract_cli.py`, `tests/fixtures/cli_*` |
| 5 | `src/rco/redact.py`, `src/rco/output.py`, `tests/test_redact.py`, `tests/test_output.py` |
| 6 | `src/rco/diff.py`, the `diff` branch of `cli.py`, `tests/test_diff.py` |

Changing a frozen type in `model.py` or `canon.py` is a step-1 follow-up
PR, and the change must go to every open step first. Golden files under
`tests/golden/` are regenerated only by the step that changed an output, and
the PR says why.

## Determinism tests (step 1, every step extends them)

- Build the Map twice for the same SHA and get the same bytes.
- Feed the files in reversed order and get the same bytes.
- Change only a function body and see the same TreeHash but a different
  ModuleHash.
- Change a signature and see a different TreeHash.
- Run the same contract at two different commit SHAs and get identical
  TreeHash and ModuleHash.
- Check that no key or value in a Map holds a line number. The test pattern
  `"line"` must not appear in any node key.

## Amendments after the freeze

Applied in the steps 2 to 6 fold (card 1167):

- Applied: TreeHash leaves out `module_hash` (section 3).
- Applied: SQL INDEX sig forms `PRIMARY (a,b)`, `FULLTEXT (a)`,
  `SPATIAL (a)`; an unnamed UNIQUE takes its first column's name (section 6).
- Applied: a SQL function's PROCEDURE sig appends ` RETURNS <type>`
  (section 6).
- Applied: every unquoted SQL name is lower-cased (section 6).
- Applied: ROUTE methods `WS` and `ANY` (section 6).
- Applied: routes quick-skip for files with no route literal (section 6).
- Applied: redact VERSION 2 runs `redact_text` over FUNCTION and METHOD
  sigs (section 7).
- Applied: redact VERSION 3 runs `redact_text` over CLASS, FLAG, COLUMN
  and PROCEDURE sigs and the `decorators` attr (section 7).
- Applied: unquoted identifiers inside a COLUMN CHECK clause are
  lower-cased (section 6).
- Applied: the self-test plants every drift on a real node and its report
  is `rco.selftest/2` (section 8).
- Deferred: an optional `aliases` attr on FLAG and COMMAND (short option
  strings, subparser aliases). It changes cli output, the cli golden and
  the parity check against the mill's parser, so it is not a trivial fold
  change; it goes to a later step.

Applied in 0.1.2 (bundled parsers for other languages):

- Applied: Kind `SRCMODULE`, a file container like PYMODULE, for the
  `js`, `go`, `rust` and `shell` extractors (sections 2 and 6). Their
  children reuse CLASS (with a `kind` attr: `class`, `interface`, `type`,
  `enum`, `struct`, `trait`, `impl`, `alias`, `mod`), FUNCTION, METHOD,
  FIELD, CONSTANT and EXPORT. The extractors share one deterministic
  scanner (`rco/extract/scan.py`): it masks comments and strings, tracks
  brace depth, and reads top-level declarations only (plus the body of a
  top-level IIFE in a JS script). There is no tree-sitter dependency, so
  `pip install` stays dependency-free and the same bytes come out
  everywhere. Public means: exported (JS/TS modules) or every top-level
  name not starting with `_` (JS scripts); capitalized (Go); `pub` (Rust);
  every function (shell, PowerShell). The doc is the first line of the
  comment right above a declaration.

Applied in 0.1.2 (modules from manifests and an override file):

- Applied: `module_of(path, layout=None)` and `group(paths, layout=None)`
  take a `Layout` read from git at the same SHA (`layout_at`), so the same
  commit always gives the same modules. The order is: `rco.toml`
  `[[module]]` rules (`id`, `paths` globs, first match wins), then
  `package.json` (JS/TS files) and `Cargo.toml` with `[package]` (Rust
  files), each down to the next nested manifest, then the directory
  grain (section 4). A bad override entry becomes a Map note, never an
  error. With no `rco.toml` and no such manifest, every module id is
  unchanged.

Applied in 0.1.2 (staged and working-tree input, test selection):

- Applied: `rco diff --staged|--worktree [BASE]` (section 8). HEAD is a
  commit object that `gitread.snapshot` writes for the index or the
  working tree, with fixed author, committer and dates; BASE defaults to
  HEAD. Readers still see only git objects (section 5).
- Applied: `rco affected [BASE] [HEAD] [--staged|--worktree] [--ignore
  GLOB] [--list]` reports `rco.affected/1` (`base`, `head`, `changed`,
  `files`, `tests`, `run_all`, `reasons`) and always exits 0.
