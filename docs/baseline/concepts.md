# Concept labels and the no-ML baseline (cards 1338 and 1352)

Card 1338 (`the-mill/rco-concept-model`), steps 1 and 2, and card 1352
(`the-mill/rco-concept-taxonomy-v2`). The goal is a concept classifier
that suggests module boundaries (the forest) and links them to files
(the trees), including in repos that keep every file in one place. This
page is the concept set, the labelled subset, and the number any model
has to beat. steps 3 (a trained model) and 4 (optional embeddings) are
later cards.

Everything here is deterministic and offline once the corpus is fetched.
Nothing in it is part of the rco package or its runtime; it adds no
dependency.

## Files

| Path | What it is |
|---|---|
| [`concepts/taxonomy.json`](concepts/taxonomy.json) | The concept set, version 2.0.0: a superset of v1 with id, group, definition, source, status (`trained`, `untrained`, or `dropped`), optional aliases, and lowercase hint words on trained concepts |
| [`concepts/corpus.json`](concepts/corpus.json) | 26 public repos: URL, pinned commit, licence, language, layout, split |
| [`concepts/labels/`](concepts/labels/) | One file per repo: labelled module directories and files |
| [`concepts/baseline-run.json`](concepts/baseline-run.json) | Full output of the baseline run below, per concept |
| [`../../tools/concept_baseline.py`](../../tools/concept_baseline.py) | `check`, `fetch`, `items`, `auto`, `score` (standard library only) |
| [`../../tests/test_concept_baseline.py`](../../tests/test_concept_baseline.py) | Checks the committed data and the scorer |

Only URLs, commits, licences, paths and labels are stored. No code from
the corpus repos is copied into rco.

## Concept set

Version 2.0.0 is a superset of v1.0.0. Every v1 id is unchanged. The
baseline labels and `baseline-run.json` still name taxonomy version
1.0.0; they were not relabelled. New concepts start as `untrained`. A
later A/B test per concept decides what earns its tokens.

Counts by group (trained in v1, plus untrained additions):

- **layer** (11, all trained): cli, api, ui, persistence, domain, config,
  io, tests, build, util, os.
- **pattern** (22): the 10 v1 GoF patterns (factory, which covers Factory
  Method and Abstract Factory, plus adapter, observer, strategy,
  decorator, command, visitor, chain, proxy, iterator) and the other 12
  GoF patterns (builder, prototype, singleton, bridge, composite,
  facade, flyweight, interpreter, mediator, memento, state,
  template_method).
- **arch** (37): the 7 v1 styles (repository, pipeline, plugin, mvc, di,
  messaging, client_server) plus layered, hexagonal, onion, clean,
  event_sourcing, cqrs, broker, blackboard, event_driven, microservices,
  modular_monolith, monolith, soa, rest, peer_to_peer, space_based,
  serverless, lambda, kappa, saga, strangler, api_gateway, bff, sidecar,
  ambassador, service_mesh, mvp, mvvm, pac, cqrs_read_model. Pipes and
  Filters and Microkernel stay as aliases of pipeline and plugin.
- **ood** (13): the 4 v1 concepts plus encapsulation, polymorphism,
  abstraction, composition, delegation, generic, mixin, trait,
  abstract_class.
- **xcut** (13): the 9 v1 concerns plus audit, privacy,
  rate_limit_concern, feature_toggle_concern.
- **parse** (6): lexer, parser, ast, codegen, plus typecheck, optimizer.
- **net** (7): http, transport, client, server, plus rpc, websocket,
  graphql.
- **ser** (3): serialization, plus schema, migration.
- **cs** (24): data_structures, ml, plus complexity, recursion, graphs,
  automata, formal_languages, sorting, searching, hashing, trees,
  dynamic_programming, greedy, divide_and_conquer, backtracking,
  string_algorithms, computability, types, memory_management, numeric,
  probability, information_theory, distributed_algorithms, logic.
- **scm** (4): vcs, packaging, plus branching, codeowners.
- **poeaa** (46), from the poeaa catalogue (unit of work, data
  mapper, active record, the gateways, service layer, identity map, and
  the rest). repository, domain model, MVC, value object and plugin are
  aliases of the v1 ids, not new ids.
- **eip** (43): EIP highlights (channels, routers, translators,
  messages, scatter-gather, outbox, and related).
- **concur** (22): concurrency patterns (mutex, monitor, actor, reactor,
  thread_pool, and related).
- **principle** (25): SOLID, GRASP, law of demeter, composition over
  inheritance, DRY, YAGNI, KISS, tell don't ask, CQS, design by
  contract, and related.
- **uml** (25): class, sequence, state, component and deployment views,
  plus association, aggregation, composition and the other UML 2.5.1
  relations and building blocks listed in the file.
- **se** (37): testing kinds, CI and CD, refactoring, versioning,
  observability, documentation, release practices, and related.
- **ddd** (17): entity, aggregate, bounded context, domain event, and
  the other DDD building blocks. Repository and value object stay as
  aliases of the v1 ids.
- **resilience** (7): circuit_breaker, bulkhead, retry, timeout,
  rate_limit, cache_aside, idempotency_key.

Sources are SWEBOK v4, the GoF and POSA pattern books, PoEAA,
Domain-Driven Design, UML 2.5.1, the ACM CCS 2012, Enterprise
Integration Patterns, ISO/IEC 25010, the design-by-contract literature,
CLRS, the theory-of-computation texts, and the other books named on
each concept. ids are stable. A changed definition bumps the minor
version. adding concepts
bumps the minor version; this file is 2.0.0 because the schema gained
status, aliases and dropped records. A concept is never removed: mark
it dropped instead. A renamed concept keeps the old id as dropped. The
corpus and every label file name the taxonomy version they were
labelled against.

## Schema

Each concept has:

- `id` (stable, `group.name`)
- `group`
- `definition` (one line)
- `source` (book or catalogue)
- `status`: `trained`, `untrained`, or `dropped`
- `aliases` (optional list of synonyms, used for lookup)
- `names` and `imports` (lowercase hint tokens; used only when
  `status` is `trained`)

A `dropped` concept also has `dropped_reason` (one line, for example
"A/B gain near zero over N tasks"), `evidence` (a path to the
measurement), `dropped_on` (the date), and `revisit_when` (what would
bring it back). No concept is dropped in v2.0.0; the fields and the
check are in place for the later A/B tests.

## dropped concepts

A dropped concept is a deliberate decision, not a gap. It stays in the
file. future agents must not re-add a dropped id as if it had been
missed. To drop a concept, set `status` to `dropped` and fill the four
fields above; do not delete the entry. A check in
`tools/concept_baseline.py` fails if a v1 id disappears from the file
instead of being marked dropped.

## adding a concept

Before adding a concept that seems to be missing, look it up in
`taxonomy.json` by id and by aliases.

- If it is `dropped`, read `dropped_reason` and `evidence`. revive it
  only if its `revisit_when` condition has happened, and record why
  (change `status` to `untrained` or `trained`, and keep a note of the
  revival in the card or changelog).
- If it is already `trained` or `untrained`, use that id (add an alias
  if the name you had in mind was a synonym).
- If it is absent, it is a real gap: add it as `untrained`, with its
  source.

## Corpus

Twenty-six repos, pinned on 2026-11-27. Twenty-one have a clear folder
structure; five keep their code in one package directory or the repo root
and form the flat stress set. The split is by repo, never by file: 15
structured repos are train, 6 structured repos and all 5 flat ones are
held out. Licences are as GitHub reports them; all are MIT, BSD-3-Clause
or Apache-2.0. `pallets/werkzeug` replaces `celery/celery`, whose licence
GitHub reports as NOASSERTION.

| Repo | Licence | Lang | Layout | Split | Commit | Modules | Files | auto | judged | auto rejected |
|---|---|---|---|---|---|---|---|---|---|---|
| [pallets/flask](https://github.com/pallets/flask) | BSD-3-Clause | python | structured | train | `49943b5a9441` | 6 | 12 | 11 | 28 | 2 |
| [pallets/click](https://github.com/pallets/click) | BSD-3-Clause | python | structured | train | `08b803cf32ce` | 4 | 12 | 7 | 25 | 0 |
| [pallets/jinja](https://github.com/pallets/jinja) | BSD-3-Clause | python | structured | train | `5ef70112a1ff` | 2 | 12 | 5 | 29 | 0 |
| [psf/requests](https://github.com/psf/requests) | Apache-2.0 | python | structured | heldout | `3c7baaa05ab4` | 3 | 12 | 12 | 25 | 2 |
| [encode/httpx](https://github.com/encode/httpx) | BSD-3-Clause | python | structured | train | `e347fb5e93b9` | 5 | 12 | 6 | 36 | 4 |
| [encode/starlette](https://github.com/encode/starlette) | BSD-3-Clause | python | structured | heldout | `63d868e1b890` | 4 | 12 | 11 | 25 | 0 |
| [fastapi/sqlmodel](https://github.com/fastapi/sqlmodel) | MIT | python | structured | train | `8debceb2d5bf` | 8 | 12 | 7 | 35 | 0 |
| [pypa/pip](https://github.com/pypa/pip) | MIT | python | structured | train | `5b4d45102eb5` | 15 | 12 | 18 | 52 | 4 |
| [python-poetry/poetry](https://github.com/python-poetry/poetry) | MIT | python | structured | heldout | `3cc663a73852` | 15 | 12 | 18 | 54 | 3 |
| [pallets/werkzeug](https://github.com/pallets/werkzeug) | BSD-3-Clause | python | structured | train | `0f43c6ebd426` | 10 | 12 | 15 | 39 | 0 |
| [scrapy/scrapy](https://github.com/scrapy/scrapy) | BSD-3-Clause | python | structured | train | `d2052b866c35` | 15 | 12 | 13 | 53 | 2 |
| [marshmallow-code/marshmallow](https://github.com/marshmallow-code/marshmallow) | MIT | python | structured | heldout | `8dd74114b3fc` | 4 | 12 | 5 | 26 | 0 |
| [Textualize/rich](https://github.com/Textualize/rich) | MIT | python | structured | train | `3a109ccdffe5` | 4 | 12 | 6 | 24 | 1 |
| [aio-libs/aiohttp](https://github.com/aio-libs/aiohttp) | Apache-2.0 | python | structured | train | `1290e6997822` | 6 | 12 | 4 | 38 | 1 |
| [lark-parser/lark](https://github.com/lark-parser/lark) | MIT | python | structured | heldout | `9a4fb9c7458e` | 6 | 12 | 9 | 33 | 1 |
| [pytest-dev/pytest](https://github.com/pytest-dev/pytest) | MIT | python | structured | train | `6c3902cdb044` | 12 | 12 | 8 | 45 | 0 |
| [python-attrs/attrs](https://github.com/python-attrs/attrs) | MIT | python | structured | train | `af29c2a4a6f6` | 4 | 12 | 4 | 29 | 4 |
| [redis/redis-py](https://github.com/redis/redis-py) | MIT | python | structured | train | `7ec0553cdb4b` | 15 | 12 | 10 | 48 | 3 |
| [tox-dev/tox](https://github.com/tox-dev/tox) | MIT | python | structured | train | `da6036b647a1` | 15 | 12 | 13 | 50 | 6 |
| [gin-gonic/gin](https://github.com/gin-gonic/gin) | MIT | go | structured | train | `0f09c3a9b462` | 6 | 12 | 9 | 28 | 0 |
| [axios/axios](https://github.com/axios/axios) | MIT | javascript | structured | heldout | `71c87d506567` | 12 | 12 | 8 | 44 | 3 |
| [Aider-AI/aider](https://github.com/Aider-AI/aider) | Apache-2.0 | python | flat | heldout | `5dc9490bb35f` | 7 | 20 | 4 | 56 | 1 |
| [simonw/llm](https://github.com/simonw/llm) | Apache-2.0 | python | flat | heldout | `46f0461d9b1e` | 3 | 20 | 16 | 41 | 2 |
| [simonw/sqlite-utils](https://github.com/simonw/sqlite-utils) | Apache-2.0 | python | flat | heldout | `9c66bafa52ae` | 2 | 20 | 8 | 42 | 0 |
| [karpathy/nanoGPT](https://github.com/karpathy/nanoGPT) | MIT | python | flat | heldout | `3adf61e154c3` | 1 | 12 | 6 | 21 | 0 |
| [TheR1D/shell_gpt](https://github.com/TheR1D/shell_gpt) | MIT | python | flat | heldout | `a082bd5327ce` | 4 | 20 | 8 | 49 | 4 |
| total | | | | | | 188 | 344 | 241 | 975 | 43 |


`fetch` reproduces the checkouts:

```sh
python3 tools/concept_baseline.py fetch --clones /tmp/rco-concepts
python3 tools/concept_baseline.py score --clones /tmp/rco-concepts > run.json
```

## Labels

**What gets labelled.** For each repo, `items` picks the code directories
with two or more code files (Python, JavaScript, TypeScript, Go), skipping
docs, examples, vendored code, fixtures and hidden directories, up to 15
by size with at most 3 under test directories; plus a sample of 12 files
(20 in flat repos) by a hash of repo id and path, at most a quarter of
them tests. The same commit always gives the same items. 532 items:
188 modules and 344 files.

**Labels.** Each item carries one or more concept ids and a short
evidence note from its file list, docstring, top-level names or imports.
A test file gets `layer.tests` plus the concept of what it tests.

**Provenance.**

- `auto`: the concept's hint words contain the item's own directory name
  or file stem exactly (for example `cli.py` gives `layer.cli`), and the
  labeller kept it.
- `judged`: the labeller added it from the evidence.
- `auto_rejected` lists name matches the labeller removed, such as
  `llm/tools.py`, which holds LLM tools rather than build tools.

Overall: 241 auto, 975 judged, 43 auto matches rejected. One labeller
did the judging, with every held-out item read on its evidence. A human
or the Validator should spot-check a sample before step 3 trains on it.

## Baseline method

A score for every concept on every item, from three signals, and one
threshold:

1. **Names.** Path parts are split on separators and camel case. A token
   in a concept's hint words scores 1.0 for the item's own name, 0.5 for
   a file's parent directory and 0.25 for higher directories (the best one
   counts). A module also adds half the mean score of its files.
2. **Import lexicon.** Each file's imports that do not resolve inside the
   repo (Python `ast`, Go and JavaScript import lines) are matched against
   each concept's `imports` list (`sqlite3` for persistence, `argparse`
   for cli, `torch` for ml), adding 0.5 per hit.
3. **Import graph.** Imports that do resolve inside the repo make an
   undirected file graph. Two ways to use it: `graph` adds half the mean
   name score of a file's neighbours; `clusters` first drops package
   facades (`__init__.py`, `index.js`) and hubs (degree above
   max(8, 3 x median)), runs label propagation (sorted order, ties to the
   smallest label, at most 20 rounds), and adds half the mean name score
   of the file's cluster.

The weights were set by hand before any score was read. The threshold is
the one that gives the best micro F1 on the train split, from 0.25 to 2.0
in steps of 0.05, and is then applied unchanged to the held-out repos.

Micro averages count every item and concept decision together; macro
averages the per-concept F1 over concepts with held-out support.

## Results

| Configuration | Threshold | Set | Items | Micro P | Micro R | Micro F1 | Macro P | Macro R | Macro F1 |
|---|---|---|---|---|---|---|---|---|---|
| `names` | 0.3 | train | 307 | 0.681 | 0.492 | 0.571 | 0.673 | 0.404 | 0.460 |
| `names` | 0.3 | heldout_structured | 116 | 0.698 | 0.530 | 0.602 | 0.514 | 0.415 | 0.433 |
| `names` | 0.3 | heldout_flat | 109 | 0.559 | 0.434 | 0.489 | 0.491 | 0.394 | 0.387 |
| `names+imports` | 0.55 | train | 307 | 0.681 | 0.414 | 0.515 | 0.679 | 0.356 | 0.429 |
| `names+imports` | 0.55 | heldout_structured | 116 | 0.756 | 0.459 | 0.571 | 0.542 | 0.371 | 0.412 |
| `names+imports` | 0.55 | heldout_flat | 109 | 0.577 | 0.402 | 0.474 | 0.487 | 0.412 | 0.411 |
| `names+imports+graph` | 0.6 | train | 307 | 0.651 | 0.447 | 0.530 | 0.661 | 0.384 | 0.441 |
| `names+imports+graph` | 0.6 | heldout_structured | 116 | 0.689 | 0.493 | 0.575 | 0.522 | 0.392 | 0.417 |
| `names+imports+graph` | 0.6 | heldout_flat | 109 | 0.516 | 0.450 | 0.481 | 0.442 | 0.433 | 0.402 |
| `names+imports+clusters` | 0.55 | train | 307 | 0.655 | 0.442 | 0.527 | 0.653 | 0.375 | 0.435 |
| `names+imports+clusters` | 0.55 | heldout_structured | 116 | 0.693 | 0.493 | 0.576 | 0.515 | 0.395 | 0.414 |
| `names+imports+clusters` | 0.55 | heldout_flat | 109 | 0.530 | 0.422 | 0.470 | 0.443 | 0.418 | 0.395 |

Two runs on two separate fetches produced byte-identical output
(sha256 `b0c68eb0c1f6...`).

**The number to beat is `names` on held out: micro F1 0.602 on structured
repos and 0.489 on flat ones; macro F1 0.433 and 0.387.** Imports and the
graph did not help: the import lexicon raised precision on structured
repos (0.698 to 0.756) but cost more recall than it gained, and neither
graph signal beat names alone on train, so neither is the baseline.

### Per concept, `names`, held out

Support is the number of held-out items whose gold labels include the
concept.

| Concept | Structured P | R | F1 | support | Flat P | R | F1 | support |
|---|---|---|---|---|---|---|---|---|
| `layer.cli` | 1.00 | 0.46 | 0.63 | 13 | 0.75 | 0.26 | 0.39 | 23 |
| `layer.api` | 0.75 | 0.30 | 0.43 | 10 | 0.00 | 0.00 | 0.00 | 5 |
| `layer.ui` | 0.00 | 0.00 | 0.00 | 0 | 0.00 | 0.00 | 0.00 | 5 |
| `layer.persistence` | 0.00 | 0.00 | 0.00 | 0 | 0.58 | 0.32 | 0.41 | 22 |
| `layer.domain` | 0.75 | 0.60 | 0.67 | 5 | 0.50 | 0.67 | 0.57 | 3 |
| `layer.config` | 0.50 | 0.80 | 0.61 | 5 | 0.64 | 0.60 | 0.62 | 15 |
| `layer.io` | 1.00 | 0.75 | 0.86 | 4 | 1.00 | 0.20 | 0.33 | 5 |
| `layer.tests` | 1.00 | 0.97 | 0.98 | 32 | 1.00 | 1.00 | 1.00 | 31 |
| `layer.build` | 0.57 | 0.67 | 0.61 | 6 | 0.57 | 0.67 | 0.61 | 6 |
| `layer.util` | 0.35 | 1.00 | 0.52 | 11 | 0.31 | 1.00 | 0.48 | 5 |
| `layer.os` | 0.67 | 1.00 | 0.80 | 4 | 1.00 | 0.29 | 0.44 | 7 |
| `pattern.factory` | 0.00 | 0.00 | 0.00 | 3 | 0.00 | 0.00 | 0.00 | 2 |
| `pattern.adapter` | 1.00 | 1.00 | 1.00 | 2 | - | - | - | 0 |
| `pattern.observer` | 1.00 | 1.00 | 1.00 | 1 | 1.00 | 1.00 | 1.00 | 2 |
| `pattern.strategy` | 0.00 | 0.00 | 0.00 | 2 | 0.00 | 0.00 | 0.00 | 9 |
| `pattern.decorator` | 0.00 | 0.00 | 0.00 | 1 | - | - | - | 0 |
| `pattern.command` | 1.00 | 0.75 | 0.86 | 8 | 0.14 | 0.33 | 0.20 | 3 |
| `pattern.visitor` | 0.00 | 0.00 | 0.00 | 2 | - | - | - | 0 |
| `pattern.chain` | 1.00 | 0.86 | 0.92 | 7 | 0.40 | 1.00 | 0.57 | 2 |
| `arch.repository` | 0.67 | 0.67 | 0.67 | 3 | - | - | - | 0 |
| `arch.plugin` | 1.00 | 0.50 | 0.67 | 2 | 1.00 | 0.43 | 0.60 | 14 |
| `arch.mvc` | 0.00 | 0.00 | 0.00 | 2 | 0.00 | 0.00 | 0.00 | 0 |
| `arch.messaging` | 0.00 | 0.00 | 0.00 | 0 | - | - | - | 0 |
| `arch.client_server` | 0.50 | 1.00 | 0.67 | 1 | - | - | - | 0 |
| `ood.interface` | 0.50 | 0.25 | 0.33 | 4 | 0.00 | 0.00 | 0.00 | 4 |
| `ood.value_object` | 0.50 | 0.12 | 0.20 | 8 | 0.00 | 0.00 | 0.00 | 1 |
| `ood.inheritance` | 0.00 | 0.00 | 0.00 | 1 | 1.00 | 0.14 | 0.25 | 7 |
| `xcut.auth` | 0.67 | 0.50 | 0.57 | 4 | 0.00 | 0.00 | 0.00 | 0 |
| `xcut.logging` | 0.00 | 0.00 | 0.00 | 1 | 1.00 | 0.67 | 0.80 | 3 |
| `xcut.errors` | 1.00 | 0.89 | 0.94 | 9 | 1.00 | 0.50 | 0.67 | 2 |
| `xcut.concurrency` | 0.67 | 0.50 | 0.57 | 4 | 0.00 | 0.00 | 0.00 | 3 |
| `xcut.caching` | 0.00 | 0.00 | 0.00 | 5 | 1.00 | 0.50 | 0.67 | 2 |
| `xcut.validation` | 0.33 | 0.20 | 0.25 | 5 | 1.00 | 1.00 | 1.00 | 2 |
| `xcut.compat` | 1.00 | 0.80 | 0.89 | 5 | - | - | - | 0 |
| `xcut.i18n` | 0.00 | 0.00 | 0.00 | 1 | - | - | - | 0 |
| `parse.lexer` | 0.00 | 0.00 | 0.00 | 1 | 0.00 | 0.00 | 0.00 | 1 |
| `parse.parser` | 1.00 | 0.64 | 0.78 | 11 | 1.00 | 1.00 | 1.00 | 1 |
| `parse.ast` | 0.67 | 0.50 | 0.57 | 4 | - | - | - | 0 |
| `parse.codegen` | 0.00 | 0.00 | 0.00 | 3 | 0.00 | 0.00 | 0.00 | 1 |
| `net.http` | 0.88 | 0.44 | 0.58 | 32 | 0.00 | 0.00 | 0.00 | 4 |
| `net.transport` | 0.00 | 0.00 | 0.00 | 1 | - | - | - | 0 |
| `net.client` | 0.50 | 0.29 | 0.36 | 7 | 0.10 | 0.29 | 0.15 | 7 |
| `net.server` | 0.00 | 0.00 | 0.00 | 3 | 0.00 | 0.00 | 0.00 | 0 |
| `ser.serialization` | 1.00 | 0.13 | 0.23 | 15 | 0.50 | 0.11 | 0.18 | 9 |
| `cs.data_structures` | 0.33 | 0.20 | 0.25 | 5 | - | - | - | 0 |
| `cs.ml` | 0.00 | 0.00 | 0.00 | 0 | 0.70 | 0.44 | 0.54 | 43 |
| `scm.vcs` | 0.00 | 0.00 | 0.00 | 1 | 0.00 | 0.00 | 0.00 | 1 |
| `scm.packaging` | 0.80 | 0.50 | 0.61 | 16 | 0.50 | 1.00 | 0.67 | 1 |

### The forest: import clusters against folders

Folder purity is, over files in clusters of two or more, the share that
sit in their cluster's most common directory. On the raw graph, label
propagation collapses most repos into one or two clusters, because package
facades and a few hub files connect everything. Pruning them gives more,
smaller clusters, but purity stays mostly under 0.6, partly because
tests import across folders by design.

| Repo | Layout | Split | Files | Folders | Clusters raw | Purity raw | Clusters pruned | Clustered share pruned | Purity pruned |
|---|---|---|---|---|---|---|---|---|---|
| aider | flat | heldout | 152 | 12 | 2 | 0.34 | 24 | 0.59 | 0.69 |
| aiohttp | structured | train | 148 | 11 | 1 | 0.55 | 7 | 0.45 | 0.58 |
| attrs | structured | train | 56 | 4 | 1 | 0.56 | 9 | 0.52 | 0.69 |
| axios | structured | heldout | 266 | 34 | 9 | 0.31 | 34 | 0.43 | 0.51 |
| click | structured | train | 76 | 6 | 2 | 0.33 | 4 | 0.35 | 0.59 |
| flask | structured | train | 65 | 14 | 1 | 0.38 | 4 | 0.43 | 0.54 |
| gin | structured | train | 98 | 7 | 3 | 0.77 | 3 | 0.20 | 0.50 |
| httpx | structured | train | 60 | 5 | 2 | 0.33 | 5 | 0.40 | 0.83 |
| jinja | structured | train | 50 | 4 | 1 | 0.54 | 6 | 0.66 | 0.67 |
| lark | structured | heldout | 53 | 7 | 1 | 0.35 | 3 | 0.77 | 0.37 |
| llm | flat | heldout | 59 | 3 | 1 | 0.68 | 5 | 0.37 | 0.64 |
| marshmallow | structured | heldout | 34 | 5 | 1 | 0.47 | 3 | 0.62 | 0.57 |
| nanogpt | flat | heldout | 12 | 2 | 1 | 1.00 | 1 | 0.33 | 1.00 |
| pip | structured | train | 338 | 31 | 2 | 0.22 | 39 | 0.58 | 0.47 |
| poetry | structured | heldout | 375 | 69 | 8 | 0.13 | 38 | 0.58 | 0.40 |
| pytest | structured | train | 220 | 39 | 2 | 0.34 | 13 | 0.39 | 0.47 |
| redis-py | structured | train | 319 | 30 | 2 | 0.18 | 27 | 0.67 | 0.37 |
| requests | structured | heldout | 35 | 4 | 2 | 0.67 | 5 | 0.43 | 0.80 |
| rich | structured | train | 172 | 4 | 5 | 0.59 | 25 | 0.44 | 0.60 |
| scrapy | structured | train | 545 | 47 | 2 | 0.34 | 63 | 0.52 | 0.48 |
| shell-gpt | flat | heldout | 28 | 6 | 1 | 0.50 | 1 | 0.71 | 0.45 |
| sqlite-utils | flat | heldout | 64 | 2 | 2 | 0.83 | 3 | 0.12 | 0.62 |
| sqlmodel | structured | train | 186 | 50 | 6 | 0.29 | 7 | 0.10 | 0.58 |
| starlette | structured | heldout | 71 | 4 | 2 | 0.33 | 6 | 0.82 | 0.40 |
| tox | structured | train | 269 | 50 | 2 | 0.07 | 26 | 0.44 | 0.44 |
| werkzeug | structured | train | 89 | 12 | 4 | 0.32 | 12 | 0.55 | 0.45 |

## Limits

- **One labeller.** Judged labels come from one reading of names,
  docstrings and imports, not from running the code. Pattern concepts
  (strategy, visitor, observer) are the least certain. They need a
  spot-check before training.
- **Names feed both labels and the baseline.** `auto` labels come from
  name matches, so the names signal is favoured where they agree. The
  run file also reports items with at least one judged label
  (`*_judged_items`); names-only F1 drops there to 0.583 (structured)
  and 0.466 (flat).
- **Small held-out sets.** 116 and 109 items; many concepts have under 5
  held-out examples, so per-concept numbers move a lot with one label.
- **Go and JavaScript are thin.** One repo each. Go files in the repo
  root are not a module (`gin`), and JavaScript resolves only relative
  imports.
- **The flat set is flat by layout, not proven vibe-coded.** It is five
  real projects that put most modules in one directory.
- **Plain label propagation is a weak clusterer here.** Better module
  boundaries (weighted edges, directionality, community detection) are
  step 3 or later work, measured against the purity table above.

## Next

Step 3 (its own card): TF-IDF over identifiers, imports, docstrings and
paths into logistic regression, trained on the train split on the dev
host's CPU, with the model file pinned; scored with this same `score` on
the same held-out repos against `names` above.
