"""`rco init`: one-step setup for a repo (config, CI report job, local hook).

Writes, only where nothing is there yet:

- `rco.toml`: every setting, commented out, so it changes nothing until edited.
- `.github/workflows/rco.yml`: on a pull request, a job summary with
  `rco diff`, `rco affected`, (when pins/ exists) `rco drift`, and the
  cross-PR notice (`python -m rco.notice_report`, which ships in the
  wheel); on a push to the default branch, `rco update` with the store
  cached across runs. It fails only on exit 2 or 3, never on findings.
  With `--metrics`, the pull-request job also packs the changed files,
  writes rco-metrics.json, and uploads the rco-report artifact.
- `--hooks`: a post-merge hook that runs `rco update --name <branch>` in
  the background after every pull or merge, and always exits 0.
- `--agents`: one marked block (AGENT_BEGIN..AGENT_END) at the top of each
  agent instruction file, telling an agent to run `rco pack --for` before it
  reads code. AGENTS.md is created if missing; CLAUDE.md, GEMINI.md and
  .github/copilot-instructions.md get the block only if they exist. This is
  the one place init edits a file that exists: only between the markers,
  plus one blank line after the block.

Outside those marked blocks, an existing file is never overwritten; the
report says it was kept. A hook
file is rco's only if it carries HOOK_MARK, and `--remove-hooks` deletes
only such a file. The output is the same bytes for the same repo and rco
version.
"""
from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rco import __version__, gitread
from rco.canon import canonical_json
from rco.config import worktree_top
from rco.forest_model import estimate_tokens, token_method
from rco.output import Exit, UsageError, write_atomic

SCHEMA_INIT = "rco.init/1"
HOOK_MARK = "# rco: post-merge hook (rco init --hooks)"
WORKFLOW = ".github/workflows/rco.yml"
CONFIG = "rco.toml"

AGENT_BEGIN = "<!-- rco:begin (rco init --agents) -->"
AGENT_END = "<!-- rco:end -->"
# No version in the block, so it stays byte-stable across rco releases.
AGENT_BLOCK = """\
## rco: read the pack before the code
- Run `rco update` once per checkout.
- Before you read code, run `rco pack --for FILE...` for the files you will touch.
- Read the pack first; open a raw file only when the pack does not answer.
- If the pack's second line does not say `current`, run `rco update` and pack again; read any file on its `changed since this pack` line directly.
- After a compaction, rerun `rco pack --for FILE...` instead of rereading the files.
"""
AGENTS_FILE = "AGENTS.md"
CLAUDE_IMPORT = "@AGENTS.md\n"  # an agent that reads CLAUDE.md skips AGENTS.md
AGENT_TARGETS = ((AGENTS_FILE, AGENT_BLOCK, True), ("CLAUDE.md", CLAUDE_IMPORT, False),
                 ("GEMINI.md", AGENT_BLOCK, False),
                 (".github/copilot-instructions.md", AGENT_BLOCK, False))
AIDER_CONF = ".aider.conf.yml"
CURSOR_ENV = ".cursor/environment.json"
SIZE_WARN = 16384  # devin injects the first 16 KiB; codex caps at 32 KiB

CONFIG_TEXT = """\
# rco settings (README "Storage backends" and "Module"). Everything is
# commented out, so the defaults apply until you edit a line.

# backend = "sqlite"            # or an installed rco.backends entry point
# [store]
# path = ".rco/rco.db"          # sqlite, relative to the working tree top

# [affected]
# ignore = ["docs/**"]          # changed paths no test reads

# Modules (read from git at each commit). The first matching rule wins;
# manifests (package.json, Cargo.toml) and directories cover the rest.
# [[module]]
# id = "frontend"
# paths = ["web/**"]
"""

HOOK_TEXT = f"""\
#!/bin/sh
{HOOK_MARK}
# Keeps the local rco store current after a pull or merge. Never blocks git.
command -v rco >/dev/null 2>&1 || exit 0
branch=$(git symbolic-ref --short -q HEAD) || exit 0
(rco update --name "$branch" >/dev/null 2>&1 &)
exit 0
"""

_CHECKOUT = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262 # v4.4.0"
_PYTHON = "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065 # v5.6.0"
_CACHE = "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830 # v4.3.0"
_UPLOAD = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9 # v7.0.2"


def _metrics_steps() -> str:
    return f"""\
      - name: Token metrics
        if: ${{{{ !cancelled() && github.event_name == 'pull_request' }}}}
        env:
          PR_NUMBER: ${{{{ github.event.pull_request.number }}}}
          PR_BASE: ${{{{ github.event.pull_request.base.sha }}}}
          PR_HEAD: ${{{{ github.event.pull_request.head.sha }}}}
        run: |
          set -u
          mkdir -p rco-report
          base=$(git merge-base "$PR_BASE" "$PR_HEAD")
          git diff --name-only -z --no-renames "$base" "$PR_HEAD" --diff-filter=d > rco-report/changed.z
          if [ -s rco-report/changed.z ]; then
            # Exit 1 is findings (stale prose), not an error; xargs would fold it
            # into 123. An over-budget pack exits 0; rco metrics measure reports it.
            mapfile -d '' changed < rco-report/changed.z
            code=0
            rco pack "$PR_HEAD" --out rco-report/pack.md --for "${{changed[@]}}" || code=$?
            if [ "$code" -gt 1 ]; then exit "$code"; fi
          else
            : > rco-report/pack.md
          fi
          rco metrics measure --base "$base" --head "$PR_HEAD" --pr "$PR_NUMBER" --pack rco-report/pack.md --out rco-report/rco-metrics.json --format json
          rco metrics check rco-report/rco-metrics.json
      - uses: {_UPLOAD}
        if: ${{{{ !cancelled() && github.event_name == 'pull_request' }}}}
        with:
          name: rco-report
          path: rco-report/
          if-no-files-found: error
          retention-days: 14
"""


def workflow_text(default_branch: str, metrics: bool = False) -> str:
    metrics_steps = _metrics_steps() if metrics else ""
    return f"""\
# Written by rco init (rco {__version__}). Advisory: it fails only on
# exit 2 or 3 (usage or internal error), never on contract findings.
name: rco

"on":
  pull_request:
  push:
    branches:
      - {default_branch}

permissions:
  contents: read
  pull-requests: read

jobs:
  rco:
    runs-on: ubuntu-latest
    timeout-minutes: 15
    steps:
      - uses: {_CHECKOUT}
        with:
          fetch-depth: 0
          persist-credentials: false
      - uses: {_PYTHON}
        with:
          python-version: "3.12"
      - uses: {_CACHE}
        with:
          path: .rco
          key: rco-${{{{ github.ref_name }}}}-${{{{ github.sha }}}}
          restore-keys: |
            rco-${{{{ github.ref_name }}}}-
            rco-{default_branch}-
      - name: Install rco
        run: pip install "repo-context-optimizer=={__version__}"
      - name: Contract report
        if: github.event_name == 'pull_request'
        env:
          GH_TOKEN: ${{{{ github.token }}}}
          EVENT_NAME: ${{{{ github.event_name }}}}
          PR_NUMBER: ${{{{ github.event.pull_request.number }}}}
          PR_BASE: ${{{{ github.event.pull_request.base.sha }}}}
          PR_BASE_REF: ${{{{ github.event.pull_request.base.ref }}}}
          PR_HEAD: ${{{{ github.event.pull_request.head.sha }}}}
        run: |
          set -u
          base=$(git merge-base "$PR_BASE" "$PR_HEAD")
          worst=0
          run() {{
            code=0
            "$@" >> "$GITHUB_STEP_SUMMARY" || code=$?
            if [ "$code" -ge 2 ]; then worst=$code; fi
          }}
          run rco diff "$base" "$PR_HEAD"
          run rco affected "$base" "$PR_HEAD"
          if [ -d pins ]; then run rco drift pins --at "$PR_HEAD"; fi
          # cross-PR notice: other open PRs into the same base that use a
          # contract this PR changes. It writes its own summary section.
          code=0
          python -m rco.notice_report || code=$?
          if [ "$code" -ge 2 ]; then worst=$code; fi
          exit "$worst"
{metrics_steps}      - name: Update the store
        if: github.event_name == 'push'
        run: rco update --name "${{{{ github.ref_name }}}}"
"""


@dataclass(frozen=True, slots=True)
class Step:
    path: str
    action: str  # "wrote", "kept", "removed", "absent", "added", "updated"
    detail: str = ""


@dataclass(frozen=True)
class InitReport:
    steps: tuple[Step, ...]
    agents_tokens: int | None = None
    agents_tokenizer: str | None = None

    def to_obj(self) -> dict:
        obj: dict = {"schema": SCHEMA_INIT,
                     "steps": [{"path": s.path, "action": s.action, "detail": s.detail}
                               for s in self.steps]}
        if self.agents_tokens is not None:
            obj["agents_tokens"] = self.agents_tokens
            obj["agents_tokenizer"] = self.agents_tokenizer
        return obj

    def to_markdown(self) -> str:
        out = ["# rco init", ""]
        out += [f"- {s.action} `{s.path}`" + (f": {s.detail}" if s.detail else "")
                for s in self.steps]
        if self.agents_tokens is not None:
            out += ["", f"agents block: {self.agents_tokens} tokens ({self.agents_tokenizer})"]
        return "\n".join(out) + "\n"

    def exit_code(self) -> Exit:
        return Exit.OK


def _write_new(top: Path, rel: str, text: str, mode: int | None = None) -> Step:
    p = top / rel
    if p.exists():
        return Step(rel, "kept", "exists; not overwritten")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8", newline="\n")
    if mode is not None:
        os.chmod(p, mode)
    return Step(rel, "wrote")


def agent_block(body: str, nl: str = "\n") -> str:
    """The marked block for one file, with that file's line ending."""
    return nl.join([AGENT_BEGIN, *body.splitlines(), AGENT_END]) + nl


def _frontmatter_end(text: str) -> int:
    """Index just past a leading --- frontmatter, or 0 when there is none."""
    first = text.split("\n", 1)[0].rstrip("\r")
    if first != "---" or "\n" not in text:
        return 0
    pos = text.index("\n") + 1
    while pos < len(text):
        nxt = text.find("\n", pos)
        end = len(text) if nxt < 0 else nxt + 1
        if text[pos:end].rstrip("\r\n") == "---":
            return end
        pos = end
    return 0  # unterminated: treat as plain text


def _upsert_block(top: Path, rel: str, body: str, create: bool,
                  seen: dict[Path, str] | None = None) -> Step:
    """Add the marked block at the top of rel, or bring an existing block up to date.

    A symlink is written through to its real file, and a real file reached twice in one run
    (CLAUDE.md linked to AGENTS.md) is edited once; seen maps real paths to the name that did it.
    """
    link = top / rel
    p = link.resolve()
    if seen is not None:
        if p in seen:
            return Step(rel, "kept", f"same file as {seen[p]}")
        seen[p] = rel
    if not p.exists():
        if not create:
            return Step(rel, "absent")
        p.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(p, agent_block(body).encode("utf-8"))
        return Step(rel, "wrote")
    mode = stat.S_IMODE(p.stat().st_mode)
    try:
        with open(p, encoding="utf-8", newline="") as f:
            text = f.read()
    except UnicodeDecodeError:
        return Step(rel, "kept", "not UTF-8; convert it to UTF-8 or add the block by hand")
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    text = text[len(bom):]
    nl = "\r\n" if "\r\n" in text else "\n"
    block = agent_block(body, nl)
    begin = text.find(AGENT_BEGIN)
    if begin >= 0:
        end = text.find(AGENT_END, begin)
        if end < 0:
            return Step(rel, "kept", "unterminated rco block")
        end += len(AGENT_END)
        if text.startswith(nl, end):
            end += len(nl)
        if text[begin:end] == block:
            return Step(rel, "kept", "current")
        new, action = text[:begin] + block + text[end:], "updated"
    else:
        at = _frontmatter_end(text)
        new, action = text[:at] + block + nl + text[at:], "added"
    write_atomic(p, (bom + new).encode("utf-8"), mode)
    return Step(rel, action)


def _size_warning(top: Path, step: Step) -> Step:
    p = top / step.path
    n = p.stat().st_size if p.is_file() else 0
    if n <= SIZE_WARN or step.detail.startswith("same file as"):
        return step
    warn = f"warning: {n} bytes; devin injects the first 16 KiB, codex caps at 32 KiB"
    return Step(step.path, step.action, f"{step.detail}; {warn}" if step.detail else warn)


def cursor_install_line() -> str:
    return f"pip install repo-context-optimizer=={__version__} && rco update"


def _agent_steps(top: Path) -> list[Step]:
    seen: dict[Path, str] = {}
    steps = [_size_warning(top, _upsert_block(top, rel, body, create, seen))
             for rel, body, create in AGENT_TARGETS]
    aider = top / AIDER_CONF
    if aider.is_file() and AGENTS_FILE not in aider.read_text(encoding="utf-8", errors="replace"):
        steps.append(Step(AIDER_CONF, "kept", f"add `read: [{AGENTS_FILE}]` by hand"))
    env = top / CURSOR_ENV
    if env.exists():
        steps.append(Step(CURSOR_ENV, "kept", f"exists; install line: {cursor_install_line()}"))
    elif env.parent.is_dir():
        write_atomic(env, canonical_json({"install": cursor_install_line()}) + b"\n")
        steps.append(Step(CURSOR_ENV, "wrote"))
    return steps


def hooks_dir(repo: Path, top: Path) -> Path:
    p = subprocess.run(["git", "-C", str(repo), "rev-parse", "--git-path", "hooks"],
                       capture_output=True)
    if p.returncode != 0:
        raise UsageError(f"not a git repository: {repo}")
    d = Path(p.stdout.decode().strip())
    return d if d.is_absolute() else (top / d)


def _rel(p: Path, top: Path) -> str:
    try:
        return p.resolve().relative_to(top.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def init(repo: Path, config: bool = True, ci: bool = True, hooks: bool = False,
         remove_hooks: bool = False, metrics: bool = False,
         agents: bool = False) -> InitReport:
    top = worktree_top(repo)
    steps: list[Step] = []
    if config:
        steps.append(_write_new(top, CONFIG, CONFIG_TEXT))
    if ci:
        try:
            branch = gitread.default_branch(top).removeprefix("origin/")
        except UsageError:
            branch = "main"
        steps.append(_write_new(top, WORKFLOW, workflow_text(branch, metrics=metrics)))
    hook = hooks_dir(repo, top) / "post-merge"
    if hooks:
        if hook.exists():
            mine = HOOK_MARK in hook.read_text(encoding="utf-8", errors="replace")
            steps.append(Step(_rel(hook, top), "kept", "already rco's" if mine else
                              "another post-merge hook exists; add `rco update` to it by hand"))
        else:
            hook.parent.mkdir(parents=True, exist_ok=True)
            hook.write_text(HOOK_TEXT, encoding="utf-8", newline="\n")
            os.chmod(hook, 0o755)
            steps.append(Step(_rel(hook, top), "wrote"))
    if remove_hooks:
        if not hook.exists():
            steps.append(Step(_rel(hook, top), "absent"))
        elif HOOK_MARK in hook.read_text(encoding="utf-8", errors="replace"):
            hook.unlink()
            steps.append(Step(_rel(hook, top), "removed"))
        else:
            steps.append(Step(_rel(hook, top), "kept", "not rco's; left alone"))
    if agents:
        steps += _agent_steps(top)
        return InitReport(tuple(steps),
                          estimate_tokens(agent_block(AGENT_BLOCK)),
                          token_method())
    return InitReport(tuple(steps))
