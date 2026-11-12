# Phase 4 pinned contracts

Card 1188 (`repo-context-optimizer/phase4-pinned-contracts`). README roadmap, Phase 4: a developer reviews a Tree and commits it as the intended contract; every PR re-extracts and reports drift from the pin as a visible check, not a block. Built on Phase 1 only (Trees, `model.walk` keys, the `rco diff` comparison); no Forest, no store.

## Pin format (`rco.pin/1`)

Canonical JSON with exactly these keys:

| key | type | meaning |
|---|---|---|
| `schema` | str | `rco.pin/1` |
| `sha` | str | commit the pin was taken at |
| `paths` | list[str] | the pinned files, sorted |
| `nodes` | list[node] | the Tree top nodes of those files (tree-contract node objects) |

A pin holds only the pinned files, not their whole package-grain module, and carries no `module_hash`, so a body-only edit never drifts.

## Commands

| Command | Report | Exit 1 when |
|---|---|---|
| `rco pin [SHA] --for FILE...` | the pin (`--format json`), or a review listing (markdown) | never |
| `rco drift PIN... [--at SHA]` | `rco.drift/1` | any added, removed or changed shape |

`PIN` is a pin file or a directory of `*.json` pins. A path that is not a file at SHA, an unreadable pin, or an empty pin directory is a usage error (exit 2). Doc-only drift is reported and exits 0, as in `rco diff`.

To pin: review `rco pin --for FILE`, then commit `rco pin --for FILE --format json --out pins/NAME.json`. To accept an intended change, re-pin and commit the new pin in the same PR.

## CI

The `drift_report` job runs `rco drift pins` on every PR and push (not in the merge queue, which re-checks the same head). The report goes to the job summary; drift adds a warning annotation and the job still passes. Only exit 2 or 3 fails the job. The repo pins its own frozen data model (`src/rco/model.py`) and output contract (`src/rco/output.py`).
