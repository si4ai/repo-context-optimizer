"""ci/notice_heads.py: other open PR heads, named as pr-N, never a desk."""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ci.notice_heads import PrHead, fetch_spec, load_pulls, other_heads  # noqa: E402


def _pr(number, sha, ref="card/x", state="open", user="some-bot[bot]"):
    return {
        "number": number,
        "state": state,
        "user": {"login": user},
        "head": {"sha": sha, "ref": ref, "user": {"login": user}},
    }


def test_excludes_this_pr_and_closed_prs_and_sorts():
    prs = [
        _pr(34, "a" * 40, ref="card/b"),
        _pr(9, "b" * 40, ref="card/a"),
        _pr(18, "c" * 40, state="closed"),
        _pr(34, "a" * 40, ref="card/b"),  # duplicate page
    ]
    heads = other_heads(prs, exclude=34)
    assert [h.label for h in heads] == ["pr-9"]
    assert heads[0].sha == "b" * 40
    assert heads[0].number == 9


def test_labels_are_pr_numbers_never_logins_or_branch_owners():
    heads = other_heads([_pr(7, "d" * 40, user="si4ai-builder[bot]")])
    assert heads[0].label == "pr-7"
    blob = " ".join(f"{x.label} {x.sha} {x.ref} {x.number}" for x in heads)
    assert "si4ai-builder" not in blob
    assert "bot]" not in blob
    assert heads[0].ref == "card/x"  # branch name is kept, but the notice label is pr-N
    assert fetch_spec(heads[0]) == "pull/7/head:refs/heads/pr-7"


def test_push_with_no_exclude_keeps_every_open_pr():
    heads = other_heads([_pr(9, "b" * 40), _pr(34, "a" * 40)], exclude=None)
    assert [h.label for h in heads] == ["pr-9", "pr-34"]


def test_empty_or_no_others_is_empty():
    assert other_heads([], exclude=12) == ()
    assert other_heads([_pr(12, "e" * 40)], exclude=12) == ()


def test_missing_sha_is_an_error():
    with pytest.raises(ValueError, match="has no head sha"):
        other_heads([{"number": 1, "state": "open", "head": {"ref": "x"}}])


def test_load_pulls_accepts_paged_arrays_and_empty():
    a = [_pr(1, "a" * 40)]
    b = [_pr(2, "b" * 40)]
    text = json.dumps(a) + "\n" + json.dumps(b)
    got = load_pulls(text)
    assert [p["number"] for p in got] == [1, 2]
    assert load_pulls("") == []
    assert load_pulls("[]") == []


def test_cli_prints_stable_lines(tmp_path, capsys):
    from ci.notice_heads import main

    path = tmp_path / "pulls.json"
    path.write_text(json.dumps([_pr(34, "a" * 40), _pr(9, "b" * 40)]), encoding="utf-8")
    assert main([str(path), "--exclude", "34"]) == 0
    assert capsys.readouterr().out == f"pr-9 {'b' * 40}\n"
    assert main([str(path)]) == 0
    assert capsys.readouterr().out == f"pr-9 {'b' * 40}\npr-34 {'a' * 40}\n"
