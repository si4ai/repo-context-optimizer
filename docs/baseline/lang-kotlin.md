# Kotlin replay (card 1364)

Method: `rco replay`'s per-commit measure (`rco.replay._one`), tiktoken
`o200k_base`, budget 12000, non-merge code commits since 2026-11-01, at
most 200 code files per commit. The files counted are `rco.meter.read_set`
(card 1359): without rco is the changed files, plus one import hop, plus the
importers of a changed public signature; with rco is the pack plus the
changed files. A skipped pack is a tie.

## square/okhttp

| item | value |
|---|---|
| repo | square/okhttp, head pinned at `b7d98b0c8e0889b437141c0e951114fa555379cb` (2026-11-26), 400-commit shallow clone |
| commits | 61 non-merge commits since 2026-11-01; 24 touch code, all 24 replayed, 0 over 200 files |
| losses (with > without) | 0 |
| ties (skipped packs) | 0 |
| median tokens without rco | 64,924 (64,923.5) |
| median tokens with rco | 24,726 (24,725.5) |
| saving (median with against median without) | 61.9% |
| saving per commit, median | 64.4% (smallest 25.3%) |
| median files in the import hop | 19.5 |
| commits where importers were added | 2 (24 changed-name call sites in importers, found in 1 of those commits; the pack does not carry call-site lines yet, card 1360) |

Edges: the `package` line of every file is indexed, not its folder, with the
top-level names the file declares. `import a.b.C` (also `as`, a nested name
such as `a.b.C.Inner`, and `a.b.*`) maps to the files declaring that name in
package `a.b`; a Java class is found the same way, and a Java file sees
Kotlin declarations by its imports and its own package. A file sees the other
files of its own package in any source root, and twin declarations (a common
and a jvm source set, say) that tie on every rule all get an edge.

### How much of the saving comes from same-package edges

A Kotlin file needs no import for a class of its own package, so the
resolver adds an edge when the file names one. To show how much of the result
rests on that, the same replay with edges from `import` statements only:

| edges | median without | median with | saving | losses | median import hop |
|---|---|---|---|---|---|
| imports and same-package use (shipped) | 64,924 | 24,726 | 61.9% | 0 | 19.5 |
| imports only | 37,517 | 21,490 | 42.7% | 0 | 7.5 |

The saving falls to 42.7% without same-package edges but stays positive, with
no loss.

## Limits

- A file with top-level functions or properties is also known by its JVM
  facade class: `FooKt` for `Foo.kt`, or the name in `@file:JvmName("X")`
  (files that share a name all get an edge). That is how a Java file that
  calls `FooKt.bar()` finds `Foo.kt`; a facade named any other way is not
  followed.
- The pack is aimed at exactly the files the commit changed, as for every
  replay; this is an estimate, not an agent measurement.
- 24 code commits is a small sample, and okhttp mixes Kotlin with Java
  (576 `.kt` and 71 `.java` files); edges between them count both ways.
- Public means not `private` and not `internal`; those declarations are not
  in the Tree (they still count as import edges).
- Names are matched as words, so an edge can be wider than a real use.
- One shallow clone. The pinned head makes the run repeatable: check out
  `b7d98b0c8e08`, then rerun this replay.
