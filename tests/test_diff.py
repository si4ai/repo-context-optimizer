"""rco diff: added/removed/changed/doc, callers as file:line, defaults, determinism."""
import json

import pytest

from rco import canon, cli, diff, fixture
from rco.output import Exit

BASE = {
    "pkg/__init__.py": '"""Pkg."""\n',
    "pkg/core.py": (
        "LIMIT = 10\n\n\n"
        "def run(argv: list[str]) -> int:\n"
        '    """Run the tool."""\n'
        "    return 0\n\n\n"
        "def old() -> None:\n"
        "    pass\n\n\n"
        "class Store:\n"
        "    name: str\n\n"
        "    def get(self, key: str) -> bytes | None:\n"
        '        """Get a value."""\n'
        "        return None\n"
    ),
    "pkg/use.py": (
        "from pkg.core import Store, run, old\n\n\n"
        "def main() -> int:\n"
        "    s = Store()\n"
        "    old()\n"
        "    return run([s.get('k')])\n"
    ),
    "README.md": "# r\n",
}

HEAD_CORE = (
    "LIMIT = 10\n\n\n"
    "def run(argv: list[str], *, verbose: bool = False) -> int:\n"
    '    """Run the tool."""\n'
    "    return 0\n\n\n"
    "def new() -> None:\n"
    "    pass\n\n\n"
    "class Store:\n"
    "    name: str\n\n"
    "    def get(self, key: str) -> bytes | None:\n"
    '        """Get one value."""\n'
    "        return None\n"
)


def _two(tmp_path, head_files):
    repo, a = fixture.make_repo(tmp_path / "r", BASE)
    b = fixture.commit(repo, head_files)
    return repo, a, b


def _ops(rep):
    return [(c.op, c.key) for c in rep.changes]


def test_added_removed_changed_doc(tmp_path):
    repo, a, b = _two(tmp_path, {"pkg/core.py": HEAD_CORE})
    rep = diff.diff(repo, a, b)
    assert _ops(rep) == [
        ("added", "function:pkg/core.py:new"),
        ("removed", "function:pkg/core.py:old"),
        ("changed", "function:pkg/core.py:run"),
        ("doc", "method:pkg/core.py:Store.get"),
    ]
    by = {c.key: c for c in rep.changes}
    run = by["function:pkg/core.py:run"]
    assert json.loads(run.before) == {"attrs": {}, "sig": "(argv: list[str]) -> int"}
    assert json.loads(run.after)["sig"] == "(argv: list[str], *, verbose: bool=False) -> int"
    assert by["function:pkg/core.py:new"].before == ""
    assert by["function:pkg/core.py:old"].after == ""
    doc = by["method:pkg/core.py:Store.get"]
    assert json.loads(doc.before) == {"doc": "Get a value."}
    assert json.loads(doc.after) == {"doc": "Get one value."}
    assert rep.exit_code() is Exit.FINDINGS


def test_callers_are_file_line_at_head(tmp_path):
    repo, a, b = _two(tmp_path, {"pkg/core.py": HEAD_CORE})
    by = {c.key: c for c in diff.diff(repo, a, b).changes}
    assert by["function:pkg/core.py:run"].callers == ("pkg/use.py:7",)
    assert by["function:pkg/core.py:old"].callers == ("pkg/use.py:6",)  # still called: dangling
    assert by["function:pkg/core.py:new"].callers == ()
    assert by["method:pkg/core.py:Store.get"].callers == ()             # doc changes carry none


def test_doc_only_exits_0(tmp_path):
    repo, a, b = _two(tmp_path, {"pkg/core.py": BASE["pkg/core.py"].replace("Run the tool", "Run it")})
    rep = diff.diff(repo, a, b)
    assert [c.op for c in rep.changes] == ["doc"] and rep.exit_code() is Exit.OK


def test_body_and_file_only_changes_are_clean(tmp_path):
    body = BASE["pkg/core.py"].replace("    return 0\n", "    x = 1\n    return x\n", 1)
    repo, a, b = _two(tmp_path, {"pkg/core.py": body, "README.md": "# changed\n", "NOTES.txt": "n\n"})
    rep = diff.diff(repo, a, b)
    assert rep.changes == () and rep.exit_code() is Exit.OK
    assert rep.to_markdown().endswith("no contract changes\n")


def test_same_sha_no_changes(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", BASE)
    assert diff.diff(repo, a, a).changes == ()


def test_removed_module_and_parse_error(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", BASE)
    fixture.git(repo, "rm", "-q", "pkg/use.py")
    b = fixture.commit(repo, {"pkg/core.py": "def broken(:\n"})
    ops = _ops(diff.diff(repo, a, b))
    assert ("removed", "pymodule:pkg/use.py:pkg.use") in ops
    assert ("removed", "function:pkg/use.py:main") in ops
    assert ("removed", "function:pkg/core.py:run") in ops   # unparseable file: members gone
    assert not any(k.startswith("file:") for _, k in ops)


def test_overloads_compare_as_sorted_list(tmp_path):
    src = ("from typing import overload\n\n\n@overload\ndef f(x: int) -> int: ...\n\n\n"
           "@overload\ndef f(x: str) -> str: ...\n")
    repo, a = fixture.make_repo(tmp_path / "r", {"m.py": src})
    same = fixture.commit(repo, {"m.py": src.replace("x: int", "Y").replace("Y", "x: int")})
    assert diff.diff(repo, a, same).changes == ()
    b = fixture.commit(repo, {"m.py": src + "\n\n@overload\ndef f(x: bytes) -> bytes: ...\n"})
    (c,) = diff.diff(repo, a, b).changes
    assert c.op == "changed" and c.key == "function:m.py:f"
    assert len(json.loads(c.before)) == 2 and len(json.loads(c.after)) == 3


def test_deterministic_bytes_and_sorted(tmp_path):
    repo, a, b = _two(tmp_path, {"pkg/core.py": HEAD_CORE})
    r1, r2 = diff.diff(repo, a, b), diff.diff(repo, a, b)
    assert canon.canonical_json(r1.to_obj()) == canon.canonical_json(r2.to_obj())
    assert r1.to_markdown() == r2.to_markdown()
    assert list(r1.changes) == sorted(r1.changes, key=lambda c: (c.path, c.key, c.op))
    assert r1.to_obj()["counts"] == {"added": 1, "removed": 1, "changed": 1, "doc": 1}


def test_markdown_shape(tmp_path):
    repo, a, b = _two(tmp_path, {"pkg/core.py": HEAD_CORE})
    md = diff.diff(repo, a, b).to_markdown()
    assert "3 contract change(s), 1 doc-only" in md
    assert "## pkg/core.py" in md
    assert "- CHANGED function `run`" in md
    assert "  - callers: pkg/use.py:7" in md
    assert md.endswith("\n")


def test_defaults_merge_base_of_main_and_head(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", BASE)
    fixture.git(repo, "checkout", "-q", "-b", "feature")
    b = fixture.commit(repo, {"pkg/core.py": HEAD_CORE})
    fixture.git(repo, "checkout", "-q", "main")
    fixture.commit(repo, {"pkg/other.py": "def later() -> None:\n    pass\n"})  # main moves on
    fixture.git(repo, "checkout", "-q", "feature")
    rep = diff.diff(repo, None, None)
    assert (rep.base, rep.head) == (a, b)
    assert "function:pkg/other.py:later" not in {c.key for c in rep.changes}


def test_cli_exit_codes(tmp_path, capsysbinary):
    repo, a, b = _two(tmp_path, {"pkg/core.py": HEAD_CORE})
    assert cli.main(["diff", a, b, "--repo", str(repo), "--format", "json"]) == Exit.FINDINGS
    cap = capsysbinary.readouterr()
    assert json.loads(cap.out)["schema"] == "rco.diff/1"
    assert b"rco diff:" in cap.err                    # timing on stderr, not stdout
    assert cli.main(["diff", a, a, "--repo", str(repo)]) == Exit.OK
    assert cli.main(["diff", "nope", b, "--repo", str(repo)]) == Exit.USAGE
    out = tmp_path / "d.json"
    assert cli.main(["diff", a, b, "--repo", str(repo), "--format", "json", "--out", str(out)]) == 1
    capsysbinary.readouterr()
    cli.main(["diff", a, b, "--repo", str(repo), "--format", "json"])
    assert out.read_bytes() == capsysbinary.readouterr().out
