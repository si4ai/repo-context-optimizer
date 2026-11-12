# Phase 2 Forest contract

Card 1180 (`repo-context-optimizer/phase2-step0-contract`), under card 1179
(`repo-context-optimizer/phase2-forest-release`). This is the shared
contract the Phase 2 steps build against, the same way
[tree-contract.md](tree-contract.md) served Phase 1. Step 0 lands it with the
frozen types and the stubs. Steps 1 to 6 then run in parallel, and each one
edits only the files it owns (see [Ownership](#ownership)). Step 7 is serial
and last: integration, replay and the 0.1.0 release.

Scope comes from the README: Concepts / Forest, Fingerprints, Incremental
update (rules 1 to 4), Planned commands (`rco pack`, `rco update`), Storage
backends (SQLite only), and Roadmap Phase 2. The release is rough on purpose:
Python import edges only, package-grain modules (tree-contract section 4),
no override file, no mill backend, no triage, no A/B claim.

Zero network and no runtime dependencies, as in Phase 1. The LLM prose path
is a hook that ships off.

## 1. Module layout

```
docs/design/forest-contract.md  step 0. this file
src/rco/forest_model.py         step 0. FROZEN types and canonical JSON (sections 2-4)
src/rco/store/__init__.py       step 0. FROZEN Store protocol + MemoryStore fake (section 5)
src/rco/store/sqlite.py         step 0 stub (schema draft) -> step 1
src/rco/imports.py              step 0 stub -> step 2
src/rco/forest.py               step 0 stub -> step 2
src/rco/update.py               step 0 stub -> step 3
src/rco/prose/__init__.py       step 0 stub (queue worker) -> step 4
src/rco/prose/mine.py           step 0 stub -> step 4
src/rco/prose/check.py          step 0 stub -> step 4
src/rco/prose/llm.py            step 0 stub (hook, off) -> step 4
src/rco/pack.py                 step 0 stub -> step 5
src/rco/cli.py                  step 0 adds update, pack, forest (exit 2 until built)
tests/test_phase2_contract.py   step 0
```

`ci/test-select.toml` needs no new rule: `src/**/*.py`, `tests/**/*.py`,
`tests/fixtures/**` and `tests/golden/**` already route to pytest.

## 2. Forest (`rco.forest/1`)

One entry per module of the Map at the same SHA:

| Field | Type | Source |
|---|---|---|
| `id` | str | module id (tree-contract section 4) |
| `layer` | int >= 0 | depth in the import graph after collapsing cycles; a module with no in-repo imports is 0 |
| `depends_on` | sorted ids | in-repo modules this module imports |
| `used_by` | sorted ids | exact mirror of `depends_on` |
| `entry_points` | sorted names | `[project.scripts]` names, `__main__` when the module has `__main__.py`, top-level COMMAND names |
| `owns.commands` / `owns.tables` / `owns.routes` | sorted names | COMMAND qualnames, TABLE and VIEW names, ROUTE names from the module's Tree |
| `tree_hash` | str | the module's TreeHash (tree-contract section 3) |
| `prose` | optional | `{text, source: mined or llm, tree_hash, prompt_version, model_id}` |

Entries sort by `id`; `notes` (cycles, unresolved relative imports) sort as
strings. No line numbers and no stored stale flag anywhere. Canonical JSON is
`canon.canonical_json`; unknown keys are refused on read, as for Trees.

A cycle `a -> b -> a` collapses into one component; every member takes the
component's layer and the Forest gets one note `cycle: a b`.

## 3. ForestHash and staleness

```
ForestHash = sha256 of canonical
  {"schema": "rco.foresthash/1", "tree_hash", "edges" (sorted, unique depends_on),
   "prompt_version", "model_id"}
```

Mined prose uses `prompt_version = "mined/1"` and `model_id = ""`. A prompt or
model change moves ForestHash and so invalidates only the prose (README
Fingerprints).

Staleness is computed at read time and never stored (rule 3):
`stale = prose is not None and prose.tree_hash != current tree_hash`
(`forest_model.is_stale`). Import edges are not in the Tree, so an
import-only change keeps TreeHash; edges are recomputed whenever ModuleHash
changes, and prose is copied forward on equal TreeHash only.

## 4. Pack (`rco.pack/1`) and update (`rco.update/1`)

`rco pack [SHA] [--for FILE...] [--budget N]`. Budget is estimated tokens,
characters / 4 rounded up (`forest_model.estimate_tokens`), default 8000,
measured on the rendered markdown.

JSON keys: `schema, sha, budget, used, forest[], trees[], hop[{id,line}],
trimmed[], stale[], notes[]`. `trimmed` names every module or node that was
cut for budget, in the order cut. `stale` lists module ids shown with stale
prose.

Markdown follows the README example, plus a trimmed line:

```markdown
# rco pack  (SHA=abc1234, budget=8000 estimated tokens)

## Forest
- <id>: <one line> (depends on: a, b) (used by: c)

## Trees (full, for --for <files>)
### <id>
<one line per node, the rco tree markdown>

## One hop (name and one line)
- <id>: <one line>

# budget used: ~N / B estimated tokens
# trimmed: x, y
```

Without `--for` the pack is the Forest only. Cut order when over budget: one
hop lines first, then Tree nodes from the end of each full Tree, then Forest
entries; nothing is cut silently. Exit 1 when the pack shows a stale entry,
else 0 (README Output contract).

`rco update [NEW] [--name main]` prints an `rco.update/1` report:
`name, old, new, swapped, extracted[], queued[], notes[]`. A refused swap is
exit 0 with a note.

## 5. Store protocol (`rco/store/__init__.py`)

The README's five calls, plus the reads and writes they imply:

| Call | Rule |
|---|---|
| `put_node(kind, key, obj)` | content-addressed, idempotent; same key with other bytes raises `StoreConflict` |
| `get_node(kind, key)` | `None` on a miss |
| `list_by_sha(sha)` | the SHA's `IndexRow(module, module_hash, tree_hash)` rows sorted, `()` when not indexed |
| `set_pointer(name, old, new) -> bool` | the only pointer write; one atomic compare-and-swap; `old=None` means absent |
| `post_notice(text)` | optional; a store that cannot post prints to stdout |
| `get_pointer(name)` | read |
| `index_sha(sha, rows)` | whole index at once; same rows again is a no-op, other rows raise |
| `put_prose(module, prose)` / `get_prose(module, tree_hash=None)` | keyed by `(module, tree_hash)`; `None` reads the newest |
| `enqueue` / `queued` / `dequeue` | one row per `(module, tree_hash)`, which is the debounce |

Node kinds: `tree` (canonical Tree JSON keyed by ModuleHash) and `edges`
(`{"depends_on": [...]}` keyed by ModuleHash). `MemoryStore` is the fake every
step can use before step 1 lands; step 1's conformance suite runs against
both.

### SQLite schema (draft, needs the human's sign-off)

```sql
CREATE TABLE node      (kind TEXT, key TEXT, json BLOB, PRIMARY KEY (kind, key));
CREATE TABLE sha_index (sha TEXT, module TEXT, module_hash TEXT, tree_hash TEXT,
                        PRIMARY KEY (sha, module));
CREATE TABLE pointer   (name TEXT PRIMARY KEY, sha TEXT);
CREATE TABLE prose     (module TEXT, tree_hash TEXT, seq INTEGER, json BLOB,
                        PRIMARY KEY (module, tree_hash));
CREATE TABLE queue     (module TEXT, tree_hash TEXT, PRIMARY KEY (module, tree_hash));
CREATE TABLE notice    (seq INTEGER PRIMARY KEY, text TEXT);
```

No stale column (rule 3). `set_pointer` is
`UPDATE pointer SET sha=? WHERE name=? AND sha=?`, or `INSERT` when `old` is
`None` and no row exists, in one transaction; it returns whether a row
changed. The database is `.rco/rco.db` in WAL mode; first use adds `.rco/`
to `.git/info/exclude`, never to `.gitignore`. The plan draft named five
tables; `notice` is the sixth, so `post_notice` has somewhere to write.

## Ownership

| Step | Owns |
|---|---|
| 0 | this doc, `forest_model.py`, `store/__init__.py`, every stub above, the update/pack/forest branches of `cli.py` |
| 1 | `src/rco/store/sqlite.py`, `tests/test_store_sqlite.py`, `tests/store_conformance.py` |
| 2 | `src/rco/imports.py`, `src/rco/forest.py`, the `forest` branch of `cli.py`, `tests/test_imports.py`, `tests/test_forest.py`, `tests/fixtures/forest_*`, `tests/golden/forest_*` |
| 3 | `src/rco/update.py`, the `update` branch of `cli.py`, `tests/test_update.py`, `tests/test_update_props.py` |
| 4 | `src/rco/prose/`, `tests/test_prose_*` |
| 5 | `src/rco/pack.py`, the `pack` branch of `cli.py`, `tests/test_pack.py`, `tests/golden/pack_*` |
| 6 | `.github/workflows/release.yml` and the CI `package` job, `tests/test_wheel_smoke.py` |
| 7 | version, `CHANGELOG.md`, README status lines, `tests/test_replay.py`, `docs/baseline/phase2-dogfood.md` |

Changing a frozen type in `forest_model.py` or `store/__init__.py` is a
step-0 follow-up PR, sent to every open step first.

## Open questions (from the plan, for the human)

1. Mined prose on by default in 0.1.0, with only the LLM path off?
2. Who runs `tw rco update` after merges (Validator after each merge is proposed)?
3. Store per worktree (`.rco/`, the README) or one shared store per repo through `RCO_STORE`?
4. `hypothesis` as a test-only CI dependency, or the seeded stdlib stand-in (proposed)?
5. TestPyPI rehearsal first, or one shot to PyPI?
6. Release gate: Validator Pass plus the human's GO on the SHA, or build-room consensus?
7. Sign-off on the SQLite schema above.

## Amendments in the steps 1 to 6 fold

- Applied: `rco update` re-reads the pointer at swap time and compares against
  that value, not against the value read at start. This is the `fixed`
  variant of the SPIN model (`n >= pointer` inside the atomic step). A lost
  race retries when NEW still descends from the new pointer (step 3).
- Applied: the prose queue worker drops a queued row whose TreeHash the
  published pointer does not show. `rco update` queues only after its swap,
  so such a row was superseded (step 4).
- Applied: `rco pack` refuses `--budget` under 64 (exit 2). An owner Tree
  whose nodes are all cut loses its heading; its nodes are named in
  `trimmed` (step 5).
- Applied: the update/pack/forest placeholders are gone; all three are built.
- Open: `rco check` does not read a Forest yet. `forest.compare_edges` gives
  the `edge` finding (step 2).
- Open: name-in-Tree flags (`prose.flags`) are not shown in the pack or
  counted in `rco check` yet (step 7).
