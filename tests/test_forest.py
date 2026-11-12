"""Phase 2 step 2: the computed Forest is deterministic, mirrored and checked for edges."""
import os
from dataclasses import replace
from pathlib import Path

import pytest

from rco import build, canon, cli, fixture
from rco.forest import build_forest, compare_edges, compute_forest, layers, read_sources
from rco.forest_model import Forest, forest_from_obj, forest_to_obj
from rco.model import Map
from rco.output import Exit

HERE = Path(__file__).parent
CASES = ["forest_basic"]


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture
def repo(tmp_path):
    return fixture.make_repo(tmp_path / "forest_basic", _files(HERE / "fixtures" / "forest_basic"))


@pytest.mark.parametrize("case", CASES)
def test_golden(case, tmp_path):
    r, sha = fixture.make_repo(tmp_path / case, _files(HERE / "fixtures" / case))
    got = canon.canonical_json(forest_to_obj(build_forest(r, sha))) + b"\n"
    golden = HERE / "golden" / f"{case}.json"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()
    assert canon.canonical_json(forest_to_obj(build_forest(r, sha))) + b"\n" == got


def test_reordered_input_same_bytes(repo):
    r, sha = repo
    m = build.build_map(r, sha)
    src = read_sources(r, sha)
    from rco import imports, modules, gitread
    mods = modules.group(e.path for e in gitread.ls_tree(r, sha))
    edges = imports.module_edges(mods, src)
    a = compute_forest(m, edges, src)
    rev_map = Map(m.sha, tuple(reversed(m.trees)), m.notes)
    rev_edges = {k: tuple(reversed(v)) for k, v in reversed(list(edges.items()))}
    rev_src = dict(reversed(list(src.items())))
    b = compute_forest(rev_map, rev_edges, rev_src)
    assert canon.canonical_json(forest_to_obj(a)) == canon.canonical_json(forest_to_obj(b))
    assert forest_from_obj(forest_to_obj(a)) == a


def test_forest_basic_shape(repo):
    f = {e.id: e for e in build_forest(*repo).entries}
    assert f["src/app"].depends_on == ("src/app/core",)
    assert f["src/app/core"].depends_on == ("src/app/util",)
    assert f["src/app/util"].layer == 0
    assert f["src/app/core"].layer == 1 and f["src/app"].layer == 2
    assert {"app", "__main__"} <= set(f["src/app"].entry_points)
    assert f["db"].owns.tables == ("users",)
    assert "app" in f["src/app"].owns.commands
    assert f["src/app/cyc_a"].layer == f["src/app/cyc_b"].layer
    assert "cycle: src/app/cyc_a src/app/cyc_b" in build_forest(*repo).notes


def test_depends_on_and_used_by_mirror(repo):
    f = build_forest(*repo)
    fwd = {(e.id, d) for e in f.entries for d in e.depends_on}
    back = {(u, e.id) for e in f.entries for u in e.used_by}
    assert fwd == back and fwd


def test_layers_cycles_collapse():
    lay, notes = layers(["a", "b", "c", "d"], {"a": ("b",), "b": ("c",), "c": ("b",), "d": ()})
    assert lay == {"a": 1, "b": 0, "c": 0, "d": 0}
    assert notes == ["cycle: b c"]


def test_planted_missing_edge_is_an_edge_finding(repo):
    actual = build_forest(*repo)
    planted = Forest(actual.sha, tuple(
        replace(e, depends_on=()) if e.id == "src/app/core" else e for e in actual.entries),
        actual.notes)
    found = compare_edges(planted, actual)
    assert [(x.status, x.key) for x in found] == [("edge", "edge:src/app/core->src/app/util")]
    assert compare_edges(actual, actual) == ()
    assert compare_edges(actual, planted)[0].detail == "claimed, not in code"


def test_cli_forest(repo, capsys):
    r, sha = repo
    assert cli.main(["forest", sha, "--repo", str(r), "--format", "json"]) == Exit.OK
    out = capsys.readouterr().out
    assert forest_from_obj(__import__("json").loads(out)).sha == sha
    assert cli.main(["forest", "--repo", str(r)]) == Exit.OK
    assert "src/app/core (layer 1)" in capsys.readouterr().out
