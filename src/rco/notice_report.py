"""The cross-PR contract notice for a CI job: `python -m rco.notice_report`.

Reads the event from the environment, as a GitHub Actions step sets it:

- EVENT_NAME: `pull_request` or `push`.
- pull_request: PR_BASE and PR_HEAD (SHAs), PR_BASE_REF (base branch),
  PR_NUMBER (this PR, left out of the list).
- push: PUSH_BEFORE and PUSH_SHA, PUSH_REF_NAME (the pushed branch).
- GITHUB_REPOSITORY, and GH_TOKEN for `gh api` (the job needs
  `pull-requests: read`). GITHUB_STEP_SUMMARY, when set, gets the report.

On a pull request, BASE is the merge-base of the base tip and the head,
so later base commits are never counted as this PR's changes. It lists
the other open PRs into the same base branch with `gh api`, fetches each
head as the local ref pr-N, checks the fetched SHA, and runs `rco notice`
with the same interpreter. A run with no usable base or no other open PR
writes a short note and exits 0. A failed listing or fetch exits 3, so a
dead lister never looks like a quiet repo. Otherwise the exit code is
`rco notice`'s, which is always 0: a visible hint, never a block.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from typing import Sequence

from rco.notice_heads import PrHead, fetch_spec, load_pulls, other_heads

ZEROS = "0" * 40
NO_BASE = "no usable base for this event: no cross-PR contract notice."
QUIET = "no other open PRs: no cross-PR contract notice."


class StepError(Exception):
    """A step that must fail the job (exit 3), not pass as a quiet repo."""


def _summary(text: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(text)


def _note(line: str) -> int:
    print(line)
    _summary(line + "\n")
    return 0


def _git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p.stdout.strip()


def _open_pulls(repo: str) -> str:
    gh = shutil.which("gh")
    if gh is None:
        raise StepError("gh not found on PATH: cannot list open PRs")
    p = subprocess.run([gh, "api", "--paginate", f"repos/{repo}/pulls?state=open&per_page=100"],
                       capture_output=True, text=True)
    if p.returncode != 0:
        raise StepError(f"gh api failed: {p.stderr.strip()}")
    return p.stdout


def _fetch(head: PrHead) -> None:
    subprocess.run(["git", "fetch", "--no-tags", "origin", fetch_spec(head)], check=False)
    p = subprocess.run(["git", "rev-parse", "--verify", f"{head.label}^{{commit}}"],
                       capture_output=True, text=True)
    got = p.stdout.strip()
    if got != head.sha:
        raise StepError(f"fetched {head.label} is {got or 'missing'}, expected {head.sha}")


def run(env: dict[str, str]) -> int:
    pr = env.get("EVENT_NAME") == "pull_request"
    if pr:
        tip, head, base_ref = env.get("PR_BASE", ""), env.get("PR_HEAD", ""), env.get("PR_BASE_REF", "")
        exclude = int(env["PR_NUMBER"]) if env.get("PR_NUMBER") else None
    else:
        tip, head, base_ref = env.get("PUSH_BEFORE", ""), env.get("PUSH_SHA", ""), env.get("PUSH_REF_NAME", "")
        exclude = None
    if not tip or tip == ZEROS:
        return _note(NO_BASE)
    # the PR's own changes are merge-base(base tip, head) -> head.
    base = _git("merge-base", tip, head) if pr else tip
    print(f"notice base: {base}")
    try:
        heads = other_heads(load_pulls(_open_pulls(env.get("GITHUB_REPOSITORY", ""))), exclude,
                            base_ref or None)
    except ValueError as e:
        raise StepError(str(e)) from e
    if not heads:
        return _note(QUIET)
    for h in heads:
        _fetch(h)
    # notice names the local refs pr-N, never a login or a desk
    p = subprocess.run([sys.executable, "-m", "rco", "notice", "--base", base, "--head", head,
                        *[h.label for h in heads]], capture_output=True, text=True)
    sys.stderr.write(p.stderr)
    print(p.stdout, end="")
    _summary(p.stdout)
    return p.returncode


def main(argv: Sequence[str] | None = None) -> int:
    argparse.ArgumentParser(
        prog="python -m rco.notice_report",
        description="cross-PR contract notice for a CI job; reads the event from the environment "
                    "(see the module docstring)").parse_args(argv)
    try:
        return run(dict(os.environ))
    except StepError as e:
        print(f"notice_report: {e}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
