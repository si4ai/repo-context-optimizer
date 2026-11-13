# Changelog

## 0.1.1 - 2026-11-12

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
