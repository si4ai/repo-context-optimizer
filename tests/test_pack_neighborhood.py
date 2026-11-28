"""Card 1342: with --for the Forest is the owners plus their one-hop modules, cut before Trees."""
from rco import fixture
from rco.forest_model import estimate_tokens
from rco.pack import MIN_BUDGET, pack, pack_markdown

N = 400


def _repo(tmp_path):
    files = {
        "focus/__init__.py": "",
        "focus/f.py": "from m001.a import a001\nfrom m002.a import a002\n\n\n"
                      "def f() -> int:\n    return a001() + a002()\n",
    }
    for i in range(N):
        files[f"m{i:03d}/__init__.py"] = ""
        files[f"m{i:03d}/a.py"] = "".join(
            f"def a{i:03d}{'' if j == 0 else f'_{j}'}() -> int:\n    return {i}\n\n" for j in range(8))
    files["m003/use.py"] = "from focus.f import f\n\n\ndef g() -> int:\n    return f()\n"
    return fixture.make_repo(tmp_path / "big", files)


def test_for_keeps_imports_and_only_the_neighborhood_forest(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, ["focus/f.py"], 12000, fit_to_imports=False)
    assert {e.id for e in p.forest} == {"focus", "m001", "m002", "m003"}
    assert {n.path for t in p.trees for n in t.nodes} == {"m001/a.py", "m002/a.py"}
    assert p.used <= 12000 and not [t for t in p.trimmed if t.startswith("node:")]
    # the default (card 1341 budget) keeps the same neighborhood
    assert {e.id for e in pack(repo, sha, ["focus/f.py"]).forest} <= {"focus", "m001", "m002", "m003"}


def test_without_for_keeps_the_full_forest(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, None, 10 ** 6)
    assert {f"m{i:03d}" for i in range(N)} <= {e.id for e in p.forest}


def test_neighbor_lines_cut_before_trees_and_owner_last(tmp_path):
    repo, sha = _repo(tmp_path)
    full = pack(repo, sha, ["focus/f.py"], 10 ** 6, fit_to_imports=False)
    seen_node_cut = False
    for budget in range(MIN_BUDGET, full.used + 1, 16):
        p = pack(repo, sha, ["focus/f.py"], budget, fit_to_imports=False)
        assert estimate_tokens(pack_markdown(p, "focus/f.py")) <= budget
        cuts = [t for t in p.trimmed if not t.startswith("hop:")]
        kinds = ["owner" if t == "forest:focus" else t.split(":", 1)[0] for t in cuts]
        if "node" in kinds:
            seen_node_cut = True
            # every neighbor Forest line went before the first Tree node
            assert {"forest:m001", "forest:m002", "forest:m003"} <= set(cuts[:kinds.index("node")])
        if "owner" in kinds:
            assert not p.trees  # the owner's line goes last
    assert seen_node_cut


def test_neighbor_lines_cut_in_reverse_hop_order(tmp_path):
    # card 1344 (validator 7172 W1): hop ids sort m001, m002, m003, and the
    # neighbor Forest lines go from the end of that list
    repo, sha = _repo(tmp_path)
    full = pack(repo, sha, ["focus/f.py"], 10 ** 6, fit_to_imports=False)
    for budget in range(MIN_BUDGET, full.used + 1, 16):
        p = pack(repo, sha, ["focus/f.py"], budget, fit_to_imports=False)
        cuts = [t for t in p.trimmed if t.startswith("forest:m")]
        assert cuts == ["forest:m003", "forest:m002", "forest:m001"][:len(cuts)]


def test_empty_owner_line_still_goes_last(tmp_path):
    # README: "the owning modules' Forest lines go last", even an owner whose
    # line counts as empty (layer 0, no edges, nothing owned, no prose)
    repo, sha = _repo(tmp_path)
    sha = fixture.commit(repo, {"lone/__init__.py": "", "lone/x.py": "X = 1\n"})
    paths = ["focus/f.py", "lone/x.py"]
    full = pack(repo, sha, paths, 10 ** 6, fit_to_imports=False)
    assert "lone" in {e.id for e in full.forest}
    seen_node_cut = False
    for budget in range(MIN_BUDGET, full.used + 1, 16):
        p = pack(repo, sha, paths, budget, fit_to_imports=False)
        if any(t.startswith("node:") for t in p.trimmed):
            seen_node_cut = True
        if "forest:lone" in p.trimmed or "forest:focus" in p.trimmed:
            assert not p.trees  # no owner line goes before the last Tree node
    assert seen_node_cut
