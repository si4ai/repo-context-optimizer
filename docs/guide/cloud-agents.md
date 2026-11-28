# Cloud agents and rco

A cloud agent starts from a fresh checkout every time: no `.rco/` store, no
installed tools, and only the files you committed. This guide says what to
commit so that such an agent can run `rco pack --for FILE...` before it reads
code.

**Measured benefit: unmeasured.** Nothing here claims a saving. The method
for finding out is in [`docs/baseline/cloud-ab.md`](../baseline/cloud-ab.md).
The office's own desk numbers there are not a cloud-agent result.

## What to commit

| file | what it does |
|---|---|
| `AGENTS.md` | the instruction file most agents read; carries the rco block |
| `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md` | for agents that read their own file; each gets the block, or `@AGENTS.md` for `CLAUDE.md` |
| `.cursor/environment.json` | Cursor cloud agents: the setup command that installs rco and builds the store |

`rco init --agents` writes the first group and, when `.cursor/` exists,
the Cursor file. It edits only between its markers, is safe to rerun, and
keeps a symlinked `CLAUDE.md` or `AGENTS.md` as a link.

## The block

[`examples/AGENTS.rco.md`](examples/AGENTS.rco.md) is exactly what
`rco init --agents` writes. It is under 1 KiB and carries no version. When a
release changes it, rerun `rco init --agents` and the block is replaced
(`updated`).

```markdown
<!-- rco:begin (rco init --agents) -->
## rco: read the pack before the code
- Run `rco update` once per checkout.
- Before you read code, run `rco pack --for FILE...` for the files you will touch.
- Read the pack first; open a raw file only when the pack does not answer.
- If the pack's second line does not say `current`, run `rco update` and pack again; read any file on its `changed since this pack` line directly.
- After a compaction, rerun `rco pack --for FILE...` instead of rereading the files.
<!-- rco:end -->
```

Size caps matter: Codex reads at most 32 KiB of project instructions and
Devin injects the first 16 KiB. `rco init --agents` warns when a file is over
16 KiB; put the block first, and it is always inside both caps.

## Setup hook per platform

| platform | setup | instruction file |
|---|---|---|
| Cursor cloud agents | `install` in `.cursor/environment.json` (below) | `AGENTS.md` |
| Codex | the environment's setup script runs the install line | `AGENTS.md` |
| Devin | the machine setup runs the install line | `AGENTS.md` |
| Claude Code | a setup step in the session or a SessionStart hook | `CLAUDE.md` (imports `@AGENTS.md`) |
| Gemini CLI | the session's shell runs the install line | `GEMINI.md` |
| GitHub Copilot agent | a `copilot-setup-steps` workflow runs the install line | `.github/copilot-instructions.md` |
| Aider | add `read: [AGENTS.md]` to `.aider.conf.yml` by hand | `AGENTS.md` |

The install line is the same everywhere:

```sh
pip install repo-context-optimizer==0.1.8 && rco update
```

`rco update` builds the store at `.rco/rco.db`, which is local to the
checkout and never committed. rco adds `.rco/` to `.git/info/exclude`, so
nothing needs to go in `.gitignore`.

### Cursor

Cursor runs `install` once per Build, in the background, and it must be
idempotent; `pip install` and `rco update` both are. Commit
[`examples/cursor-environment.json`](examples/cursor-environment.json):

```json
{"install":"pip install repo-context-optimizer==0.1.8 && rco update"}
```

Cursor reads `.cursor/environment.json` only when no saved environment
overrides it, and not on self-hosted machines. The file takes `install`,
`start` and `terminals`; rco needs only `install`.

## Freshness

An agent that starts from a Build may hold a store older than the commit it
checked out. The pack's second line says whether it is `current`, how many
commits behind it is, and which files changed since; the block tells the
agent to run `rco update` and read those files directly.

## Not covered

This guide does not decide whether to use cloud agents, and it makes no
claim about token or time savings.
