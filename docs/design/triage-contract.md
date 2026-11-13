# Phase 5 triage hints

Card 1189. README roadmap, Phase 5: `rco triage` reports, per branch, either clean or a structural brief, as a hint. It never rebases, merges or pushes. A cross-PR contract notice in CI names other open PRs whose changed files use a contract this head changes; a visible hint, never a block. Built on Phase 1 Trees and `rco diff` only; no Forest, no store, no pins.

No per-desk mapping (identity constraint): the notice addresses PRs (`pr-N`), not desks. Mapping a PR to a desk needs a per-desk GitHub identity, which is not available while desks share one account.

## Commands

| Command | Report | Exit 1 when |
|---|---|---|
| `rco triage BRANCH... [--onto TARGET]` | `rco.triage/1` | any branch gets a structural brief |
| `rco notice OTHER... [--base BASE] [--head HEAD]` | `rco.notice/1` | never (always exit 0) |
| `rco plan BRANCH BRANCH... [--base BASE]` | `rco.plan/1` | any pair of branches has a finding |

`TARGET` defaults to the default branch. A branch is clean when it changed no file the upstream side also changed, and none of its changed code files references a symbol the upstream contract diff changed. References are resolved by `rco.refs` to the changed symbol's file and qualname, never by bare name, in Python, JS/TS, Go, Rust, shell and PowerShell. Otherwise it gets a structural brief. CLI, SQL and route changes show up as files changed on both sides.

`rco plan` compares two or more branches against their common merge-base (or `--base`). For each ordered pair (A, B) it lists files both changed, B's references to symbols A changed or removed (same index), modules B changed that depend in B's Forest on a module where A changed or removed a contract, the test files that reach both sides' changes through in-repo imports, and whether B is stacked on A. The merge order layers B after A when B uses, depends on or is stacked on A; a cycle shares a layer and is listed. Each report lists what it cannot prove (body-only changes, unresolved references; a clean pair is not proof the merge works).

None of these commands rebases, merges, pushes or writes a ref. An unchanged contract can still hide a behavior change, so tests still run.

## CI

The `notice_report` job lists open PR heads, fetches each other head as the local ref `pr-N`, and runs `rco notice` of this head against those refs. The report goes to the job summary. The job is a visible hint: `rco notice` always exits 0, and a run with no other open PRs writes a short note and passes. Only a usage error or a crash (exit 2 or 3) fails the job. Skipped in the merge queue, which re-checks the same head. Kept out of `ci/test-select.toml` so it runs on every PR and push.

On a pull_request event, this head is the PR head and base, and the current PR is excluded from the other-heads list. On a push to main, this head is the pushed commit against its predecessor, and every open PR is an other head.

## The hypothesis

The README metrics line "branches triage reported clean" is the share of open branches that `rco triage` reports clean when each branch is compared to the current tip of its target branch. The first measurement is in `docs/baseline/phase5-triage.md`.
