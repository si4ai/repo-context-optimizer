"""`rco init`: one-step setup for a repo (config, CI report job, local hook).

Writes, only where nothing is there yet:

- `rco.toml`: every setting, commented out, so it changes nothing until edited.
- `.github/workflows/rco.yml`: on a pull request, a job summary with
  `rco diff`, `rco affected` and (when pins/ exists) `rco drift`; on a push
  to the default branch, `rco update` with the store cached across runs.
  It fails only on exit 2 or 3, never on findings.
- `--hooks`: a post-merge hook that runs `rco update --name <branch>` in
  the background after every pull or merge, and always exits 0.

An existing file is never overwritten; the report says it was kept. A hook
file is rco's only if it carries HOOK_MARK, and `--remove-hooks` deletes
only such a file. The output is the same bytes for the same repo and rco
version.
"""
from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rco import __version__, gitread
from rco.config import worktree_top
from rco.output import Exit, UsageError

SCHEMA_INIT = "rco.init/1"
HOOK_MARK = "# rco: post-merge hook (rco init --hooks)"
WORKFLOW = ".github/workflows/rco.yml"
CONFIG = "rco.toml"

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


def workflow_text(default_branch: str) -> str:
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
          PR_BASE: ${{{{ github.event.pull_request.base.sha }}}}
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
          exit "$worst"
      - name: Update the store
        if: github.event_name == 'push'
        run: rco update --name "${{{{ github.ref_name }}}}"
"""


@dataclass(frozen=True, slots=True)
class Step:
    path: str
    action: str  # "wrote", "kept", "removed", "absent"
    detail: str = ""


@dataclass(frozen=True)
class InitReport:
    steps: tuple[Step, ...]

    def to_obj(self) -> dict:
        return {"schema": SCHEMA_INIT,
                "steps": [{"path": s.path, "action": s.action, "detail": s.detail}
                          for s in self.steps]}

    def to_markdown(self) -> str:
        out = ["# rco init", ""]
        out += [f"- {s.action} `{s.path}`" + (f": {s.detail}" if s.detail else "")
                for s in self.steps]
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
         remove_hooks: bool = False) -> InitReport:
    top = worktree_top(repo)
    steps: list[Step] = []
    if config:
        steps.append(_write_new(top, CONFIG, CONFIG_TEXT))
    if ci:
        try:
            branch = gitread.default_branch(top).removeprefix("origin/")
        except UsageError:
            branch = "main"
        steps.append(_write_new(top, WORKFLOW, workflow_text(branch)))
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
    return InitReport(tuple(steps))
