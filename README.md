# repo-context-optimizer

Deterministic repo context for coding agents: contract Trees, an architecture Forest, contract diffs, and rebase triage. The goal is to replace LLM tokens with code wherever a contract can be computed.

**Status:** 0.1.3 is the current release. 0.1.1 was merged but never tagged or published to PyPI, so 0.1.2 was the first install after 0.1.0 and carried both. `pip install repo-context-optimizer` installs the `rco` command. Everything in it is deterministic: the LLM prose path is off, and mined or derived prose fills in. `rco triage`, `rco notice`, `rco plan`, and `rco affected` are hints only (they never rebase, push, or block). The cold-agent A/B that counts waits until this install is the one under test. See [What ships](#what-ships) for what works and what is still design.

Builder and grok-tui split the development work, Auditor checks the design and models, and Validator passes and merges each PR.

### If you are an agent

Run `rco pack --for <files you will touch>`.
Read the pack.
Open raw files only on demand.

## What ships

| Code | Capability | 0.1.3 |
|---|---|---|
| TR, FO, PK, DF, CK, UP | `rco tree`, `forest`, `pack`, `diff`, `check`, `update` | shipped |
| PN, DR, TG, NT | `rco pin`, `drift`, `triage`, `notice` | shipped |
| LG | bundled parsers for JavaScript/TypeScript, Go, Rust, shell, and PowerShell | shipped |
| MO | modules from `rco.toml`, then `package.json` / `Cargo.toml`, then directories | shipped |
| CF | `rco.toml` settings and backend choice (`sqlite` or an entry point adapter) | shipped |
| SS | a store cached across CI runs | shipped |
| SG | `rco diff --staged` / `--worktree` | shipped |
| TS | `rco affected`: test selection from a diff | shipped |
| IN | `rco init`: config, CI workflow, and hook setup | shipped |
| HK | post-merge hook trigger for `rco update` | shipped (a merge webhook needs your own server) |
| RO | `rco live`: check against a schema dump, OpenAPI, or `--help` | shipped |
| LP | LLM prose | not shipped; off by design |
| MB | mill backend extra | not shipped |
| NM | notices sent by mail through a backend | not shipped |
| TW | `tw rco update` recorded runs | not shipped |
| MC | MCP backend | not shipped |
| MP | `rco plan`: simple merge planning across branches | shipped (simple; a plan only, never merges; [the rest is a proposal](#merge-planning-beyond-rco-plan)) |

## Why

Cold-start agents waste tokens rediscovering a repository: they read too many files, guess at module boundaries, and re-learn contracts that code already knows. Parallel agents on a busy repo pay again in rebase churn, often rereading full diffs when only a contract changed, or waking an agent when no contract they use has moved.

RCO's rule: **if code can compute it, code computes it.** CPU, memory, and disk are cheaper than tokens. LLM use is minimized and optional. `rco` is designed to work with zero tokens.

## Just works

The default in 0.1.3:

```
pip install repo-context-optimizer   # installs the `rco` command
cd any-git-repo
rco pack                   # SHA=HEAD; no --for: whole-repo Forest only; --budget 8000
rco pack --for src/cli.py  # Forest + Trees for those modules and one import hop
rco diff                   # BASE=merge-base(default branch, HEAD); HEAD=HEAD
```

`--budget` is in estimated tokens. The estimator is approximate (characters / 4). The design default is 8000. `--for` is optional. These defaults match the [Commands](#commands) section.

Zero config is the goal: the SQLite backend is the default, with no `rco.toml` required. Cache lives in SQLite under `.rco/`, which rco adds to `.git/info/exclude`, never to `.gitignore`. Modules are detected automatically. Parsers for Python, SQL, JavaScript and TypeScript, Go, Rust, shell, and PowerShell come bundled. An unknown language falls back to a file outline instead of an error. No LLM, keys, or network are required.

`rco.toml`, other backends, adapters, and MCP are all opt-in. If a configured backend is not installed, rco exits 2 and names it; it never silently switches to another store.

### Acceptance tests (zero-config goal)

| Repo | Expected behavior |
|---|---|
| Small Python repo | Modules detected from the package layout; Trees extracted; `rco pack` and `rco diff` run with the defaults above. |
| Polyglot repo | Bundled parsers used where available; unknown languages fall back to a file outline, not an error. |
| Large monorepo | Pack respects `--budget`; `rco update` re-extracts only modules whose tree ids changed. Cost and hit rate are Phase 0 measurements. |
| Repo with submodules | Submodules are skipped, with a note in the output. |
| LFS repo | LFS pointer files are treated as opaque: not fetched, not parsed as the real blob. |
| Empty repo | Empty pack and tree, exit 0, including a repo with no commits. |

## Concepts

```mermaid
flowchart LR
    R[repo at a SHA] --> M[Modules]
    M -->|parse, no LLM| T[Trees<br/>micro-contracts]
    T -->|import graph| F[Forest<br/>macro architecture]
    F -.->|optional, lazy| P[Forest prose]
    T --> K[rco pack]
    F --> K
    P -.-> K
```

### Module

A module is a logical unit of the repo, not necessarily a directory.

Detection order, shipped today:

1. A checked-in override file (`rco.toml`) can pin boundaries; the detector only falls back where that file is silent.
2. Language manifests: a `package.json` owns the JS/TS files under it, and a `Cargo.toml` with a `[package]` table owns the Rust files under it. Each manifest stops at the next nested one, and files in other languages there stay at the directory grain.
3. Directory fallback for whatever remains. Python files keep the directory grain (one module per package directory). Go packages are already directories.

`pyproject.toml`, `go.mod`, `pom.xml`, and similar are not used as module roots. Import-graph clustering (community detection on the import graph) is not built yet. Size caps on the directory fallback are not built yet. Every file maps to exactly one module, so the "file in no module" check passes by construction.

The override file is `rco.toml` at the repo root, read at the same commit as the code:

```toml
[[module]]
id = "frontend"
paths = ["web/**", "shared/**/*.ts"]
```

The first rule whose glob matches a path owns it. `*` and `?` stay inside one path segment, and `**` spans segments. A bad entry (no `id`, no `paths`, invalid TOML) becomes a note in the output, never an error.

### Tree (micro-contracts)

A Tree is a deterministic contract extracted from a module. It is never written by an LLM.

- **Code contracts:** language-native skeletons with function and method bodies stripped, via Python's `ast` module and bundled deterministic scanners for JavaScript and TypeScript, Go, Rust, shell, and PowerShell (no tree-sitter or other dependency). Public classes and functions, signatures, type hints, the first docstring or comment line, constants, and `__all__` (or the language's equivalent: JS/TS exports, Go capitalized names, Rust `pub`).
- **Non-code contracts,** which often matter more across a repo than signatures alone: CLI commands and flags, SQL schema tables and columns, HTTP routes, and message or event shapes.

Output is canonical (sorted JSON, no line numbers), so identical input always yields byte-identical output. The Tree hash is the sha256 of that JSON.

### Forest (macro architecture)

A Forest describes how modules fit together. Most of it is computed, not generated: layer; depends-on and used-by, from the import graph; entry points; and the commands, tables, and routes a module owns.

Prose is deterministic by default. It is mined first (docstrings, module headers, READMEs). Where nothing can be mined, it is derived from extracted facts: a module line from the names its Tree defines and owns, and, in a pack, a line per class or function with no docstring from its name, signature, the in-repo symbols it calls, and its in-repo callers (resolved through imports, never guessed from a bare method name). Derived lines are marked `derived` and cite only names in the Tree, so the name-in-Tree check holds. A small, optional LLM prose part may add purpose, invariants, and gotchas; it is off, and rco works without it. Generation rules live under [Phase 2](#roadmap). A repo-level Forest can render the module graph (for example as a mermaid diagram) plus a short paragraph per layer.

## Fingerprints

Hashes are keyed by content, not by commit.

- **ModuleHash** = the module's own content (git's tree object id for the module's paths, or sha256 of its file hashes) plus the extractor version. **The commit SHA is not part of this key:** including it would invalidate every module on every merge.
- **TreeHash** = sha256 of the canonical Tree JSON.
- **ForestHash** = TreeHash plus prompt version plus model id, so a prompt or model change invalidates only the prose.
- A separate index maps `commit SHA -> module hashes`. A new commit mostly points at existing cache entries. A lookup by tree hash on a miss still resolves identical module states across rebased histories.

Generated summaries are not committed to the default branch. Generated files in the repo would themselves cause rebase conflicts, which is the problem this tool is meant to avoid.

## Incremental update after each merge

The cost of each merge is meant to scale with what the merge changed, not with the size of the repo. Git already says which files moved. How many modules a typical merge touches, and how long a re-extract takes, are Phase 0 measurements.

```mermaid
flowchart TD
    A[merge lands: OLD to NEW] --> B["git diff --name-only OLD NEW<br/>map files to modules"]
    B --> C{module git tree id<br/>changed?}
    C -->|no| S[skip module]
    C -->|yes| D[re-extract Tree<br/>parse, no LLM]
    D --> E{TreeHash changed?}
    E -->|no, internal change| G[copy Forest forward to NEW]
    E -->|yes, contract moved| H[recompute import edges<br/>Forest now reads as stale]
    S --> I
    G --> I
    H --> I{every module of NEW<br/>in the store?}
    I -->|no| D
    I -->|yes| J["publish pointer<br/>one atomic compare-and-swap"]
    J --> K[queue debounced prose rewrites]
    J --> L[contract diff, rco triage hints,<br/>contract-change notices]
```

The same update in order, showing where the four SPIN rules sit:

```mermaid
sequenceDiagram
    participant M as merge
    participant U as rco update
    participant S as store
    participant P as pointer
    participant Q as prose queue
    M->>U: OLD to NEW
    U->>S: write Trees for changed modules
    U->>S: check every module of NEW is present
    Note over U,S: rule 2: complete SHAs only
    U->>P: compare-and-swap OLD to NEW
    Note over U,P: rule 1: one atomic pointer write
    alt pointer still reads OLD
        P-->>U: swapped to NEW
        U->>Q: queue prose rewrites
        Note over U,Q: rule 4: prose after the pointer
    else pointer already moved past OLD (late finisher)
        P-->>U: swap refused
        Note over U,P: write nothing, the newer update already covers NEW or later
    end
    Note over P,Q: rule 3: packs compute staleness from TreeHashes
```

Staleness is never stored: a pack sees a stale Forest because its TreeHash no longer matches the current Tree's. Several merges in a short window share one prose rewrite per module.

Properties, checked by the SPIN model in `verify/spin/` (see [Verification](#verification)):

- **The pointer write is one atomic compare-and-swap:** move `main` from OLD to NEW only if it still reads OLD and NEW descends from it (for example `git update-ref` with the old value, or a conditional database update). A separate "check, then write" is not enough: two updaters can both pass the check, and a late update then moves the pointer backwards.
- **Publish only a complete SHA:** before the pointer write, check every module of the new SHA against the store, not only the modules in the diff. Otherwise a slow or crashed earlier update leaves a module unbuilt and the pointer moves past it for good.
- **Staleness is computed, not stored:** a pack compares each Forest's TreeHash with the current Tree's. A stored stale flag can be left looking fresh by a race.
- **Queue the prose rewrite after the pointer write,** so a rewrite never describes a Tree that is already out of date and leaves its Forest stale for good.
- Idempotent and content-addressed: running the same update twice equals running it once. There is no hold: mill holds do not auto-expire, so a lock taken for regeneration could be left stuck.
- The new SHA's pointer moves as soon as its Trees are complete. Forest prose catches up asynchronously. A stale Forest is shown as stale, not as if it were current.

Triggers: a git `post-merge` hook, a CI job on push to the default branch, or a merge webhook. `rco init --hooks` installs the hook, and `rco init` writes the CI job. A webhook needs a server, which rco does not ship: point one at the same `rco update --name BRANCH` call.

## Commands

`rco tree`, `rco check`, `rco diff`, `rco pack`, and `rco update` have landed; see [Just works](#just-works). The [output contract](#output-contract) applies to every shipped command. `rco triage` and `rco notice` (phase 5, hints only) have landed, and so has `rco plan` (simple merge planning, a plan only). Phase 4 adds `rco pin` and `rco drift` (pinned contracts, a report and never a block; see [the pin contract](https://github.com/si4ai/repo-context-optimizer/blob/main/docs/design/pin-contract.md)). Defaults match the [quick start](#just-works).

### `rco pack [SHA] [--for FILES] [--budget N]`

- `SHA` defaults to `HEAD`. A repository with no commits still yields an empty pack and exit 0.
- `--for FILE...` is optional. If omitted, the pack is the whole-repo Forest only. If present, the pack is the repo Forest, full Trees for the modules that own those paths, and one import hop out as a name and one line. Hop lists put test modules after non-test modules. A `--for` path that matches no module is a warning on stderr; the command still exits 0 and the pack is unchanged (the whole-repo Forest when nothing matched).
- `--budget N` is in estimated tokens (characters / 4, approximate). Design default: 8000. Cuts start with hop lines, but a hop line is put back when later cuts leave room for it, so the One hop section appears when it fits.

The pack is the map; raw files are fetched on demand.

Which command reads and writes what:

```mermaid
flowchart LR
    G[git history] --> UP[rco update]
    UP -->|Trees, Forest, pointer| ST[(store)]
    ST --> PK[rco pack]
    ST --> DF[rco diff]
    G --> DF
    DF -->|contract changes| TR[rco triage]
    B[open branches] --> TR
    PK --> A[agent]
    TR -->|clean or structural brief| A
```

#### Pack output (example)

This sample is about 150 estimated tokens (characters / 4; the estimator is approximate).

```markdown
# rco pack  (SHA=abc1234, budget=8000 estimated tokens)

## Forest
- tw: desk CLI and process runner (depends on: proc, client)
- server: HTTP API and schema (used by: tw, wall)
- wall: browser UI

## Trees (full, for --for tw/cli.py)
### tw
def run(argv: list[str]) -> int  # Run one tw command and return its exit code.
def stop_run(run_id: str) -> None  # Stop run. Takes run_id. Calls kill. Called by cli.py:main.
CLI: tw run, tw stop

## One hop (name and one line)
- proc: process supervision for tw runs
- client: shared client library

# budget used: ~150 / 8000 estimated tokens
```

### `rco tree [SHA] [--for PATHS]`

`SHA` defaults to `HEAD`. Prints the Map (the Trees) of that commit. `--for` limits the Map to the modules that own those paths; a path that matches no module is a warning on stderr, and the command still exits 0. A repository with no commits yields an empty Map (`modules=0`) and exit 0. `--format json` writes the Map JSON that `rco check` takes as MAP.

### `rco forest [SHA]`

`SHA` defaults to `HEAD`. Prints the computed Forest of that commit. `--format json` writes the Forest JSON that `rco check` takes as FOREST. A repository with no commits yields an empty Forest and exit 0.

### `rco check MAP|FOREST [SHA] [--selftest]`

Checks a claimed map or forest against the code at a commit. MAP is the output of `rco tree --format json`. FOREST is the output of `rco forest --format json`. `SHA` defaults to the SHA recorded in that file. `--selftest` runs the planted-drift self-test and needs no file. Exit 0 if the claim matches the code, 1 on findings, 2 if no file and no `--selftest` are given.

### `rco diff [BASE] [HEAD]`

`BASE` defaults to the merge-base of the repository's default branch and `HEAD`. `HEAD` defaults to `HEAD`.

Lists added, removed, and changed signatures, flags, columns, and routes. For each changed class, function, method, field, or constant, it lists callers as `file:line`. A caller counts only when it resolves to that symbol. In Python, that means through imports, `self`, an annotation, an assignment from a constructor or from a function whose declared return type is the class, or a constructor keyword. An attribute on a receiver rco cannot resolve is not counted, so in Python rco can miss a caller but never invents one. In JS/TS, Go, Rust, shell, and PowerShell, a top-level name counts as a whole-word use in the defining file or in a file that imports it (for Go, also the same package), and a member counts as `.name` (or `::name` in Rust) on any receiver in those files; that can invent a caller, for example `cfg.timeout` in a file that imports a class with a `timeout` field.

`rco diff --staged [BASE]` diffs the index and `rco diff --worktree [BASE]` diffs the working tree (tracked changes plus untracked files that are not ignored). With either flag, `BASE` defaults to `HEAD`. rco still reads only git objects. It first writes the index or working tree as a commit object with fixed author, committer, and dates, so the same content always gives the same SHA. Only loose objects are written: no ref moves, and the real index is never touched.

### `rco affected [BASE] [HEAD]`

Selects tests from a diff. A test is picked when it changed itself or reaches a changed file through in-repo imports in any supported language. Imports are read at both `BASE` and `HEAD`, so a test that imported a deleted file is still picked. Python imports also resolve from the importing file's own directory, as pytest sees them. A changed `conftest.py` picks every test under its directory.

A changed file that is not code (data, config, fixtures, docs a test reads) sets `run_all`, and the reason is listed. `--ignore GLOB` drops paths that you know no test reads. `--staged` and `--worktree` work as in `rco diff`. `--list` prints only the test paths, one per line, and every test when `run_all` is set. That makes `pytest $(rco affected --list)` safe: with an empty list, pytest runs everything. Imports computed at run time are not seen. Test files are `test_*.py`, `*_test.py`, `*_test.go`, `*.test.*`, `*.spec.*`, anything under `__tests__/`, and `.rs` files directly in a `tests/` directory. Always exits 0.

### `rco triage`

Triage never rebases, merges, or pushes anyone's branch. For each open branch it reports either `clean: no changed file, no changed contract used` or a structural brief, as a hint. The branch owner or a merge-main step acts on that hint.

Usage: `rco triage BRANCH... [--onto TARGET]` (at least one BRANCH; TARGET defaults to the default branch). The cross-PR contract notice is `rco notice BRANCH... [--base BASE] [--head HEAD]` (at least one other BRANCH): it lists this PR's contract changes once, then, per other branch, the files that branch changed that use one of those contracts and the files changed on both sides. The wording is a neutral hint about this PR, not an upstream structural brief. It names branches, not desks, and always exits 0, so a CI job running it is a visible hint, never a block. In CI ([`ci/notice_report.sh`](https://github.com/si4ai/repo-context-optimizer/blob/main/ci/notice_report.sh)), BASE is the merge-base of the PR base tip and head, and only open PRs into the same base branch are compared. Both find callers with the same qualified index as `rco diff`, in Python, JS/TS, Go, Rust, shell, and PowerShell. CLI, SQL, and route changes show up as files changed on both sides.

An unchanged contract can still hide a behavior change, so tests still run (`rco affected` picks which). The hypothesis, to be measured in Phase 5, is that many branches are contract-clean and need no structural update.

### `rco plan BRANCH BRANCH... [--base BASE]`

Simple merge planning, deterministic and read-only: it never merges, rebases, or pushes. Each pair is compared against that pair's own merge-base, unless `--base` is given, in which case every pair uses that commit. The per-branch overview is still read against the common merge-base of all the branches (or `--base`). For each branch it reads the changed files, the contract changes (as `rco diff` reports them, without doc-only changes), the modules whose Tree changed, and the Forest of the branch head. Then, for each ordered pair (A, B), it reports what A changes that B uses:

- **files changed on both:** where a textual merge can conflict.
- **uses:** lines in B's changed files that reference a symbol whose contract A changed or removed, resolved by the same qualified index as `rco diff` (Python, JS/TS, Go, Rust, shell, PowerShell). A symbol A only added is not a use.
- **module dependencies:** a module B changed that depends, in B's Forest, on a module where A changed or removed a contract, when no use above explains it.
- **tests:** test files that reach both A's and B's changed files through in-repo imports (the `rco affected` graph at BASE, A, and B). Run them on the merged result.
- **stacked:** B already has all of A's commits.

The merge order puts B after A when B uses, depends on, or is stacked on A, layered like the Forest. A cycle is listed, and its branches share a layer. Changed files that are not code are listed per branch, because who reads them is unknown. Every report ends with what the plan cannot prove: a function body changed under an unchanged contract (only the shared tests see it), references the index does not resolve (calls across languages, dynamic dispatch, run-time imports, positional constructor arguments), and the fact that a clean pair is no proof the merged result works. Exit 1 when any pair has a finding. The JSON schema is `rco.plan/1`.

### `rco live [SHA] [--schema FILE] [--openapi FILE] [--help-text CMD=FILE]...`

Checks the map against real outputs that you captured. rco never runs repo code, so you capture what the running system says and pass in the files:

- `--schema` takes a SQL schema dump (`pg_dump --schema-only`, `sqlite3 db .schema`). Tables, views, and column names are compared; column types are not, because dumps spell them differently.
- `--openapi` takes an OpenAPI or Swagger JSON document, compared against the routes. Path parameters match in any spelling (`{id}`, `<int:id>`, `:id`), and an `ANY` route matches every method.
- `--help-text "tool run=run-help.txt"` takes a captured `tool run --help`, compared against that command's long flags.

`missing` means the live output has something the map lacks. `extra` means the map claims something the live output lacks. Exit 1 on any finding.

### `rco update`

The incremental post-merge updater described above.

### `rco init [--hooks] [--remove-hooks] [--no-config] [--no-ci]`

One-step setup. It writes only files that don't exist yet, never overwrites one, and reports each file as wrote or kept:

- `rco.toml` with every setting commented out, so nothing changes until you edit it.
- `.github/workflows/rco.yml`. On a pull request, it puts `rco diff`, `rco affected`, and (when `pins/` exists) `rco drift` into the job summary. On a push to the default branch, it runs `rco update` with the store cached. Actions are pinned to full SHAs. It fails only on exit 2 or 3.
- With `--hooks`, a `post-merge` hook in git's hooks directory (`core.hooksPath` is honored). After a pull or merge it runs `rco update --name <branch>` in the background, and it always exits 0, even without `rco` on `PATH`. If another post-merge hook is already there, it is kept, and you add the line by hand. `--remove-hooks` deletes the hook only if rco wrote it.

### Structural brief (example)

The brief is generated from the contract diff and the caller index. It is not written by a model.

```markdown
### STRUCTURAL UPDATE REQUIRED
Module `auth_service` in the target branch updated its interface contracts.

#### Contract changes (upstream):
- REMOVED: `validateToken(token: string): boolean`
+ ADDED: `validateToken(token: string, options?: AuthOptions): Promise<AuthResult>`

#### Impacted files in your branch:
- `src/controllers/user_controller.ts` (calls `validateToken`)

#### Task:
Update callers in `src/controllers/user_controller.ts` to match the new `AuthResult` signature.
Do not re-read or modify unaffected modules.
```

## Output contract

Output goes to stdout by default, or to `--out FILE`. `--format markdown` (default) or `--format json`.

Exit codes, so agents can script the commands:

| Code | Meaning |
|---|---|
| 0 | Ok (clean pack, no contract findings, triage all clean). |
| 1 | Findings (contract changed, Forest stale, or triage emitted a structural brief). `rco notice` never exits 1. |
| 2 | Usage error. |
| 3 | Internal error. |

## Secrets

Before any pack output or storage, rco runs a redaction and hygiene pass over extracted constants, docstrings, and other literals. Redacted values are replaced with a placeholder and are never written through. What is redacted:

- Private keys and PEM blocks
- Bearer tokens, API keys, passwords, and other high-entropy secret-shaped strings
- Connection strings and URLs that embed credentials
- Credential-like assignments (for example `password = "..."`, `api_key = "..."`)

Signatures, type names, route paths, flag names, and table or column names are kept. Public identifiers are not treated as secrets.

## Storage backends

Storage sits behind one small interface, chosen in `rco.toml`:

```toml
backend = "sqlite" | "mill" | "mcp" | "<entry point>"
```

The five calls are: put a node, get a node, list nodes by SHA, set a pointer (like a label), and post a notice (a stale Forest or an affected PR). Notices are optional; a backend that can only store still works, and notices go to stdout.

```mermaid
flowchart LR
    rco[rco] --> I["storage interface<br/>put node, get node, list by SHA,<br/>set pointer, post notice"]
    I --> Q[(sqlite: default<br/>one database under .rco/)]
    I --> ML[mill: optional extra<br/>hashes, pointers, scores]
    I --> MC[mcp: five calls as tools<br/>on a named server]
    I --> AD[adapter: Python entry point<br/>pip install rco-whatever]
```

An adapter is a small Python class that implements the five calls and registers through an entry point, so installing it makes it show up with no change to rco. The `mcp` backend maps the same five calls to tools on a server the user names, so any shared-memory tool that exposes MCP works with no Python adapter.

A conformance test suite is the check that an adapter is honest. Every backend runs it against itself.

The SQLite backend (`sqlite`) is the default: one database under `.rco/`. Tree and Forest JSON live in this content store (or another backend's equivalent). They are not copied into mill cards.

The mill and MCP backends are design only and not shipped. There is no `[mill]` install extra. The mill design:

- **Content:** Tree and Forest JSON stay in the SQLite content store. The mill holds only hashes and pointers. There is no mill tree node per module per SHA. Mill trees outline mail and todo. Card text has a 4096-character budget.
- **label** holds pointers such as `rco/main` or `rco/0.8.11`. A new label needs `mill create label` (Admin) on limited trust.
- **Staleness** is a score, `stale Forests outstanding`, and optionally one rollup card per repo. Not a card per module. Cards start only when a human asks.
- **mail** may carry a contract-diff notice. Mapping a PR to a desk requires a per-desk GitHub identity, which is not available while desks share one account. Letters are batched, one per affected PR, and addressed; every wake costs tokens.
- **tw** is the mill's recorded command runner. From a desk's sitting `mill connect DESK`, `tw rco update` records the run: start, finished, and failed.
- **score** also tracks LLM tokens rco itself spends (when prose is on) and stale Forests outstanding. Tokens and reads before the first useful action are not produced by rco; see Metrics.

Shipped today: `sqlite`, plus adapters. `mill` and `mcp` are not built yet; naming either one is a usage error (exit 2) that says so. The settings come from `rco.toml` at the top of the working tree, because they describe this checkout, not a commit. The `[[module]]` rules in the same file are read from git at each SHA, so a setting can never change a commit's Tree or Forest.

```toml
backend = "sqlite"          # or the name of an installed rco.backends entry point
[store]
path = ".rco/rco.db"        # sqlite: relative to the working tree top
[affected]
ignore = ["docs/**"]        # added to every rco affected --ignore
```

If the file is missing, the defaults apply. If it is not valid TOML, rco notes that on stderr and ignores it. A wrong type, or a backend that is not installed, is a usage error. An adapter registers a callable `factory(repo, options) -> Store` in the entry point group `rco.backends`; `options` is the `[store]` table. It checks itself with `rco.store.conformance.run_all`. `rco pack` and `rco check` only read a store and never create one.

### A store cached across CI runs

A CI job starts with an empty `.rco/`, so every `rco update` would extract every module. Cache the directory instead. Trees are keyed by ModuleHash, so a restored store only extracts the modules that changed, and a stale cache can only cost time, never correctness. Use one pointer per branch:

```yaml
- uses: actions/cache@v4   # pin to a full SHA
  with:
    path: .rco
    key: rco-${{ github.ref_name }}-${{ github.sha }}
    restore-keys: |
      rco-${{ github.ref_name }}-
      rco-${{ github.event.repository.default_branch }}-
- run: rco update --name "${{ github.ref_name }}"
```

The database is a single file once `rco update` exits, because SQLite checkpoints its WAL on close.

## Metrics

Claim no savings until they are measured against a baseline.

- Tokens and reads before the first useful action, and wrong guesses on cold runs, come from cold-agent logs, not from rco itself. Phase 0 uses the mill's cold-test kit and an existing baseline.
- Branches triage reported clean (hypothesis, measured in Phase 5; see [`docs/baseline/phase5-triage.md`](https://github.com/si4ai/repo-context-optimizer/blob/main/docs/baseline/phase5-triage.md)). Tests still run, because an unchanged contract can hide a behavior change.
- LLM tokens spent per module and per merge, when prose is on (from rco's own logs).
- Stale Forests outstanding (score).

## Roadmap

Development order: a reliable map first, then pins and drift checks, then rebase and merge tools built on the map.

```mermaid
flowchart LR
    P0[Phase 0<br/>baseline] --> P1[Phase 1<br/>Trees, diff,<br/>map checker]
    P1 --> P2[Phase 2<br/>Forest,<br/>optional prose]
    P2 --> P3[Phase 3<br/>rco pack<br/>A/B on cold agents]
    P3 --> P4[Phase 4<br/>pinned contracts]
    P4 --> P5[Phase 5<br/>triage hints]
    P5 --> P6[mill backend,<br/>MCP adapter]
```

### First slices

Each slice lands before the next starts, and each has a done-when that a check can confirm.

```mermaid
flowchart TD
    S1["1. Empty repo<br/>README, role-only hygiene and role-trailer checks, CI, test selection (ci/test-select.toml), verify/spin/"] --> S2
    S2["2. Map checker<br/>planted-drift self-test, recorded time limit"] --> S3
    S3["3. Pointer writer<br/>atomic CAS, complete SHAs, computed staleness"] --> S4
    S4["4. Forest prose<br/>queued after the pointer write"] --> S5
    S5["5. rco update and rco pack<br/>end to end on this repo and the mill"]
```

| Slice | Done when |
|---|---|
| 1. Empty repo | CI is green on `main` and selects jobs from [`ci/test-select.toml`](https://github.com/si4ai/repo-context-optimizer/blob/main/ci/test-select.toml); the hygiene check fails a planted personal name in a test PR; the role-trailer check fails a commit with no known role, so the self-merge check works from the first PR; a desk can take a card from worktree to PR. |
| 2. Map checker | It lists every mismatch (missing, extra, changed) with its path and exits nonzero on any; output is byte-identical for the same inputs; the planted-drift self-test fails if any drift is missed; a time limit is recorded with the RCO SHA. |
| 3. Pointer writer | The SPIN model is committed beside the code and passes all seven properties; a crash between store write and pointer write never publishes a partial SHA. |
| 4. Forest prose | A test shows no prose is ever written for a SHA the pointer has not published. |
| 5. Update and pack | `tw rco update` runs after each merge on this repo and the mill and publishes the merge SHA; `rco pack --for FILES` returns a pack within `--budget`; this is where dogfooding starts. |

Until slice 5 lands, the pipeline ends at merge; from slice 2 on, the map checker reports drift read-only on this repo and the mill.

- **Phase 0.** Baseline, using the mill's cold-test kit and an existing baseline: tokens and reads before the first useful action, plus wrong guesses, from cold-agent logs. Also measure extract cost and how many modules a typical merge touches.
- **Phase 1.** Trees, hashes, and `rco diff` for Python, the CLI, SQL, and routes. Zero LLM. Deterministic tests: same SHA in, same bytes out. Redaction pass and the output contract (stdout/`--out`, format flags, exit codes). The map checker (see [Verification](#verification)) ships with the first Tree builder, so the first map anyone reads comes with a measured accuracy.
- **Phase 2.** The Forest, computed first, with optional prose, plus staleness and name-in-Tree checks. When prose is on, it is generated lazily from the Tree (not raw source), the first time an agent asks for it in a pack; mined first from existing docstrings, module headers, and READMEs, with the model called only when nothing useful exists; written to a fixed template with a hard length cap, using a cheap model; rewritten only when that module's contract hash changes, never on internal edits, and debounced across merges; and checked by code: any name the prose cites must exist in the Tree, or the entry is flagged.
- **Phase 3.** `rco pack`, A/B tested on cold agents.
  The harness (`tools/phase3_ab.py`, method in [`docs/baseline/phase3-ab.md`](https://github.com/si4ai/repo-context-optimizer/blob/main/docs/baseline/phase3-ab.md)) prepares control and treatment briefs and scores mill cold-test kit logs against the phase 0 metrics (reads and tokens before the first useful action, wrong guesses). It never launches agents. The counting run, and any savings claim, waits until the forest is in a released install; a claim requires platform token counts (see [`docs/baseline/phase0.md`](https://github.com/si4ai/repo-context-optimizer/blob/main/docs/baseline/phase0.md)).
- **Phase 4.** Pinned contracts: a developer reviews a Tree and commits it as the intended contract; every PR re-extracts and reports drift from the pin as a visible check, not a block. The map then works both ways: it describes the code, and the code is checked against it.
- **Phase 5.** `rco triage` as a hint (never rebases or pushes), plus a cross-PR contract notice in CI (a visible hint, not a block), subject to the identity constraint above. Rebase and merge tools come only after the map is reliable.
- Then the mill backend and the MCP adapter.

Language: Python first, so `pip install repo-context-optimizer` just works. Port only a hot path if measurement shows one. The LLM Forest comes after the deterministic diff and triage, because those are meant to save tokens with zero LLM spend.

Dynamic dispatch (command tables, `getattr`, and similar registries) will need custom extractors, or the CLI Tree will be incomplete. The Forest prose will drift unless the staleness signal is actually read.

## Verification

Three layers, the same ones the mill uses for its own protocol.

```mermaid
flowchart LR
    V1["1. Is the design right?<br/>SPIN model, verify/spin/"] --> V2["2. Does the code follow it?<br/>map checker, property tests,<br/>history replay"]
    V2 --> V3["3. Does it run?<br/>smoke tests, exit codes,<br/>timing, cold-agent A/B"]
```

1. **Is the design right?** A SPIN (or TLA+) model of `rco update` with racing merges, a late update, a crash midway, and a rebased history. Properties: the pointer only moves forward (a new SHA must descend from the current one, checked and written in one atomic compare-and-swap); a published pointer always has its Trees; a stale Forest is never shown as current; an update run twice equals one run; no changed module is skipped; every Forest eventually becomes fresh. Status: modelled in `verify/spin/rco_update.pml` (linear history, two modules, two updaters, one crash, one duplicate). The design as first written failed all seven properties; with the four rules above it passes all seven. Rebased history and a lossy store are not modelled yet.
2. **Does the code follow the design, and is the map true?** The map checker takes a repo, a SHA, and a map, and reports each claim as true, false, or missing, with no LLM:
   - Contracts: every function, flag, table, column, and route in a Tree exists at that SHA with that shape, checked by a fresh parse and by real outputs (`--help`, the live schema, the route table). It also reports what the Tree missed.
   - Edges: every depends-on and used-by link matches the import graph at that SHA.
   - Freshness: the map's SHA and module hashes match the code.
   - Prose names: every name the Forest prose cites exists in the Tree.
   - The checker checks itself: maps with planted errors (a removed flag, a renamed column, a missing edge) must all be flagged.
   Alongside it: property tests (Hypothesis) that mirror the model's properties over random edit and merge sequences; a history replay where incremental updates must equal a full rebuild at every SHA of the default branch; and golden fixture repos with known Trees and Forests.
3. **Does it run?** The zero-config acceptance table as smoke tests, the exit-code contract, timing and budget on the replay, and the cold-agent A/B, which is the only evidence that the map helps.

When `--budget` trims a pack, the pack names what it trimmed.

## Open questions

- **PyPI package name.** The package is on PyPI as `repo-context-optimizer` (installs the `rco` command).
- **Which prose pieces truly need an LLM.** Find out empirically: ship Phase 1 with no LLM, then see what cold agents still ask for or get wrong.
- **Default `--budget`.** 8000 estimated tokens is the design default; confirm against Phase 0 pack sizes.

## Merge planning beyond `rco plan`

**Status:** `rco plan` ships the simple, deterministic part: changed symbols, signatures, calls, module dependencies, shared tests, and a merge order for branches that already exist. Everything below is still a proposal, and its scope is still to be discussed.

**Where it lives:** in the mill, as a host report plus a desk CLI command. The optimizer later feeds it a file- and function-level map in place of the plain diffs.

**What it does:**

- For open PRs, or a card's planned file list, it reports which ones collide on the same files or lines.
- It groups the rest into lanes that can land in any order.
- It suggests a merge order and names which PR needs a rebase after each merge.
- It previews collisions before code is written.

**Design:** deterministic, with no LLM. Data gathering is kept separate from the conflict graph so the optimizer's map can plug in later. It is a visible check or hint, never a hard block.

**Quick win:** generate the CHANGELOG at release time, so PRs stop conflicting on it.

**Possible split:** Builder takes the server report and the graph; grok-tui takes the CLI. The two agree on the JSON shape first.

**Open questions for the scope discussion:** line-level or function-level granularity; whether it is advisory only or wired into Validator; the CHANGELOG format.

## Prior art

AST skeletonizers, ctags, tree-sitter, Aider's repo map, and various repo packers already extract structure or pack context. RCO's difference is content-addressed incremental Trees and Forest, a contract diff, rebase triage that reports a hint rather than touching branches, and pluggable storage.

## Process and GitHub setup

This repository dogfoods a portable process: checks over rules, report-only CI reports that show facts, and an independent Validator that passes and merges. A hidden-code report (`tools/check_hidden_code.py`) runs on every PR and push. It exits 0 on findings; a crash fails the job. The recipe for pointing the same process at a user repository is in [`docs/github-setup.md`](https://github.com/si4ai/repo-context-optimizer/blob/main/docs/github-setup.md).

## License

MIT. See [LICENSE](https://github.com/si4ai/repo-context-optimizer/blob/main/LICENSE).
