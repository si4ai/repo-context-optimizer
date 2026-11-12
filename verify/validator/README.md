# Validator orthogonal checks

Validator's own checks on this repository. None reuse Builder's tests or
Auditor's process checks (hygiene, role trailer, hidden code). They run in
`.github/workflows/validator.yml`, a separate status context that is not a
required check. They need Python 3.12+ with PyYAML; `spin_gate.py` also needs
`spin` and `gcc`. Reports go to `--out` (default: `rco-validator` under the
system temp dir), never into the checkout.

| check | command | exit 0 | exit 1 (finding) | exit 2 |
|---|---|---|---|---|
| 1 README ledger | `python3 verify/validator/readme_ledger.py [--ref HEAD] [--offline] [--check-runs] [--red-on LIST]` | no claim has a red status | a claim has a status in `--red-on` | error |
| 2 SPIN gate | `python3 verify/validator/spin_gate.py [--ref HEAD]` (about 5 min) or `--summary FILE` | results match the documented tables and the self-test is green | a mismatch, a missing row, a tool error, or a tampered summary that stays green | spin or gcc missing, or no summary written |
| 3 config tamper | `python3 verify/validator/config_tamper.py --base BASE --head HEAD` or `--selftest` | no finding hidden by the head config, no watched file touched | a dropped finding or a touched watched file | git or scanner error |

All three take `--repo DIR` (default: this checkout).

**1 readme_ledger.** `claims.yaml` lists 42 README promises (id, kind, quote,
probe). The ledger re-finds each quote (a missing quote is STALE), runs its
probe against `git archive REF`, and writes `ledger-<sha>.md` and `.yaml`. A
claim with no probe is UNTESTABLE. By default PASS is the only green status;
`--red-on FAIL` (what CI passes) makes only a false claim red and reports the
rest. Claims about the SPIN rules read the spin_gate run for the same commit
and `--out`, so run check 2 first. The PyPI probe uses the network;
`--offline` marks it UNTESTABLE. The CI-green claim (R20) reads check runs
only with `--check-runs` and `GITHUB_TOKEN` set, so it is UNTESTABLE in CI,
where the run cannot grade itself.

**2 spin_gate.** Exports `verify/spin` at REF to `OUT/spin-<sha>/`, runs the
repo's own `run.sh` unchanged, then parses `results/summary.txt`. Expected
verdicts come from `verify/spin/README.md` at the same REF (main table and
ablations, 49 cells), plus hard rules from the top README: `fixed` passes
P1-P7, `a` fails all seven, and b and b-cas fail at least one. State counts
are compared too (a difference is a note). The verdict does not depend on
run.sh's exit code; a disagreement between the two is printed as a note.
Self-test: six tampered summaries (fixed P1 to FAIL, a P1 to PASS, an
ablation flipped, a row dropped, a gcc ERROR row, no footer) must all turn
the gate red.

**3 config_tamper.** Runs `tools/check_hidden_code.py` from BASE twice on the
same BASE..HEAD diff: once with `ci/hidden-code.toml` as at BASE, once as at
HEAD (absent means scanner defaults). Any finding in the first run missing
from the second is reported. It flags any diff touching
`ci/hidden-code.toml`, `ci/test-select.toml`, `.github/workflows/ci.yml`, or
the scanner itself. `--selftest` clones the checkout's HEAD into a temp dir
and rebuilds the RCO #3 hole: a workflow edit (new job, unpinned action,
secret read) plus a narrowed `control_plane`. It must show 3 findings with the
base config and 0 with the head config, and a control run without the
narrowing must show no drop.
