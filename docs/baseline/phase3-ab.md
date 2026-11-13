# phase 3 cold-agent A/B

The README roadmap's phase 3: `rco pack`, A/B tested on cold agents.
Verification layer 3 says the cold-agent A/B is the only evidence that the
map helps. This document is the method. Card 1190
(`repo-context-optimizer/phase3-ab-harness`).

`rco pack` itself landed with the phase 2 fold on `main`. This slice is the
harness only. The counting run is the decided plan below, after RCO 0.1.1
is tagged and installed from the index. The harness never launches agents.

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

## counting run

The counting run is the decided plan in [decided (A/B plan)](#decided-ab-plan).
It starts after RCO 0.1.1 is tagged and installed from the index. The
commands above stay the two-arm log scorer; they do not define the run.

Until that run exists, no savings figure belongs in the README.

## not done (by design)

- No live agents are launched by the harness or by this slice's tests.
- No counting comparison is recorded yet: it waits on the 0.1.1 tag,
  then the decided plan above.
- No new CI job: the counting run needs seated tester desks, as phase 0
  did. The harness's pure functions and fixture-log scoring run in
  `tests/test_phase3_ab.py` on every PR that touches them.

## decided (A/B plan)

The counting run uses the design below. Hashes and the numeric pass
line stay TBD until they are sealed before the first run.

Two baselines are references, not scored arms:

- a cold 256K-context trace at the mill commit 456921fa. It was
  accurate on file:line facts, recalled about one third of callers
  (it missed callers including rpc.py write-side callers and a
  floor.py call site), and mapped neither tests nor wall JS.
- a roughly 1M-context architecture review written against an earlier
  main, with the agent allowed to ask the office human questions. It
  gave why-level rationale, had no path:line anchors, and goes stale
  silently.

Round 1 is trees only: three "where" questions at the mill commit
456921fa, at least one in hold or wake code. Round 2 is one "why"
question, after the office human writes its answer.

Gardener picks the questions (it leads agent UX). Before any run, only
a SHA-256 hash of the question set goes in this document. The
questions are published with the key after grading.

Validator builds the answer key without opening any map: a full
`git grep` caller list, plus what breaks when each function is broken
on a scratch branch. The key stays off-repo. Only its hash goes in
this document before the runs. The key is published after grading so
anyone can check the hash.

Validator's separate tree-quality check samples functions that do not
overlap the A/B questions. Any overlap is held until grading.

Arms: one model at 256K, three arms: no pack (task and repo only), a
fresh `rco pack`, and a deliberately stale pack. The stale-pack
commit is 9104fca37a3b0ef33331e5fe9740563cc7f10098 (20 first-parent
commits before 456921fa). It is named here before any run.

Arms are pinned to RCO 0.1.1 from the package index. Dogfooding may
change the tool, but the arms do not move mid-test. Builder receives
the questions only after 0.1.1 is tagged.

At least three runs per arm per question. Log tokens, reads, and
questions asked. Builder strips arm names and shuffles the logs
before handoff.

Questions to the office human are allowed but counted as cost. Each
becomes an RCO card or a docs fix. It separates useful
clarification from questions the repo should already answer. A
confident wrong answer scores worse than asking.

Validator grades blind: caller recall; file:line facts that do not
exist at the commit (any one fails the run); tokens to the first
useful answer; and questions asked. It reports median and spread.

Pass line: a written number set by the office human and sealed by
Validator before any run (example shape: same or better recall, zero
wrong file:line facts, at least 25% fewer tokens). Placeholder: TBD.

If the stale pack scores about the same as the fresh pack, fix the
A/B before trusting any result.

Auditor checks the design (questions frozen, key sealed, pass line a
number) before the run, and the result against evidence after.

Later: a real-handoff test after a code change, for long-running
desks.

Token savings are claimed only from platform or tokenizer token
counts (see the rule in "what it measures" above).

## pre-run record

Values to seal before the first run. Question-set hash, answer-key
hash, pass line, and model name stay TBD. Stale-pack commit, RCO
version, and context size are already decided.

| field | value |
| --- | --- |
| question-set hash | TBD |
| answer-key hash | TBD |
| pass line | TBD |
| stale-pack commit | 9104fca37a3b0ef33331e5fe9740563cc7f10098 |
| RCO version | 0.1.1 |
| model and context size | TBD at 256K |

## learnings

- Questions to the office human helped the review, so questions are
  measured, not banned.
- DO for this run: each desk notes in its handoff when it used an
  `rco pack` and whether the pack helped or misled.
