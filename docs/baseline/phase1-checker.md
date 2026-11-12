# Phase 1 map checker: time limit and first runs

Card 1167 (`repo-context-optimizer/phase1-trees`), step 1. This doc records
the time limit that README slice 2 asks for, along with the RCO SHA it was
measured at.

## Measured at

- RCO: `0516727d7d2811ceb16af4d8119f23e2794b27e1`. That commit holds the
  checker code. This doc lands in the commit after it.
- Python 3.12.15, standard library only, on an 8-vCPU x86-64 Linux box.
- Extractors: python 1. The cli, routes, sql and redact steps are still
  stubs at version 0.

## Time limit

| Run | Measured (3 to 5 runs) | Limit | Enforced by |
|---|---|---|---|
| `rco check --selftest` (six planted drifts) | 0.016 to 0.035 s | 5 s | `tests/test_check.py::test_selftest_time_limit` |
| `rco check MAP` on the-mill (1,541 files, 119 modules) | 1.20 to 1.25 s | 10 s | recorded here; not yet a test |
| `rco check MAP` on this repo (84 files, 41 modules) | 0.05 s | 10 s | recorded here |

`rco check` re-extracts at the SHA and compares the result, so its cost is
the cost of one `rco tree` (1.20 to 1.36 s on the-mill) plus the compare.

## First runs

| Repo | SHA | Map JSON bytes | Findings against its own fresh map | Same bytes on a second run |
|---|---|---:|---:|---|
| the-mill | `22561156ae79` | 1,154,194 | 0 | yes |
| repo-context-optimizer | `0516727d7d28` | 56,147 | 0 | yes |

Both repos parse cleanly: no FILE error nodes. The self-test catches all six
planted drifts. Flag and column drifts are planted in stand-in nodes until
the cli (step 4) and sql (step 2) extractors emit real ones. The output's
`standin` field lists the kinds that still use stand-ins.

## Not measured yet

- The time limit for checking against real outputs (`--help`, the live
  schema, the route table): that part of the checker is not built yet.
- Edges and prose names: those are Phase 2 (Forest).
