"""The cloud-agent guide and its examples (card 1313) stay true to the code."""
from __future__ import annotations

import json
import re
from pathlib import Path

from rco import __version__, cli
from rco.canon import canonical_json
from rco.init import AGENT_BLOCK, agent_block, cursor_install_line

ROOT = Path(__file__).resolve().parent.parent
GUIDE = ROOT / "docs" / "guide" / "cloud-agents.md"
EXAMPLES = ROOT / "docs" / "guide" / "examples"
AB = ROOT / "docs" / "baseline" / "cloud-ab.md"
NO_RESULTS = "Cloud-agent results: none yet."


def test_guide_embeds_the_block():
    block = agent_block(AGENT_BLOCK)
    assert (EXAMPLES / "AGENTS.rco.md").read_text(encoding="utf-8") == block
    assert block in GUIDE.read_text(encoding="utf-8")


def test_block_size():
    assert len(agent_block(AGENT_BLOCK).encode()) < 1024


def test_example_json():
    raw = (EXAMPLES / "cursor-environment.json").read_bytes()
    assert raw == canonical_json({"install": cursor_install_line()}) + b"\n"
    assert json.loads(raw) == {
        "install": f"pip install repo-context-optimizer=={__version__} && rco update"}
    text = GUIDE.read_text(encoding="utf-8")
    assert raw.decode().strip() in text
    assert text.count(f"repo-context-optimizer=={__version__}") >= 2
    assert len(set(re.findall(r"repo-context-optimizer==([0-9][^\s\"`]*)", text))) == 1


def test_guide_rco_commands_exist():
    sub = next(a for a in cli.make_parser()._actions if hasattr(a, "choices") and a.choices)
    names = set(sub.choices)
    used = set(re.findall(r"(?:`|&& |^)rco ([a-z]+)", GUIDE.read_text(encoding="utf-8"), re.M))
    assert {"update", "pack"} <= used
    assert used <= names, used - names
    assert "agents" in sub.choices["init"].format_help()


def test_unmeasured_until_measured():
    guide = GUIDE.read_text(encoding="utf-8")
    ab = AB.read_text(encoding="utf-8")
    measured = NO_RESULTS not in ab
    assert ("Measured benefit: unmeasured" in guide) != measured
    rows = [ln for ln in ab.splitlines() if ln.startswith("| ") and "not measured" not in ln
            and not ln.startswith(("| repo", "| ---", "|---"))]
    assert measured == bool(rows)
