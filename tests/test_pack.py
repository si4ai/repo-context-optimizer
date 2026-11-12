"""Phase 2 step 5: `rco pack` stays within budget, names every cut, and is deterministic."""
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from rco import build, canon, cli, fixture
from rco.forest_model import Prose, ProseSource, estimate_tokens, pack_from_obj, pack_to_obj
from rco.model import node_key
from rco.output import Exit, UsageError
from rco.pack import MIN_BUDGET, pack, pack_markdown
from rco.store import IndexRow, MemoryStore, NodeKind

HERE = Path(__file__).parent
FIXTURES = ["forest_basic", "py_basic", "cli_basic", "routes_basic", "sql_basic"]


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def _repo(tmp_path, case="forest_basic"):
    return fixture.make_repo(tmp_path / case, _files(HERE / "fixtures" / case))


def _publish(store, repo, sha):
    m = build.build_map(repo, sha)
    for t in m.trees:
        store.put_node(NodeKind.TREE, t.module_hash, canon.tree_to_obj(t))
    store.index_sha(sha, tuple(IndexRow(t.module, t.module_hash, canon.tree_hash(t))
                               for t in m.trees))
    return m


def test_golden_markdown(tmp_path):
    repo, sha = _repo(tmp_path)
    got = pack_markdown(pack(repo, sha, ["src/app/cli.py"]), "src/app/cli.py").encode()
    golden = HERE / "golden" / "pack_forest_basic.md"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()


def test_whole_repo_pack_is_forest_only(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha)
    assert p.trees == () and p.hop == () and p.trimmed == ()
    assert [e.id for e in p.forest][:3] == [".", "db", "src/app"]


def test_for_gives_full_trees_and_one_hop(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, ["src/app/core/engine.py"])
    assert [t.module for t in p.trees] == ["src/app/core"]
    assert [h.id for h in p.hop] == ["src/app", "src/app/util"]
    assert pack_from_obj(pack_to_obj(p)) == p


@pytest.mark.parametrize("case", FIXTURES)
def test_never_over_budget_and_every_cut_named(tmp_path, case):
    repo, sha = _repo(tmp_path, case)
    full = fixture.git(repo, "ls-files").splitlines()
    for budget in (MIN_BUDGET, 80, 120, 200, 400, 1000, 8000):
        p = pack(repo, sha, full[:2], budget)
        text = pack_markdown(p, " ".join(full[:2]))
        assert estimate_tokens(text) <= budget, (case, budget)
        assert p.used == estimate_tokens(text)
        full_p = pack(repo, sha, full[:2], 10**6)
        kept = ({f"hop:{h.id}" for h in p.hop} | {f"forest:{e.id}" for e in p.forest}
                | {f"node:{t.module}|{node_key(n.path, n.name, n.kind)}" for t in p.trees
                   for n in t.nodes})
        everything = ({f"hop:{h.id}" for h in full_p.hop} | {f"forest:{e.id}" for e in full_p.forest}
                      | {f"node:{t.module}|{node_key(n.path, n.name, n.kind)}"
                         for t in full_p.trees for n in t.nodes})
        assert kept | set(p.trimmed) == everything  # nothing dropped silently
        assert not kept & set(p.trimmed)


def test_cut_order_hop_then_nodes_then_forest(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, ["src/app/core/engine.py", "src/app/cli.py"], 90)
    assert {t.split(":", 1)[0] for t in p.trimmed} == {"hop", "node", "forest"}
    kinds = [t.split(":", 1)[0] for t in p.trimmed]
    assert kinds == sorted(kinds, key=["hop", "node", "forest"].index)


def test_byte_identical_across_runs(tmp_path):
    repo, sha = _repo(tmp_path)
    a = canon.canonical_json(pack_to_obj(pack(repo, sha, ["src/app/cli.py"], 150)))
    b = canon.canonical_json(pack_to_obj(pack(repo, sha, ["src/app/cli.py"], 150)))
    assert a == b


def test_empty_repo_empty_pack_exit_0(tmp_path, capsys):
    repo, sha = fixture.make_repo(tmp_path / "empty", {})
    p = pack(repo, sha)
    assert p.forest == () and p.trees == () and p.stale == ()
    assert cli.main(["pack", "--repo", str(repo)]) == Exit.OK


def test_stale_prose_shown_and_exit_1(tmp_path):
    repo, sha = _repo(tmp_path)
    s = MemoryStore()
    _publish(s, repo, sha)
    s.put_prose("src/app/util", Prose("Helpers for clamp.", ProseSource.MINED, "sha256:old"))
    m = build.build_map(repo, sha)
    core = next(t for t in m.trees if t.module == "src/app/core")
    s.put_prose("src/app/core", Prose("Engine.", ProseSource.MINED, canon.tree_hash(core)))
    p = pack(repo, sha, store=s)
    assert p.stale == ("src/app/util",)
    text = pack_markdown(p)
    assert "- src/app/util: Helpers for clamp. [stale]" in text
    assert "- src/app/core: Engine. (depends on" in text
    from rco.pack import PackReport
    assert PackReport(p).exit_code() == Exit.FINDINGS
    assert "built from git" not in " ".join(p.notes)  # read from the store


def test_pack_without_store_writes_nothing(tmp_path):
    repo, sha = _repo(tmp_path)
    assert cli.main(["pack", "--repo", str(repo), "--for", "src/app/cli.py"]) == Exit.OK
    assert not (repo / ".rco").exists()
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_budget_floor_is_a_usage_error(tmp_path):
    repo, sha = _repo(tmp_path)
    with pytest.raises(UsageError):
        pack(repo, sha, budget=MIN_BUDGET - 1)
    assert cli.main(["pack", "--repo", str(repo), "--budget", "10"]) == Exit.USAGE


def test_cli_json(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert cli.main(["pack", "--repo", str(repo), "--format", "json", "--for",
                     "src/app/cli.py", "--budget", "100"]) == Exit.OK
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == "rco.pack/1" and out["used"] <= 100 and out["trimmed"]


def test_long_for_label_still_within_budget(tmp_path):
    repo, sha = _repo(tmp_path)
    paths = ["src/app/cli.py"] * 100
    p = pack(repo, sha, paths, MIN_BUDGET)
    assert estimate_tokens(pack_markdown(p, " ".join(paths))) <= MIN_BUDGET
