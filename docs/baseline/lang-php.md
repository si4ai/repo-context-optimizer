# PHP replay (card 1365)

Method: `rco replay`'s per-commit measure (`rco.replay._one`), tiktoken
`o200k_base`, budget 12000, non-merge code commits since 2026-11-01, at
most 200 code files per commit. The files counted are `rco.meter.read_set`
(card 1359): without rco is the changed files, plus one import hop, plus the
importers of a changed public signature; with rco is the pack plus the
changed files. A skipped pack is a tie.

## laravel/framework

| item | value |
|---|---|
| repo | laravel/framework, head pinned at `1f4b7cde51ba36252dd71ee3e638e176e55549a9` (2026-11-27), 783-commit shallow clone, 3,211 `.php` files |
| commits | 207 non-merge commits since 2026-11-01; 187 touch code, 186 replayed, 1 over 200 files |
| losses (with > without) | 0 |
| ties (skipped packs) | 5 |
| median tokens without rco | 81,337 (81,336.5) |
| median tokens with rco | 22,703 |
| saving (median with against median without) | 72.1% |
| saving per commit, median | 60.5% (smallest 0.0%, a tie) |
| median files in the import hop | 16 |
| commits where importers were added | 61 (27 changed-name call sites in importers, found in 6 of those commits; the pack does not carry call-site lines yet, card 1360) |

Edges: `use A\B\C;` (also a group `use A\{B, C as D}`, `use function`, and
aliases) and a name the file writes in its own namespace or fully qualified
map through the composer.json `autoload` and `autoload-dev` psr-4 tables
(the longest matching prefix; every composer.json of the repo counts,
relative to its own folder, so the split packages of laravel, such as
`src/Illuminate/Bus/composer.json`, work) to a file. A name no prefix finds
is looked up by the namespace and name the files declare. A literal
`require` or `include` path maps directly; a dynamic path adds no edge. A
call to a global function adds no edge unless the file imports it, so the
`helpers.php` files are not linked everywhere.

### How much of the saving comes from same-namespace edges

A PHP file needs no `use` for a class of its own namespace, so the resolver
adds an edge when the file names one. The same replay with edges from `use`
statements, fully qualified names and `require` only:

| edges | median without | median with | saving | losses | median import hop |
|---|---|---|---|---|---|
| `use` and same-namespace names (shipped) | 81,337 | 22,703 | 72.1% | 0 | 16 |
| `use` and fully qualified names only | 77,772 | 22,701 | 70.8% | 0 | 15 |

The saving falls from 72.1% to 70.8% without same-namespace edges, with no loss
(9 ties against 5): laravel's files mostly import what they use, so the
same-namespace rule adds little here.

## Limits

- The pack is aimed at exactly the files the commit changed, as for every
  replay; this is an estimate, not an agent measurement.
- 187 code commits on one library; laravel is a framework with a split-package
  layout (psr-4 per package), which the composer rule is built for. A repo
  with no composer.json psr-4 table falls back to the declared namespaces.
- Public means not `private`; `private` members are not in the Tree (they
  still count as edges). Names are matched as words, so an edge can be wider
  than a real use.
- PHP files that mix HTML and code are read between `<?php` and `?>` only;
  `eval`, `include` of a computed path, and `class_alias` are not followed.
- One shallow clone. The pinned head makes the run repeatable: check out
  `1f4b7cde51ba`, then rerun this replay.
