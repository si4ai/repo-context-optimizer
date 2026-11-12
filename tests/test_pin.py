"""Phase 4 pinned contracts: rco pin writes a reviewed contract, rco drift reports drift from it."""
import json

import pytest

from rco import canon, cli, fixture, pin
from rco.output import Exit, UsageError

BASE = {
    "pkg/__init__.py": '"""Pkg."""\n',
    "pkg/core.py": (
        "LIMIT = 10\n\n\n"
        "def run(argv: list[str]) -> int:\n"
        '    """Run the tool."""\n'
        "    return 0\n\n\n"
        "def old() -> None:\n"
        "    pass\n"
    ),
    "pkg/other.py": "def helper() -> int:\n    return 1\n",
    "README.md": "# r\n",
}


def _repo(tmp_path):
    return fixture.make_repo(tmp_path / "r", BASE)


def _pin_file(tmp_path, repo, sha, paths=("pkg/core.py",)):
    f = tmp_path / "core.pin.json"
    f.write_bytes(canon.canonical_json(pin.make_pin(repo, sha, list(paths)).to_obj()) + b"\n")
    return f


def _ops(rep):
    return [(c.op, c.key) for p in rep.pins for c in p.changes]


def test_pin_holds_only_the_pinned_files(tmp_path):
    repo, a = _repo(tmp_path)
    o = pin.make_pin(repo, a, ["pkg/core.py"]).to_obj()
    assert o["schema"] == "rco.pin/1" and o["sha"] == a and o["paths"] == ["pkg/core.py"]
    assert {n["path"] for n in o["nodes"]} == {"pkg/core.py"}  # not pkg/other.py, same module
    assert "module_hash" not in json.dumps(o)


def test_pin_round_trip(tmp_path):
    repo, a = _repo(tmp_path)
    p = pin.make_pin(repo, a, ["pkg/core.py", "pkg/other.py"])
    back = pin.pin_from_obj(json.loads(canon.canonical_json(p.to_obj())))
    assert back == p


def test_pin_rejects_unknown_schema_and_keys():
    with pytest.raises(ValueError):
        pin.pin_from_obj({"schema": "rco.map/1", "sha": "x", "paths": [], "nodes": []})
    with pytest.raises(ValueError):
        pin.pin_from_obj({"schema": "rco.pin/1", "sha": "x", "paths": [], "nodes": [], "extra": 1})


def test_pin_path_must_be_a_file_at_sha(tmp_path):
    repo, a = _repo(tmp_path)
    with pytest.raises(UsageError):
        pin.make_pin(repo, a, ["pkg/nope.py"])
    with pytest.raises(UsageError):
        pin.make_pin(repo, a, [])


def test_clean_when_unchanged_or_internal_edit(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    body = BASE["pkg/core.py"].replace("    return 0\n", "    x = 1\n    return x\n")
    b = fixture.commit(repo, {"pkg/core.py": body, "pkg/other.py": "def helper(x: int) -> int:\n    return x\n"})
    rep = pin.drift(repo, [f], b)
    assert _ops(rep) == [] and rep.exit_code() is Exit.OK  # other.py is not pinned
    assert "no drift" in rep.to_markdown()


def test_drift_ops(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    head = (BASE["pkg/core.py"]
            .replace("(argv: list[str])", "(argv: list[str], *, verbose: bool = False)")
            .replace("Run the tool", "Run it")
            .replace("def old() -> None:\n    pass\n", "def new() -> None:\n    pass\n"))
    b = fixture.commit(repo, {"pkg/core.py": head})
    rep = pin.drift(repo, [f], b)
    assert _ops(rep) == [
        ("added", "function:pkg/core.py:new"),
        ("removed", "function:pkg/core.py:old"),
        ("changed", "function:pkg/core.py:run"),
    ]
    assert rep.exit_code() is Exit.FINDINGS


def test_doc_only_drift_exits_0(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    b = fixture.commit(repo, {"pkg/core.py": BASE["pkg/core.py"].replace("Run the tool", "Run it")})
    rep = pin.drift(repo, [f], b)
    assert [c.op for p in rep.pins for c in p.changes] == ["doc"]
    assert rep.exit_code() is Exit.OK


def test_removed_pinned_file_drifts(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    fixture.git(repo, "rm", "-q", "pkg/core.py")
    b = fixture.commit(repo, {})
    ops = _ops(pin.drift(repo, [f], b))
    assert ("removed", "function:pkg/core.py:run") in ops
    assert all(op == "removed" for op, _ in ops)


def test_markdown_names_pin_and_repin_hint(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    b = fixture.commit(repo, {"pkg/core.py": BASE["pkg/core.py"].replace("def old", "def gone")})
    md = pin.drift(repo, [f], b).to_markdown()
    assert md.startswith(f"# rco drift  (sha={b[:12]}, pins=1)\n")
    assert str(f) in md and "REMOVED function `old`" in md
    assert "rco pin" in md  # how to re-pin when the change is intended


def test_deterministic(tmp_path):
    repo, a = _repo(tmp_path)
    f = _pin_file(tmp_path, repo, a)
    b = fixture.commit(repo, {"pkg/core.py": BASE["pkg/core.py"].replace("def old", "def gone")})
    r1, r2 = pin.drift(repo, [f], b), pin.drift(repo, [f], b)
    assert canon.canonical_json(r1.to_obj()) == canon.canonical_json(r2.to_obj())
    assert r1.to_markdown() == r2.to_markdown()
    assert r1.to_obj()["schema"] == "rco.drift/1"


def test_cli_pin_then_drift(tmp_path, monkeypatch, capsys):
    repo, a = _repo(tmp_path)
    out = tmp_path / "p.json"
    assert cli.main(["pin", "--repo", str(repo), "--for", "pkg/core.py",
                     "--format", "json", "--out", str(out)]) == 0
    assert json.loads(out.read_text())["schema"] == "rco.pin/1"
    assert cli.main(["drift", str(out), "--repo", str(repo)]) == 0
    fixture.commit(repo, {"pkg/core.py": BASE["pkg/core.py"].replace("def old", "def gone")})
    assert cli.main(["drift", str(out), "--repo", str(repo)]) == 1
    assert cli.main(["drift", str(out), "--repo", str(repo), "--at", a]) == 0


def test_cli_drift_directory_and_usage(tmp_path):
    repo, a = _repo(tmp_path)
    d = tmp_path / "pins"
    d.mkdir()
    assert cli.main(["pin", "--repo", str(repo), "--for", "pkg/core.py",
                     "--format", "json", "--out", str(d / "core.json")]) == 0
    assert cli.main(["drift", str(d), "--repo", str(repo)]) == 0
    assert cli.main(["pin", "--repo", str(repo)]) == 2                      # --for required
    assert cli.main(["drift", str(tmp_path / "missing.json"), "--repo", str(repo)]) == 2
    (tmp_path / "empty").mkdir()
    assert cli.main(["drift", str(tmp_path / "empty"), "--repo", str(repo)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "rco.map/1"}')
    assert cli.main(["drift", str(bad), "--repo", str(repo)]) == 2
