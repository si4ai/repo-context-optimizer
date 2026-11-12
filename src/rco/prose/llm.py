"""LLM prose hook (step 4). Off by default: no SDK, no keys, no network.

When enabled (rco.toml prose = "llm"), rco runs a user-named prose_cmd that
reads the Tree JSON on stdin and prints prose on stdout.
"""
from __future__ import annotations

from rco.model import Tree


def llm_prose(tree: Tree, prose_cmd: str) -> str:
    raise NotImplementedError("Phase 2 step 4: LLM prose hook")
