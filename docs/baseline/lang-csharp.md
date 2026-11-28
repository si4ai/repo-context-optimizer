# C# replay (card 1363)

Method: `rco replay`'s per-commit measure (`rco.replay._one`), tiktoken
`o200k_base`, budget 12000, non-merge code commits, at most 200 code files
per commit. The files counted are `rco.meter.read_set` (card 1359): without
rco is the changed files, plus one import hop, plus the importers of a
changed public signature; with rco is the pack plus the changed files. A
skipped pack is a tie.

## JamesNK/Newtonsoft.Json

| item | value |
|---|---|
| repo | JamesNK/Newtonsoft.Json, head pinned at `52fa3aef1f2cadcd3a3f874251eddc98d3efbbaa` (2026-09-21), 400-commit shallow clone |
| window | since 2024-01-01, not 2026-11-01: the repo has no commit after 2026-09-21. 47 non-merge commits, 37 of them touch code |
| commits | 37 code commits; 37 replayed, 0 over 200 files |
| losses (with > without) | 0 |
| ties (skipped packs) | 6 |
| median tokens without rco | 153,492 |
| median tokens with rco | 23,675 |
| saving (median with against median without) | 84.6% |
| saving per commit, median | 79.9% (smallest 0.0%, a tie) |
| median files in the import hop | 28 |
| commits where importers were added | 8 (135 changed-name call sites in importers, found in 6 of those commits; the pack does not carry call-site lines yet, card 1360) |

Edges: `using N;` (also `using static`, an alias, and `global using`) and the
file's own namespace with its parents map to the files that declare a type
the importer names in that namespace. A C# file needs no `using` for its own
namespace, so these edges are most of the hop in a library like this one
(almost every file sits in a few namespaces). A `.csproj` `ProjectReference`
(followed transitively) limits which projects a file can see; a partial type
links all its files in the nearest project.

### How much of the saving comes from own-namespace edges

To show the result does not depend on those edges, the same replay with the
file's own namespace and parents ignored (only `using` directives and
`global using` count):

| edges | median without | median with | saving | losses | median import hop |
|---|---|---|---|---|---|
| usings and own namespace (shipped) | 153,492 | 23,675 | 84.6% | 0 | 28 |
| usings only | 87,996 | 17,947 | 79.6% | 0 | 11 |

## Limits

- The pack is aimed at exactly the files the commit changed, as for every
  replay; this is an estimate, not an agent measurement.
- Newtonsoft.Json is one library with a long, quiet history in few projects:
  37 commits is a small sample. A solution of many projects would show more
  from the `.csproj` limit.
- Public means `public`, `protected` or `protected internal`; internal types
  and members are not in the Tree (they still count as import edges).
- Types are found by name: a file that names a type in a visible namespace
  is linked even if the name is a local variable or a member of another
  type. The edge can be too wide, not too narrow, as for Java.
- One shallow clone. The pinned head makes the run repeatable: check out
  `52fa3aef1f2c`, then rerun this replay with `--since 2024-01-01`.
