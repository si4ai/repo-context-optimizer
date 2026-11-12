"""Map checker: findings, sorting, the six planted drifts, CLI exit codes."""
import json
from pathlib import Path
from dataclasses import replace

from rco import build, canon, check, cli, fixture
from rco.model import Kind, Node
from rco.output import Exit


def _map(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", fixture.DRIFT_FILES)
    return repo, sha, build.build_map(repo, sha)


def test_clean_map_has_no_findings(tmp_path):
    repo, sha, m = _map(tmp_path)
    rep = check.check(repo, sha, m)
    assert rep.findings == () and rep.exit_code() is Exit.OK


def test_selftest_catches_all_six(tmp_path):
    _, _, m = _map(tmp_path)
    rep = check.selftest_on(m)
    assert len(rep.rows) == 6
    assert all(caught for *_, caught in rep.rows), rep.to_markdown()
    assert rep.exit_code() is Exit.OK


def test_selftest_fails_when_a_plant_is_missed(tmp_path, monkeypatch):
    _, _, m = _map(tmp_path)
    real = check.compare
    monkeypatch.setattr(check, "compare",
                        lambda a, b: tuple(f for f in real(a, b) if f.status != "stale"))
    rep = check.selftest_on(m)
    assert rep.exit_code() is Exit.FINDINGS
    assert "MISSED: alter a module_hash" in rep.to_markdown()


def test_findings_name_path_and_are_sorted(tmp_path):
    _, _, m = _map(tmp_path)
    t = m.trees[-1]
    extra = Node(Kind.FUNCTION, "ghost", "", "() -> None")
    pm = [n for n in t.nodes if n.kind is Kind.PYMODULE]
    assert pm, "fixture needs a python module in its last tree"
    bad = replace(m, sha="0" * 40, trees=m.trees[:-1] + (replace(t, nodes=tuple(
        replace(n, children=n.children + (extra,)) if n is pm[0] else n for n in t.nodes)),))
    found = check.compare(bad, m)
    assert [f.status for f in found].count("extra") == 1
    assert found == tuple(sorted(found, key=lambda f: (f.path, f.key, f.status)))
    assert all(f.path or f.key == "map:sha" for f in found)


def test_cli_check_exit_codes_and_byte_identical(tmp_path, capsysbinary):
    repo, sha, m = _map(tmp_path)
    mp = tmp_path / "map.json"
    assert cli.main(["tree", sha, "--repo", str(repo), "--format", "json", "--out", str(mp)]) == 0
    assert canon.map_from_obj(json.loads(mp.read_text())) == m
    assert cli.main(["check", str(mp), "--repo", str(repo), "--format", "json"]) == 0
    first = capsysbinary.readouterr().out
    assert cli.main(["check", str(mp), "--repo", str(repo), "--format", "json"]) == 0
    assert capsysbinary.readouterr().out == first
    newer = fixture.commit(repo, {"pkg/core.py": "def run(argv: list[str], x: int) -> int:\n    return 0\n"})
    assert cli.main(["check", str(mp), newer, "--repo", str(repo)]) == Exit.FINDINGS


def test_cli_selftest_and_bad_map(tmp_path):
    assert cli.main(["check", "--selftest"]) == Exit.OK
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    assert cli.main(["check", str(bad)]) == Exit.USAGE
    assert cli.main(["tree", "no-such-rev", "--repo", str(tmp_path)]) == Exit.USAGE


# Recorded in docs/baseline/phase1-checker.md: the self-test measured about
# 0.015 s; the limit leaves room for a slow CI runner.
SELFTEST_LIMIT_S = 5.0


def test_selftest_time_limit():
    import time
    t0 = time.perf_counter()
    assert check.selftest().exit_code() is Exit.OK
    assert time.perf_counter() - t0 < SELFTEST_LIMIT_S


def test_check_reads_forest_edges(tmp_path, capsys):
    """rco check accepts rco.forest/1 and reports edge findings on that graph."""
    from rco.forest import build_forest
    from rco.forest_model import Forest, forest_to_obj

    root = Path(__file__).parent / "fixtures" / "forest_basic"
    files = {q.relative_to(root).as_posix(): q.read_bytes()
             for q in sorted(root.rglob("*")) if q.is_file()}
    repo, sha = fixture.make_repo(tmp_path / "forest_basic", files)
    actual = build_forest(repo, sha)
    planted = Forest(actual.sha, tuple(
        replace(e, depends_on=()) if e.id == "src/app/core" else e for e in actual.entries),
        actual.notes)
    claimed = tmp_path / "forest.json"
    claimed.write_bytes(canon.canonical_json(forest_to_obj(planted)))
    assert cli.main(["check", str(claimed), "--repo", str(repo), "--format", "json"]) == Exit.FINDINGS
    out = json.loads(capsys.readouterr().out)
    assert out["counts"]["edge"] >= 1
    assert any(f["status"] == "edge" and f["key"] == "edge:src/app/core->src/app/util"
               and f["detail"] == "in code, not in forest" for f in out["findings"])
    good = tmp_path / "good.json"
    good.write_bytes(canon.canonical_json(forest_to_obj(actual)))
    assert cli.main(["check", str(good), "--repo", str(repo)]) == Exit.OK

