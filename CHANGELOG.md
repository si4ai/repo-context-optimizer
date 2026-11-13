# Changelog

## 0.1.2 - 2026-11-13

0.1.1 was merged to main but never tagged or published to PyPI. 0.1.2 is the first
install after 0.1.0, so it carries everything in the 0.1.1 entry below
as well as the changes here. Everything stays deterministic: no LLM runs,
and the same commit gives the same Trees, Forest, and pack bytes.

Trees change for repos with JavaScript/TypeScript, Go, Rust, shell, or
PowerShell files, and every ModuleHash changes because the extractor
list is part of it. A store from 0.1.x re-extracts once on the first
`rco update`. Module ids change only in repos with an `rco.toml`
`[[module]]` rule, a `package.json`, or a `Cargo.toml` with `[package]`.

### added

- bundled parsers for JavaScript/TypeScript, Go, Rust, shell, and
  PowerShell (new Kind `srcmodule`), with no new dependency. Public
  symbols, signatures, the first doc comment line, and exports (#40).
- derived prose: a module line from the names its Tree defines, and in
  a pack, a line for each undocumented class or function from its name,
  signature, in-repo calls, and callers (resolved through imports).
  Marked `derived`, and it passes the name-in-Tree check (#41).
- modules from an `rco.toml` override file (`[[module]]` id plus path
  globs, first match wins), then `package.json` and `Cargo.toml`
  manifests, then directories. Read from git at each SHA (#42).
- import edges for JavaScript/TypeScript, Go, Rust, shell, and
  PowerShell in the Forest and in `rco check` (#42).
- `rco affected`: tests that reach a changed file through in-repo
  imports, at both BASE and HEAD. A changed non-code file sets
  `run_all` with the reason. `--ignore`, `--list` (#43).
- `rco diff --staged` and `--worktree` (also on `rco affected`): the
  index or working tree becomes a commit object with fixed metadata.
  The real index is never touched (#43).
- `rco.toml` settings: `backend` (`sqlite` or an `rco.backends` entry
  point adapter), `[store] path`, `[affected] ignore`. The store
  conformance suite ships as `rco.store.conformance` (#44).
- a recipe for a store cached across CI runs (#44).
- `rco init`: writes `rco.toml` (all commented out) and a CI report
  workflow, and with `--hooks` a post-merge hook that runs `rco update`.
  It never overwrites a file (#45).
- `rco live`: checks the map against a captured SQL schema dump,
  OpenAPI or Swagger JSON, and `--help` texts. rco never runs repo code
  (#46).
- `rco plan BRANCH BRANCH... [--base BASE]`: simple merge planning.
  Against the branches' common merge-base, for each pair: files both
  changed, uses of symbols one branch changed or removed in the other's
  changed files, module dependencies in the Forest, tests that see both
  sides, stacking, and a merge order. Every report lists what it cannot
  prove. Read-only (#51).

### changed

- `rco diff` callers are resolved to the changed symbol's file and
  qualname (imports, module aliases, `self`, annotated and returned
  types, Go `pkg.Name`), not matched by bare name, in Python and every
  bundled language. On a large repo the caller count for 20 commits fell
  from 10,106 to 613 (#49).
- `rco triage` and `rco notice` use the same index, so changed JS/TS,
  Go, Rust, shell, and PowerShell files count as impacted (#50).

### fixed

- the `notice_report` CI job diffs from the merge-base of the PR base
  tip and head, not the base tip, and compares only open PRs into the
  same base branch (#39).
- `rco pack` and `rco check` now find the store from a subdirectory of
  the working tree (#44).
- the CI drift report job no longer fails on drift; drift is a
  warning, and only a usage error or crash fails it (#40).

### not shipped

LLM prose (off by design), the mill backend extra, mail notices
through a backend, `tw rco update` runs, the MCP backend, and merge
planning beyond `rco plan`. Naming `mill` or `mcp` as the backend is a usage error.

## 0.1.1 - 2026-11-12 (not published)

Adds the phase 3 cold-agent A/B harness and phase 5 notice CI on top of
0.1.0. Pack output is unchanged from 0.1.0; only `rco --version` and the
harness's `rco_version` field change.

### added

- a cold-agent A/B harness (`tools/phase3_ab.py`) that writes control and
  treatment briefs and scores cold-test kit logs. It never launches
  agents and it refuses a token-savings claim unless every arm has a
  platform tokenizer count. The A/B that counts still waits until this
  forest release is the one under test (card 1190, #32).
- a report-only `notice_report` CI job: other open PR heads are listed as
  `pr-N` (never a desk) and `rco notice` is written to the job summary.
  A usage error or crash fails the job; a hint never does. A triage
  design note and a clean-branch baseline are recorded (card 1189, #35).

### release gate

A pass from the validator desk, then the human's GO, then the `v0.1.1`
tag, then the human approves the `pypi` environment run.

## 0.1.0 - 2026-11-12

First installable release of phase 2. Rough on purpose, so it can be
dogfooded. Python import edges only, package-grain modules, no override
file, no mill backend, no triage, and no A/B claim.

Install: `pip install repo-context-optimizer` (or pipx). That installs
the `rco` command.

### added

- trees, the map checker, and `rco diff` for Python, CLI, SQL, and routes
  (phase 1). Zero runtime dependencies and no network at runtime.
- `rco update`, `rco pack`, and `rco forest`, with a local SQLite store
  under `.rco/` (phase 2). The store is per worktree. `.rco/` is added to
  `.git/info/exclude`, never to `.gitignore`.
- mined prose on by default: `rco update` drains the queue from
  docstrings, module headers, and READMEs. The LLM path ships off.
- a name-in-tree check: names cited in prose that are not in the Tree are
  shown in the pack and counted by `rco check` when a store is present.
- `rco check` accepts an `rco.forest/1` file and reports `edge` findings
  against the same Forest graph `rco forest` shows.
- `rco pack` cuts empty Forest lines (layer 0, no edges, nothing owned, no
  prose) before Tree nodes, from the deepest level up, and keeps a parent
  line until all of its children are cut.
- history replay: incremental `rco update` equals a full rebuild at every
  SHA of a fixture history and of this repository.
- zero-config acceptance smokes for a small Python repo, a polyglot repo,
  a large layout, submodules, LFS pointer files, and an empty repo.
- release workflow (`.github/workflows/release.yml`): a `v*` tag builds
  the sdist and wheel, runs tests, `twine check`, dist checks, and a
  wheel smoke, then publishes to PyPI with trusted publishing (OIDC, no
  token) in the `pypi` environment. A workflow dispatch publishes the
  same artifacts to test.pypi.org in the `testpypi` environment, as the
  rehearsal before a tag.

### release gate

A pass from the validator desk, then the human's GO on the SHA, then the
`v0.1.0` tag, then the human approves the `pypi` environment run.
