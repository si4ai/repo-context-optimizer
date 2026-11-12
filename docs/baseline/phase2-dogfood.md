# phase 2 dogfood (0.1.0)

Card 1187 (`repo-context-optimizer/phase2-step7-release`), under card 1179.
This is the first installable release. It is rough on purpose: Python import
edges only, package-grain modules, no override file, no mill backend, no
triage, and no A/B claim.

## applied plan defaults

The human said to use the plan defaults on the open phase 2 questions
(`docs/design/forest-contract.md`):

1. mined prose is on in 0.1.0. Only the LLM path is off.
2. `tw rco update` after each merge is proposed as a job for the validator
   desk. That hook is not in this release; start with a manual or CI run
   on this repo and on the mill while dogfooding.
3. The store is per worktree (`.rco/` in the checkout). There is no shared
   store via `RCO_STORE` yet.
4. property tests use the seeded stdlib interleaving stand-in, not
   hypothesis as a CI dependency.
5. rehearse on test.pypi.org first (`workflow_dispatch` of `release.yml`),
   then tag and publish to PyPI.
6. release gate: a pass from the validator desk, plus the human's GO on
   the SHA. Not a build-room consensus.
7. The SQLite schema in the contract is used as written. It can change in
   a later release if it needs to (alpha: no backward compatibility).

## Install and smoke

From a directory that is not a checkout of this repo:

```
python3 -m venv /tmp/rco-0.1.0
/tmp/rco-0.1.0/bin/pip install repo-context-optimizer==0.1.0
/tmp/rco-0.1.0/bin/rco --version    # rco 0.1.0
```

Or `pipx install repo-context-optimizer==0.1.0`.

Then, in a clone of this repo and in a clone of the mill, with no
`PYTHONPATH` and not using the checkout as the install:

```
rco update
rco pack
rco pack --for <a file you will touch>
rco forest
rco check --selftest
```

`git status` should stay clean. `/.rco/` should appear only in
`.git/info/exclude`. A pack that shows stale prose exits 1; after
`rco update` and a prose queue drain, a later pack of the same SHA
exits 0.

The wheel smoke used in CI is `ci/wheel_smoke.sh` on a scratch fixture.
A local pack build should run that too, from `/tmp`, after
`pip install` of the wheel or sdist.

## history replay

`tests/test_replay.py` checks that walking `rco update` SHA by SHA
leaves the same store index and trees as a full rebuild at each SHA.

- always: a fixture history (`tests/update_history.py`) and every
  first-parent SHA of this repository reachable from HEAD.
- optional, not in default CI: the last 100 first-parent merges of the
  mill. Set `RCO_REPLAY_MILL` to a checkout of the mill and run
  `pytest tests/test_replay.py -k mill`. Record the result here when
  you run it before the tag.

Replay does not yet compare stored import edges. Edges are still
computed from git at pack time. The same module bytes can resolve
different in-repo imports when the rest of the repo changes, so a
ModuleHash key for edges is still open.

`rco.toml` is still not read (no override file in 0.1.0).

## acceptance smokes

`tests/test_acceptance.py` pins the zero-config table in the README:

| repo | expected |
|---|---|
| small Python repo | modules from the package layout; `rco pack` and `rco diff` run with the defaults. |
| polyglot repo | unknown languages fall back to a file outline, not an error. |
| large layout | pack respects `--budget`; `rco update` re-extracts only modules whose ModuleHash changed. |
| repo with submodules | submodules are skipped, with a note. |
| LFS repo | LFS pointer files are opaque: not fetched, not parsed as the real blob. |
| empty repo | empty pack, exit 0. |

## release checklist (human steps only)

The code and the workflow are in this PR. Do not tag or upload until
the validator desk has passed the release SHA and the human has given
GO. The workflow never stores a PyPI token.

### 1. test.pypi.org trusted publisher (rehearsal)

Pending trusted publisher for project `repo-context-optimizer`:

- owner: `si4ai`
- repository: `repo-context-optimizer`
- workflow file name: `release.yml`
- environment name: `testpypi`

### 2. pypi.org trusted publisher (real release)

Pending trusted publisher for project `repo-context-optimizer`:

- owner: `si4ai`
- repository: `repo-context-optimizer`
- workflow file name: `release.yml`
- environment name: `pypi`

### 3. GitHub repository environments

Create two environments on `si4ai/repo-context-optimizer`:

- `testpypi`: required reviewer is the human; deployment branches include
  `main` (workflow dispatch).
- `pypi`: required reviewer is the human; deployment branches include
  tags `v*` (and `main` if you also want a branch safeguard).

No environment secrets are needed. Trusted publishing uses OIDC.

### 4. rehearsal, then tag

1. After this PR (and #25) are on `main`, run `release.yml` with
   workflow dispatch from `main`. That publishes 0.1.0 to
   test.pypi.org once the `testpypi` environment is approved.
2. Install from test.pypi.org into a clean venv in `/tmp` and run
   `ci/wheel_smoke.sh` plus `rco --version`.
3. a pass from the validator desk at the release SHA, then the human's GO on that SHA.
4. Tag `v0.1.0` on that SHA and push the tag. `release.yml` builds
   again, then waits on the `pypi` environment.
5. The human approves the `pypi` environment run.
6. `pipx install repo-context-optimizer==0.1.0`, smoke this repo and
   the mill (via `tw` where that is how the mill is invoked), and send
   the live letter.

A version can only be published once to PyPI. A failed publish needs a
new version. test.pypi.org is the same for a given file name; do not
dispatch the rehearsal twice for 0.1.0.
