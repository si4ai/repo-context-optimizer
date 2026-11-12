# Configure GitHub for a user repository

A short recipe for pointing a repository at the same development process this project uses: checks over rules, report-only checks that show facts rather than hard-blocking a merge, test selection, and an independent Validator that passes and merges pull requests. Authors do not merge their own PRs.

This is a first draft. Repo settings themselves are applied by a repo admin or by Builder; this file is the checklist.

## Prerequisites

Rulesets, classic branch protection, and the merge queue are not available on a private repository under GitHub's free plan. They work on a public repository, or on a private repository once the org is on a plan that includes rulesets. Decide that first. Until then, Validator can still review and merge by hand, but nothing can require checks or a Validator status.

## Repository settings

On the default branch, prefer:

- Allow squash merge and merge commits. Turn rebase merge off, so history stays reviewable and the queue can squash.
- Enable auto-merge, so Validator can enqueue a passed PR.
- Delete the head branch on merge.
- Turn update-branch off unless a desk has a reason to need it.
- Require no human code-owner review by default; Validator is the passer.

## Ruleset on the default branch

Create an active ruleset targeting the default branch. Do not allow bypass for desk apps or admins if you can avoid it. Include:

- No deletions and no force-pushes.
- Pull request required, with zero approving reviews (Validator's status is the pass, not a GitHub review count). Dismiss stale reviews on push. Restrict merge methods to squash and merge.
- Required status checks, with the branch up to date, including:
  - product jobs from CI (`pytest`, and any other required test job)
  - `validator` (posted by the Validator GitHub App) and `validator_pass` (the CI job that checks that status in the merge queue)
  - CodeQL analyze jobs, once added
  - optionally `hidden_code_report`, required only so a crash of the scanner blocks; the scanner itself exits 0 on findings
- A merge queue: squash, group when all required checks are green, with a generous check timeout.

Report-only jobs belong in this list only when a failure means "the check did not run", not "the check found something". Never wrap a report job in `|| echo`; that hides crashes and makes a dead checker look green.

Keep a second ruleset or tag protection for release tags (`v*`) if you publish them: no updates, no deletions, no force-pushes.

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

## CI workflow template

Start from this repository's `.github/workflows/ci.yml`:

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

`hygiene_report` runs `tools/check_hygiene.py` on every pull request, push to the default branch, and `merge_group`, the same way as `hidden_code_report`. It is report-only (no `--strict` in CI). It flags non-documentation addresses, home paths with a login segment, emails outside placeholder domains, credential shapes, title-case name tokens that are not ordinary words, typographic marks, byte-order marks, and junk files, in the commit range and in the PR title and body. the report names the kind and the path or commit, never the matched text; a crash fails the job. flags or `ci/hygiene.toml` tune the words file and options.

Once per checkout, turn on the repo hooks with `git config core.hooksPath .githooks`. `pre-commit` runs `tools/check_hygiene.py --staged --strict` on staged blobs (byte-order marks and typographic marks on the whole blob; the other rules on added lines).

`role_trailer_report` runs `tools/check_role_trailer.py` on the same events. each commit in the range must carry a `Role: <desk>` trailer whose value is in the configured role list. a missing or unknown role is a finding; bot-authored commits may omit the trailer. the job is report-only in CI, and a crash fails it. whether either job becomes a required check, or whether the trailer feeds a self-merge gate, is a later settings decision.

## What not to copy from a mill-style office

Skip anything that is about one product's database, CLI, or failure-class catalog. The portable parts are the process: the map, the hidden-code report, report-only checks, Validator-as-passer, the merge queue, pinned Actions, CodeQL, and Dependabot.
