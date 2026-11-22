"""This repo's CI already had drift_report and notice_report.

Card 1228 adds the missing PR diff/affected report and the push
update to the same workflow, with no second rco.yml and nothing in
pre-commit. Jobs stay report-only: exit 1 (findings) must not fail
the job; exit 2 or 3 must. They are skipped in the merge queue and
kept out of ci/test-select.toml so they are not product-gated.
This repository runs the tree under test, not a published wheel.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CI = ROOT / ".github" / "workflows" / "ci.yml"
SETUP = ROOT / "docs" / "github-setup.md"
HOOK = ROOT / ".githooks" / "pre-commit"
SELECT = ROOT / "ci" / "test-select.toml"


def _jobs(text: str) -> dict[str, str]:
    """Return top-level job blocks from the workflow jobs mapping."""
    lines = text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line == "jobs:")
    except StopIteration:
        return {}
    found: dict[str, str] = {}
    name = None
    buf: list[str] = []
    for line in lines[start + 1:]:
        m = re.match(r"^  ([A-Za-z0-9_]+):\s*$", line)
        if m:
            if name is not None:
                found[name] = "\n".join(buf) + "\n"
            name = m.group(1)
            buf = [line]
            continue
        if name is not None:
            buf.append(line)
    if name is not None:
        found[name] = "\n".join(buf) + "\n"
    return found


def test_no_second_rco_workflow():
    assert not (ROOT / ".github" / "workflows" / "rco.yml").exists()


def test_precommit_does_not_run_rco():
    text = HOOK.read_text(encoding="utf-8")
    assert "rco" not in text.lower()


def test_new_jobs_are_kept_out_of_the_select_map():
    sys.path.insert(0, str(ROOT))
    from ci.test_select import load_map

    _rules, jobs = load_map(SELECT)
    for name in ("diff_affected_report", "update_store", "drift_report", "notice_report"):
        assert name not in jobs


def test_diff_affected_report_is_advisory_and_skipped_in_the_queue():
    text = CI.read_text(encoding="utf-8")
    jobs = _jobs(text)
    assert "diff_affected_report" in jobs
    body = jobs["diff_affected_report"]
    assert "github.event_name == 'pull_request'" in body
    assert "fetch-depth: 0" in body
    assert "PYTHONPATH=src python -m rco" in body
    assert " diff " in body or ' diff "' in body or "diff \"$base\"" in body
    assert " affected " in body or "affected \"$base\"" in body
    assert "GITHUB_STEP_SUMMARY" in body
    assert "code" in body and "-ge 2" in body
    assert "persist-credentials: false" in body


def test_update_store_runs_on_push_and_caches_the_store():
    text = CI.read_text(encoding="utf-8")
    jobs = _jobs(text)
    assert "update_store" in jobs
    body = jobs["update_store"]
    assert "github.event_name == 'push'" in body
    assert "fetch-depth: 0" in body
    assert "PYTHONPATH=src python -m rco update --name" in body
    assert "actions/cache@0057852bfaa89a56745cba8c7296529d2fc39830" in body
    assert "path: .rco" in body
    assert "persist-credentials: false" in body
    assert "merge_group" not in body or "push" in body


def test_github_setup_tells_adopters_not_to_copy_the_self_jobs():
    text = SETUP.read_text(encoding="utf-8")
    for name in ("drift_report", "notice_report", "diff_affected_report", "update_store"):
        assert name in text
