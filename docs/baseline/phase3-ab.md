# phase 3 cold-agent A/B

The README roadmap's phase 3: `rco pack`, A/B tested on cold agents.
Verification layer 3 says the cold-agent A/B is the only evidence that the
map helps. This document is the method. Card 1190
(`repo-context-optimizer/phase3-ab-harness`).

`rco pack` itself landed with the phase 2 fold on `main`. This slice is the
harness only. The A/B that counts waits until phase 2's forest is in a
released install (card 1187, 0.1.0). The harness never launches agents.

## what it measures

The same cost the phase 0 baseline recorded in
[`phase0.md`](phase0.md) section A, for a code task rather than a
mill-protocol join:

- commands, wrong guesses, retries, and seconds on the `SUMMARY turn=task`
  line from the mill cold-test kit
- reads and stdout bytes before the first useful action
- tokens before the first useful action, when the log has them

A token-savings claim is refused unless every scored log has
`TOKENS source=platform` or `TOKENS source=tokenizer` and a
`before_first_useful` count. `proxy-bytes` and characters / 4 stay
proxies, as phase 0 already warned: they must not be divided by 4 and
called tokens.

## commands

The script is standard library plus the installed `rco` package (or
`PYTHONPATH=src` in a checkout).

```sh
python3 tools/phase3_ab.py prepare --repo REPO --sha HEAD \
    --for path/to/file.py --budget 8000 \
    --task-file TASK.md --out ab-run/

python3 tools/phase3_ab.py score \
    --control ab-run/control/*.log \
    --treatment ab-run/treatment/*.log \
    --first-useful 'apply_patch' \
    --out ab-run/score.json
```

`prepare` writes `ab-run/manifest.json`, `ab-run/control/brief.md`, and
`ab-run/treatment/{brief.md,pack.md,pack.json}`. The treatment brief
embeds a live `rco pack` (forest, trees for `--for`, one import hop,
budget). The control brief has the same task and no pack. `rco pack` is
read-only: it does not create `.rco/` in the target repo.

`score` parses kit logs with the phase 0 parser
(`tools/phase0_baseline.py`). `--first-useful REGEX` selects the first
successful command that is the useful action (a code edit, not
`mill join`). If it is omitted, the phase 0 `mill join` heuristic is
kept. The report schema is `rco.phase3_ab/1`. Exit 0 is a completed
measurement, including a worse treatment arm; exit 2 is a usage error.

### log extras

Kit rows and `SUMMARY turn=...` lines are unchanged. Optional:

```
TOKENS source=platform before_first_useful=1200
```

`source=tokenizer` is also claimable. Any other source, or a missing
count, sets `savings_claimable` to false and lists the reason in
`claim_blockers`.

## counting run (not this slice)

After 0.1.0 is released and installed from the index:

1. pick a fixed code task on a frozen SHA of a real repo (the mill is the
   planned first target). The task must need a code edit, not a protocol
   join. Phase 0 recorded that no such cold code-task log exists yet.
2. seat two fresh cold desks (or the same recipe twice): one given only
   the control brief, one given the treatment brief plus the pack from
   the released `rco` at that SHA.
3. score the kit logs with `tools/phase3_ab.py score`, using a
   `--first-useful` pattern that matches the first successful edit.
4. record the comparison here, or in a dated run file next to this one,
   with the rco version, repo SHA, budget, and token source. Claim
   savings only when `savings_claimable` is true.

Until that run exists, no savings figure belongs in the README.

## not done (by design)

- No live agents are launched by the harness or by this slice's tests.
- No counting comparison is recorded yet: it waits on the 0.1.0 forest
  release.
- No new CI job: the counting run needs seated tester desks, as phase 0
  did. The harness's pure functions and fixture-log scoring run in
  `tests/test_phase3_ab.py` on every PR that touches them.
