# Java replay (card 1362)

Method: `rco replay`'s per-commit measure (`rco.replay._one`), tiktoken
`o200k_base`, budget 12000, non-merge code commits since 2026-11-01, at
most 200 code files per commit. The files counted are `rco.meter.read_set`
(card 1359): without rco is the changed files, plus one import hop, plus the
importers of a changed public signature; with rco is the pack plus the
changed files. A skipped pack is a tie.

## google/guava

| item | value |
|---|---|
| repo | google/guava, head pinned at `cdb3863e02e9e728848727b3c132f94f8c22cd26` (2026-11-25), 400-commit shallow clone |
| commits | 45 code commits since 2026-11-01; 43 replayed, 1 root or boundary commit, 1 over 200 files |
| losses (with > without) | 0 |
| ties (skipped packs) | 0 |
| median tokens without rco | 250,766 |
| median tokens with rco | 37,913 |
| saving (median with against median without) | 84.9% |
| saving per commit, median | 79.5% (smallest 4.5%) |
| median files in the import hop | 40 |
| commits where importers were added | 5 (62 changed-name call sites in importers, found in 2 of those commits; the pack does not carry call-site lines yet, card 1360) |

Edges: `import a.b.C;` resolves under any source root (the importer's own
`package` line picks the root when two match, such as `android/guava` and
`guava`); a package import resolves to the classes the file names; a class
of the same package that the file names counts with no import, also when
the package sits under another source root (a test under `guava-tests/test`
names a class of `guava/src`); of several twins the importer's own root
wins, then the nearest path, then the root whose words overlap most. A
class is in a package when its own `package` line says so, so a directory
that only ends with the package path (`a/b/util` for `package util;`) does
not count, and files with no `package` line share the default package in
every root. Twin files that tie on every rule all get an edge.

Check on test files: in guava, 304 files `guava-tests/test/**/XTest.java` have a
same-package `guava/src/**/X.java`; 291 now have an edge to it (85 before
the fix, when only the importer's own directory was searched). The other 13
name the class only in a comment or as part of a longer name.

### How much of the saving comes from same-package edges

A class used from its own package needs no import, so the resolver adds an
edge when the file names it. To show the result does not depend on that, the
same replay with only edges from `import` statements:

| edges | median without | median with | saving | losses | median import hop |
|---|---|---|---|---|---|
| imports and same-package use (shipped) | 250,766 | 37,913 | 84.9% | 0 | 40 |
| imports only | 183,879 | 36,030 | 80.4% | 0 | 20 |

## Limits

- The pack is aimed at exactly the files the commit changed, as for every
  replay; this is an estimate, not an agent measurement.
- Guava is a library with deep package structure. A repo with few imports
  between its files would show less.
- Public means `public` or `protected`; package-private types and members
  are not in the Tree (they still count as import edges).
- One shallow clone and one window (since 2026-11-01). The pinned head makes
  the run repeatable: check out `cdb3863e02e9`, then rerun this replay.
