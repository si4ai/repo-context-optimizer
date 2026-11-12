"""LLM prose hook (Phase 2 step 4). Off by default: no SDK, no keys, no network.

Nothing in rco calls this unless rco.toml says prose = "llm" and names a
prose_cmd (config reading lands with the integration step). The command
gets the canonical Tree JSON on stdin (never raw source) and prints prose on
stdout. rco caps and redacts what comes back.
"""
from __future__ import annotations

import shlex
import subprocess

from rco.canon import canonical_json, tree_to_obj
from rco.model import Tree
from rco.prose.mine import cap
from rco.redact import redact_text

TIMEOUT_S = 120


class ProseCommandError(Exception):
    pass


def llm_prose(tree: Tree, prose_cmd: str) -> str:
    argv = shlex.split(prose_cmd)
    if not argv:
        raise ProseCommandError("prose_cmd is empty")
    try:
        p = subprocess.run(argv, input=canonical_json(tree_to_obj(tree)), capture_output=True,
                           timeout=TIMEOUT_S)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise ProseCommandError(f"prose_cmd failed: {type(e).__name__}") from None
    if p.returncode != 0:
        raise ProseCommandError(f"prose_cmd exited {p.returncode}")
    return cap(redact_text(p.stdout.decode("utf-8", "replace")))
