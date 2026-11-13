# Phase 5: share of open branches triage reports clean

Card 1189. The README metrics line "branches triage reported clean (hypothesis, measured in Phase 5)" is recorded here for the first time. An unchanged contract can still hide a behavior change, so tests still run.

## How

For each open PR, run `rco triage` on the PR head against the current tip of that PR's target branch (`--onto` the tip of `base.ref`, not the recorded `base.sha`, which can lag). A branch is clean when it changed no file the target also changed and none of its changed Python files references a name the target's contract diff changed. CLI, SQL and route changes show up only as files changed on both sides.

The share is (clean open branches) / (open branches). Re-run by listing open PR heads, fetching each, and triaging onto the current target tip.

## SHA and time

- When: 2026-11-12, 16:52 PT (UTC-8).
- Tool: `rco triage` at `2bb108782d2e` (`release/0.1.1`).
- Open PRs at that time: 2 (`#9`, `#34`). Phase 2 step drafts `#18`-`#24` had already been closed as folded into `#25`.

## Results

| pr | head | target tip | clean? | overlap | impacted files | upstream contract changes |
|---|---|---|---|---:|---:|---:|
| #9 | `915c09d0312f` | `main` at `56db5f351291` | no | 0 | 3 | 1038 |
| #34 | `01ff0cbe70cd` | `release/0.1.1` at `2bb108782d2e` | yes | 0 | 0 | 0 |

Share reported clean: 1 / 2 (50%).

`#34` is a docs-only change on `docs/baseline/phase3-ab.md`, so clean is the expected result. `#9` is far behind `main`: no overlapping file, but three files on the branch call names that `main` has since changed, so it gets a structural brief. That is the signal the hypothesis cares about. A sample of two open branches is too small to confirm or reject the hypothesis that many branches are contract-clean; this row is the starting baseline.
