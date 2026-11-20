# Configure GitHub for a user repository

A short recipe for pointing a repository at the same development process this project uses: checks over rules, report-only checks that show facts rather than hard-blocking a merge, test selection, and an independent Validator that passes and merges pull requests; authors do not merge their own PRs. For rco itself (install, `rco init`, the workflow it writes), see [adopting rco](guide/adopting-rco.md); this file covers the repository around it.

This is a first draft. Repo settings are applied by a repo admin; this file is the checklist.

## Check first

Plan and owner decide what you can turn on:

- Rulesets: any public repository; a private one only on a paid plan.
- Merge queue: a public repository owned by an organization, or a private one in an organization on GitHub Enterprise in the cloud. Never on a repository owned by a personal account.

Without them, Validator reviews and merges by hand, and nothing requires its status.

Then read what the repo already has, because the steps below add to it rather than assume a blank repo: `gh api repos/OWNER/REPO/rulesets`, `gh api repos/OWNER/REPO/branches/BRANCH/protection` (404 means none), the merge methods in the repo settings, and the workflows in `.github/workflows/`. Change an existing ruleset rather than adding a second one that overlaps it.

## Repository settings

On the default branch, prefer:

- Allow squash merge and merge commits. Turn rebase merge off, so history stays reviewable and the queue can squash.
- Enable auto-merge, so Validator can enqueue a passed PR.
- Delete the head branch on merge.
- Turn update-branch off unless a desk has a reason to need it.
- Require no human code-owner review by default; Validator is the passer.

## Ruleset on the default branch

One active ruleset targeting the default branch. Do not allow bypass for desk apps or admins if you can avoid it. It should have:

- No deletions and no force-pushes.
- Pull request required, with zero approving reviews (Validator's status is the pass, not a GitHub review count). Dismiss stale reviews on push. Restrict merge methods to squash and merge.
- Required status checks, including:
  - product jobs from CI (`pytest`, and any other required test job)
  - `validator` (posted by the Validator GitHub App) and `validator_pass` (the CI job that checks that status in the merge queue)
  - CodeQL analyze jobs, once added
  - optionally `hidden_code_report`, required only so a crash of the scanner blocks; the scanner itself exits 0 on findings

Report-only jobs belong in this list only when a failure means "the check did not run", not "the check found something". Never wrap a report job in `|| echo`; that hides crashes and makes a dead checker look green.

Add a merge queue only when the repo needs one, that is, when several PRs land on the default branch close together and each must pass against the others. Then: squash, group when all required checks are green, a generous check timeout, and a `merge_group` trigger on every workflow that has a required job. Without a queue, require the branch to be up to date instead.

Keep a second ruleset or tag protection for release tags (`v*`) if you publish them: no updates, no deletions, no force-pushes.

## rco's own jobs

These jobs are report-only: keep them off the required checks and out of the merge-queue gate.

- `rco init` writes `.github/workflows/rco.yml` with one job, `rco`. On a pull request it writes `rco diff`, `rco affected`, `rco drift` (when `pins/` exists) and the cross-PR notice (`python -m rco.notice_report`) to the job summary. On a push to the default branch it runs `rco update`. It has no `merge_group` trigger, so a queue does not wait for it.
- The notice ships in the wheel; there is nothing to copy. Its private-repository limit (the checkout drops its token) is in [adopting rco](guide/adopting-rco.md#setup-t0-rco-init).
- Do not copy this repository's `drift_report` and `notice_report` jobs; the `rco` job covers both.

## Validator GitHub App

Install a dedicated Validator app on the repository. A separate app is the point: the desk that writes a PR must not be the desk that merges it. Suggested permissions:

- `contents`: read and write (checkout, and merge or enqueue)
- `pull_requests`: read and write
- `checks` and `statuses`: read and write (post the `validator` status; read others)
- `actions`: read (see check runs)
- `workflows`: read and write, only if this app will open or merge PRs that edit `.github/workflows/**`. GitHub rejects pushes that touch workflow files unless the token has this permission.
- Administration or merge-queue related permissions as required to enqueue.

Lessons already learned:

- After an app's permissions change, the org must accept the new permissions. Until it does, a freshly minted token still lacks them. Confirm the token's permission set before retrying a push.
- Workflow edits and merge-queue setup are easy to miss when an app is first installed. Grant them on purpose, not as a surprise mid-PR.
- The `validator_pass` job should trust only statuses created by this app's bot login. Anyone with write access can post a status with the same context, so a configurable login (not a hard-coded org name) belongs in the check.

Authors, including other desk apps, never merge, enqueue, auto-merge, or rerun CI on their own PRs.

## Files to copy

The wheel ships only the `rco` package. The process checks below live in this repository, so copy them at the tag of the rco version you install:

- `ci/test_select.py`, and `ci/test-select.toml` as a starting map
- `tools/check_hidden_code.py`
- `tools/check_hygiene.py` and `tools/hygiene_words.txt`
- `tools/check_role_trailer.py`, which imports `check_hygiene`, so keep both in `tools/`
- `ci/hygiene.toml`, with `roles` set to your own desks
- `.githooks/pre-commit`
- from `.github/workflows/ci.yml`: the `select`, `hidden_code_report`, `hygiene_report` and `role_trailer_report` jobs

## CI workflow template

Shape your `ci.yml` like this repository's:

- A `select` job that runs `ci/test_select.py` against `ci/test-select.toml`.
- Product jobs (`pytest`, and others) that run only when the map selects them. A file that matches no rule runs no product job.
- `hidden_code_report`, **outside** the map, on every pull request, push to the default branch, and (once a queue exists) `merge_group`. The job is report-only: no `--strict`. A crash must fail the job.
- Pin actions to a 40-hex SHA, with the version in a comment. Prefer `persist-credentials: false`. Restrict allowed actions in the repo settings and turn on SHA pinning when the plan allows it.
- Add `codeql.yml` and `.github/dependabot.yml` when you are ready. Dependabot should be the one author allowed to change a lock file without a matching manifest change (the hidden-code scanner already exempts it).

Optional later: a `validator.yml` workflow that runs `check_validator_pass` (ported, with a configurable app login) on `pull_request` and `merge_group`.

## Test selection

`ci/test-select.toml` is the map. Each `[[rule]]` lists path globs and job names. Unknown job names are an error. Keep report-only scans that exist to catch unmapped files **out** of this map, or they will not run on the files they are meant to catch.

An optional `ci/hidden-code.toml` can override the hidden-code scanner:

```toml
map = "ci/test-select.toml"
control_plane = [".github/**"]

[[lock]]
glob = "**/package-lock.json"
manifests = ["package.json"]
```

Flags `--map` and `--control-plane` override the same fields. Defaults match the snippet above, plus common lock files (`poetry.lock`, `uv.lock`, `Pipfile.lock`, `yarn.lock`, `pnpm-lock.yaml`, `requirements*.txt`).

## Hygiene and PR text

`hygiene_report` runs `tools/check_hygiene.py` on every pull request, push to the default branch, and `merge_group`, the same way as `hidden_code_report`. It is report-only (no `--strict` in CI). It flags non-documentation addresses, home paths with a login segment, emails outside placeholder domains, credential shapes, title-case name tokens that are not ordinary words, typographic marks, byte-order marks, and junk files, in the commit range and in the PR title and body. The report names the kind and the path or commit, never the matched text; a crash fails the job. Flags or `ci/hygiene.toml` tune the words file and options.

Once per checkout, turn on the repo hooks with `git config core.hooksPath .githooks`. `pre-commit` runs `tools/check_hygiene.py --staged --strict` on staged blobs (byte-order marks and typographic marks on the whole blob; the other rules on added lines).

`role_trailer_report` runs `tools/check_role_trailer.py` on the same events. Each commit in the range must carry a `Role: <desk>` trailer whose value is in the configured role list. A missing or unknown role is a finding; bot-authored commits may omit the trailer. The job is report-only in CI, and a crash fails it. Whether either job becomes a required check, or whether the trailer feeds a self-merge gate, is a later settings decision.

## What not to copy from a mill-style office

Skip anything that is about one product's database, CLI, or failure-class catalog. The portable parts are the process: the map, the hidden-code report, report-only checks, Validator-as-passer, the merge queue, pinned Actions, CodeQL, and Dependabot.
