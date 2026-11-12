# Phase 0 baseline

The README roadmap's Phase 0: numbers measured before any rco code exists,
so later phases have something to compare against. Card 1164
(`repo-context-optimizer/phase0-baseline`).

The README asks Phase 0 for five things:

| README asks (section) | Answer below |
|---|---|
| Tokens and reads before the first useful action, plus wrong guesses, from cold-agent logs (Metrics, Roadmap) | [A. Cold-agent cost](#a-cold-agent-cost) |
| How many modules a typical merge touches (incremental update) | [B. Modules per merge](#b-modules-per-merge) |
| Extract cost, and how long a re-extract takes (Roadmap, incremental update) | [C. Extract cost](#c-extract-cost) |
| Cost and hit rate on a large repo (Just works table) | [B](#b-modules-per-merge) hit rate, [C](#c-extract-cost) cost |
| Default `--budget` 8000, confirmed against Phase 0 pack sizes (Open questions) | [D. Pack sizes and `--budget`](#d-pack-sizes-and---budget) |

## Inputs

- the-mill: `si4ai/the-mill` main at
  `5d58a605839cc79d1b5a21a488efb19df1972eb9` (2026-11-12 01:19 UTC,
  card 1153 #421), a fresh clone.
- Script: `tools/phase0_baseline.py` (standard library only; git is the only
  outside program). The full output of the run below is
  [`phase0-run.json`](phase0-run.json).
- Python 3.13.5 on an 8-vCPU x86-64 Linux box; CI runs 3.12.

Reproduce:

```sh
git clone https://github.com/si4ai/the-mill.git mill
python3 tools/phase0_baseline.py all --repo mill \
    --rev 5d58a605839cc79d1b5a21a488efb19df1972eb9 \
    --cold COLD_LOG [COLD_LOG ...] > phase0-run.json
# or one part: merges | extract | cold
python3 tools/phase0_baseline.py merges --repo mill --rev main --last 100
```

## A. Cold-agent cost

**Kit.** The mill's cold-test kit is `tools/cold_measure_head.py` (card 813)
plus the room-barker recipe in `docs/dev/barker-kit.md`. A fresh `cold-*`
desk gets only `JOIN.md` and the mill-desk SKILL (`docs/protocol/skill.md`);
Grok Bot testers also read the Grok Bot guide. The tester logs every command
with exit code, stdout bytes and time, and writes one `SUMMARY turn=...` line
per turn (join, task, leave). The fixed task (letter 2531) is "What is desk
grok-tui doing right now, on which letter or card, and is anyone waiting on
it?"

**Fixed read before the first command** (kit files at the mill SHA above,
characters / 4):

| File | Est. tokens |
|---|---:|
| `docs/JOIN.md` | 1,081 |
| `docs/protocol/skill.md` (mill-desk SKILL) | 4,575 |
| `docs/protocol/models/grok-bot/SKILL.md` (Grok Bot guide) | 1,609 |
| JOIN + SKILL / with the guide | 5,656 / 7,265 |

**Existing baseline: Gardener's cold-measure letters** (mill mail, scored by
hand by the Gardener desk; rows are commands / wrong guesses / retries /
seconds / stdout bytes):

| Letter | mill | Join | Task (2531) | Leave |
|---|---|---|---|---|
| 2535 | 0.7.15 (baseline) | not reported | 15 / 2 / 1 / 65 s / ~114 KB, 1 of 3 lines correct | not reported |
| 2972 | 0.7.18 | infra-blocked (barker 400) | 1 / 0 / 0 / 5 s / 1.2 KB | missed remove-inside-connect |
| 3198 | 0.7.19 | 9 / 0 / 0 / 28 s / 4.1 KB | 1 / 0 / 0 / 6 s / 1.4 KB | 1 / 0 / 0 |
| 3390 | 0.8.0 | 9 / 0 / 0 / 31 s / ~4.9 KB | 1 / 0 / 0 / 1 s / 1.4 KB | 1 / 0 / 0 |

3390 is the "last live run" that `tools/cold_measure_head.py` scores against.

**Raw cold-tester logs** (kit format, parsed by `phase0_baseline.py cold`;
the logs live in the tester kit directories on the desks' box, not in git):

| Log (kit dir) | Tester, mill | Join: cmds / wrong / retries / s / stdout B | Reads before first `mill join` (stdout B) | Other turns |
|---|---|---|---|---|
| `rdoing/u/log.md` | cold-doing-0813, 0.8.13 | 4 / 0 / 0 / 16 / 3,171 | 2 (1,998) | task 2 / 0 / 0 / 11 s / 9,022 B |
| `r715/a/log.md` | cold-r715-a, 0.7.15 | 8 / 0 / 0 / 22 / 9,078 | 6 (8,067) | wake 4 / 0 / 0 / 15 s |
| `rtw/r/log.md` | cold-tw-368 | 2 / 0 / 0 / 3 / 758 | 1 (not measured) | task 8 / 0 / 0 / 70 s; leave 3 / 1 / 2 / 52 s |
| `uploads/agy-cold-1-log.md` | cold join on Windows | 5 / 0 / 0 / 362 / not recorded | 4 (1,173) | none |

Across those four join turns: commands p50 4 (2 to 8), wrong guesses 0,
retries 0, reads before the first useful action p50 2 (1 to 6).

One more hand-written record: `rdoing/j-record.txt` (lean build-ui start on
mill 0.8.2): join+ack 6 commands, 2 wrong guesses, 3 retries, 43 s, 2.1 KB;
"total read ~12KB (~3K tokens est, platform total not visible)".

**Reading.** On mill 0.8.x a cold join costs about 2 to 9 commands, 0 wrong
guesses and 3 to 9 KB of stdout (about 0.2K to 2.3K tokens) on top of the
5.7K to 7.3K-token kit read. The task that cost 15 commands, 2 wrong
guesses and 114 KB on 0.7.15 costs 1 to 2 commands and 1.4 to 9 KB now.
Wrong guesses cluster in leave (rtw/r) and in desks given extra
substitutions (j-record), not in join.

**Missing, and why.**
- **Platform token counts.** No log has them: the kit records stdout bytes,
  and every tester notes the platform total is not visible to it. The token
  figures above are characters / 4 of stdout and kit files, which leaves out
  the system prompt, the agent's own reasoning and its replies. Getting real
  numbers needs the agent runtime to report tokens per turn; the Phase 3
  A/B needs that before it can claim token savings.
- **Cold runs on a code task.** Every log is a mill-protocol task (join,
  "what is X doing", leave). None is a cold agent reading the-mill's code to
  change it, which is what rco packs are for. Card 1110 (run cold tests on
  real cards under tw) is the place to add one; until then reads before the
  first code edit are not measured.
- **Wrong-guess definition.** Testers self-report wrong guesses; 2535 was
  scored against a ground-truth snapshot, the box logs were not.

## B. Modules per merge

**Population.** Every first-parent commit on the-mill main from `9abc6bf3`
(2026-10-19) to `5d58a605`, skipping the root import: **305 merges**, of
which 9 are release merge commits (diffed against their first parent) and the
rest squash merges. `git log --first-parent --diff-merges=first-parent
--no-renames --name-only`.

**Module, two grains** (`module_of` in the script):
- **coarse**: the first path part, except under the `agent_coord` package,
  where it is the first two (`agent_coord/server`, `agent_coord/client`,
  `agent_coord/web`, `agent_coord/__main__.py`, ...). Repo-root files are
  their own module (`README.md`, `pyproject.toml`). 28 modules at the SHA.
- **package**: the directory holding the file (its Python package), for
  example `agent_coord/server/api/mill`, `tests/server/records`,
  `docs/protocol`; root files are `.`. 119 modules at the SHA.

| Per merge | p50 | p90 | p99 | max | mean |
|---|---:|---:|---:|---:|---:|
| coarse modules | 3 | 5 | 9 | 13 | 3.08 |
| coarse modules with a `.py` change | 2 | 4 | 7 | 10 | 1.78 |
| package modules | 4 | 11 | 26 | 36 | 5.10 |
| package modules with a `.py` change | 2 | 6 | 15 | 19 | 2.53 |
| files | 5 | 20 | 151 | 247 | 11.65 |

Coarse histogram (modules: merges): 1: 70, 2: 54, 3: 92, 4: 37, 5: 23, 6: 9,
7: 5, 8: 8, 9: 4, 10: 2, 13: 1.

Most touched (coarse, merges out of 305): tests 222, docs 214,
agent_coord/server 116, tools 66, agent_coord/client 55,
agent_coord/__main__.py 44, pyproject.toml 37.

**Hit rate** (share of the SHA's modules a merge leaves untouched, so an
incremental update can reuse their Trees): coarse p50 0.89, p10 0.82;
package p50 0.97, p10 0.91. This uses the module count at the final SHA
for every merge, an approximation.

Last 100 merges only (`--last 100`, from `8a80dc16`): coarse p50 3, p90 7;
package p50 3, p90 13; files p50 5, p90 29. Recent merges are a bit wider
at the tail.

## C. Extract cost

Stand-in until Phase 1's extractor exists: `ast.parse` of every tracked
`*.py` file at the SHA, then a body-stripped public skeleton (signatures,
type hints, the first docstring line, UPPER_CASE constants, `__all__`,
annotated class fields; no imports, bodies or private names) via
`ast.unparse`. Sources are read from git once, outside the timed loops.

Files parsed: **872 files, 161,289 lines, 5,915,804 characters (about 1.48M
estimated tokens raw)**, no syntax errors.

| Measure | Value (3 runs) |
|---|---|
| Parse all files, wall | 0.61 to 0.70 s |
| Parse per file p50 / p99 / max | 0.34 to 0.38 / 6.3 to 7.7 / 31 to 33 ms |
| Parse + skeleton all files, wall | 0.76 to 0.81 s |
| Parse + skeleton per file p50 / p99 | 0.47 to 0.51 / 7.2 to 8.2 ms |
| tracemalloc peak, one tree at a time | 20.1 MB |
| tracemalloc peak, all 872 trees held | 250.2 MB |
| Process max RSS (whole script) | 586 MB |

So a full extract of the-mill's Python is under a second on one core, and
re-extracting a typical merge's 2 Python packages (B) is milliseconds. Memory
is the constraint to watch: hold one tree at a time, not the whole forest.

**Missing, and why.** Non-code contracts (CLI flags, SQL tables, routes) and
non-Python files are not extracted; those extractors are Phase 1 work, so
their cost is not measured here. Timings are from one box and will move with
the CPU; the Validator should compare runs on the same box.

## D. Pack sizes and `--budget`

Skeleton sizes at the SHA, in estimated tokens (characters / 4):

| Skeleton | Est. tokens |
|---|---:|
| Whole repo, all 872 files | 216,900 |
| `tests` alone | 139,866 |
| `agent_coord` (the product code) | 57,497 |
| `tools` | 14,615 |
| per file p50 / p90 / p99 / max | 151 / 524 / 1,482 / 7,008 |
| per Python package (52) p50 / p90 / max | 736 / 14,101 / 35,180 |

For each merge, the skeleton size of the Python packages it touched (sizes
at the SHA), an upper bound for a `rco pack --for` that merge's files
without the one-hop ring:

| | p50 | p90 | p99 | merges within 8,000 |
|---|---:|---:|---:|---:|
| all Python packages | 26,638 | 84,668 | 169,469 | 82 of 305 |
| code only (no `tests/`) | 2,315 | 22,700 | 45,289 | 173 of 305 |

**Reading for the default `--budget` 8000.** A whole-repo skeleton of the
mill (57K tokens for `agent_coord` alone) does not fit; the README's
whole-repo default is the Forest, not Trees, so this is expected. A
`--for` pack of a typical merge's code fits (p50 2.3K), but about 4 merges
in 10 would trim, and the largest code packages (`agent_coord/server` 14.2K,
`agent_coord/server/records` 11.3K) are over budget on their own, so they
need per-file Trees rather than per-package ones. The five largest packages
overall are test packages (`tests/server` 35.2K). Tests should be left out of a
default pack. Phase 1 should split modules by the README's size caps and
re-run D before changing the default.

## Not done (by design)

- No rco code: Phase 0 is the baseline only.
- No new CI job: the script needs a mill clone and cold logs, so it is run by
  a desk, not on every PR. Its pure functions are tested in
  `tests/test_phase0_baseline.py`.
