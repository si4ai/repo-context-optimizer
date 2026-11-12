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
planted drifts. At this SHA, flag and column drifts were planted in
stand-in nodes; the steps 2 to 6 fold below removed them.

## Steps 2 to 6 fold

- RCO: `4320c6bc7a3bba507de377ea2657f62ea3106040` (all five extractors
  plus redact 2; this doc lands in the commit after it).
- Extractors: cli 1, python 1, redact 2, routes 1, sql 1.
- Python 3.12.15, standard library only, on an 8-vCPU x86-64 Linux box.
- The self-test plants all six drifts on real nodes (no stand-ins).

| Run | Measured (3 runs) | Limit |
|---|---|---|
| `rco check --selftest` (six planted drifts) | 0.014 to 0.015 s | 5 s |
| `rco tree` on the-mill (1,541 files, 119 modules) | 2.21 to 2.41 s | not set |
| `rco check MAP` on the-mill | 2.13 to 2.20 s | 10 s |
| `rco tree` on this repo (108 files, 45 modules) | 0.22 s | not set |
| `rco check MAP` on this repo | 0.15 to 0.16 s | 10 s |

The-mill cost about doubles against step 1 because the cli, sql and routes
extractors now run too.

| Repo | SHA | Map JSON bytes | Findings against its own fresh map | Same bytes on three runs |
|---|---|---:|---:|---|
| the-mill | `22561156ae79` | 1,357,690 | 0 | yes |
| repo-context-optimizer | `4320c6bc7a3b` | 92,890 | 0 | yes |

Node counts on the-mill: 113 COMMAND, 664 FLAG, 55 TABLE, 434 COLUMN,
192 INDEX, 196 PROCEDURE, 51 ROUTE, no FILE error nodes. This repo has
three FILE error nodes, all from the deliberately broken test fixtures.

## Not measured yet

- The time limit for checking against real outputs (`--help`, the live
  schema, the route table): that part of the checker is not built yet.
- Edges and prose names: those are Phase 2 (Forest).
