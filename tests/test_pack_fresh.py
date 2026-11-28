"""Card 1315: the pack names the commit and source it came from and warns when the code moved on."""
import json

from rco import build, canon, cli, fixture
from rco.forest_model import pack_from_obj, pack_to_obj
from rco.output import Exit
from rco.pack import MIN_BUDGET, PackReport, pack, pack_markdown
from rco.store import IndexRow, MemoryStore, NodeKind
from update_history import make_history

FOR = ["pkg/core.py"]


def _publish(store, repo, sha):
    m = build.build_map(repo, sha)
    for t in m.trees:
        store.put_node(NodeKind.TREE, t.module_hash, canon.tree_to_obj(t))
    store.index_sha(sha, tuple(IndexRow(t.module, t.module_hash, canon.tree_hash(t))
                               for t in m.trees))


def _md(p, paths=FOR):
    return pack_markdown(p, " ".join(paths))


def _built_line(text):
    (line,) = [x for x in text.splitlines() if x.startswith("# built from ")]
    return line


def _warning(text):
    w = [x for x in text.splitlines() if x.startswith("# warning: changed since this pack: ")]
    return w[0] if w else None


def test_header_current(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    text = _md(pack(repo, shas[-1], FOR, fit_to_imports=False))
    assert _built_line(text) == (f"# built from {shas[-1][:7]} (git); HEAD {shas[-1][:7]}; "
                                 "current; working tree matches")
    assert _warning(text) is None
    lines = text.splitlines()
    assert lines.index(_built_line(text)) == 1 < lines.index("## Forest")


def test_explicit_old_sha_behind(tmp_path):
    # A, then B changes pkg/core.py; pack A while HEAD is B
    repo, shas = make_history(tmp_path / "r")
    fixture.git(repo, "reset", "-q", "--hard", shas[1])
    text = _md(pack(repo, shas[0], FOR, fit_to_imports=False))
    assert f"# built from {shas[0][:7]} (git); HEAD {shas[1][:7]}; 1 commit behind HEAD;" \
        in _built_line(text)
    assert _built_line(text).endswith("working tree 1 file differs")
    assert _warning(text) == "# warning: changed since this pack: pkg/core.py"


def test_many_commits_behind_and_off_history(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    p = pack(repo, shas[0], FOR, fit_to_imports=False)
    assert p.built.behind == 4 and "4 commits behind HEAD" in _built_line(_md(p))
    fixture.git(repo, "checkout", "-q", "--orphan", "other")
    fixture.commit(repo, {"pkg/core.py": "def run(x: int) -> int:\n    return 0\n"}, "other")
    p = pack(repo, shas[0], FOR, fit_to_imports=False)
    assert p.built.behind is None and "not on HEAD's history" in _built_line(_md(p))


def test_dirty_tree_warns(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    (repo / "pkg" / "core.py").write_text("def run(x: int) -> int:\n    return 2\n")
    (repo / "pkg" / "new.py").write_text("VALUE = 1\n")  # untracked, same module
    (repo / "util" / "helpers.py").write_text("X = 1\n")  # another module
    p = pack(repo, shas[-1], FOR, fit_to_imports=False)
    assert p.built.differs == ("pkg/core.py", "pkg/new.py")
    text = _md(p)
    assert _built_line(text).endswith("current; working tree 2 files differ")
    assert _warning(text) == "# warning: changed since this pack: pkg/core.py, pkg/new.py"
    # with no --for every changed path counts, but only the count is shown
    whole = pack(repo, shas[-1])
    assert whole.built.differs == ("pkg/core.py", "pkg/new.py", "util/helpers.py")
    assert _built_line(pack_markdown(whole)).endswith("working tree 3 files differ")
    assert _warning(pack_markdown(whole)) is None


def test_warning_caps_names(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    for i in range(12):
        (repo / "pkg" / f"n{i:02d}.py").write_text("")
    text = _md(pack(repo, shas[-1], FOR, fit_to_imports=False))
    w = _warning(text)
    assert w.endswith("pkg/n09.py (+2 more)") and w.count(", ") == 9


def test_rco_dir_is_not_a_change(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK  # creates .rco/
    assert pack(repo, shas[-1], FOR, fit_to_imports=False).built.differs == ()


def test_source_line(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    assert "(git)" in _built_line(_md(pack(repo, shas[-1], FOR, store=MemoryStore(), fit_to_imports=False)))
    s = MemoryStore()
    _publish(s, repo, shas[-1])
    p = pack(repo, shas[-1], FOR, store=s, fit_to_imports=False)
    assert p.built.source == "store" and "(store)" in _built_line(_md(p))


def test_json_built_and_roundtrip(tmp_path):
    repo, shas = make_history(tmp_path / "r")
    (repo / "pkg" / "core.py").write_text("")
    p = pack(repo, shas[0], FOR, fit_to_imports=False)
    obj = pack_to_obj(p)
    assert obj["schema"] == "rco.pack/1"
    assert obj["built"] == {"source": "git", "head": shas[-1], "behind": 4,
                            "differs": ["pkg/core.py"]}
    assert pack_from_obj(json.loads(json.dumps(obj))) == p
    old = {k: v for k, v in obj.items() if k != "built"}  # a pack written before 1315
    assert pack_from_obj(old).built is None


def test_exit_0_with_warning(tmp_path, capsys):
    repo, shas = make_history(tmp_path / "r")
    (repo / "pkg" / "core.py").write_text("")
    assert cli.main(["pack", "--repo", str(repo), "--for", *FOR]) == Exit.OK
    assert _warning(capsys.readouterr().out) == "# warning: changed since this pack: pkg/core.py"
    assert PackReport(pack(repo, shas[-1], FOR, fit_to_imports=False)).exit_code() == Exit.OK


def test_warning_survives_trim(tmp_path):
    from pathlib import Path
    root = Path(__file__).parent / "fixtures" / "forest_basic"
    files = {f.relative_to(root).as_posix(): f.read_bytes()
             for f in sorted(root.rglob("*")) if f.is_file()}
    repo, sha = fixture.make_repo(tmp_path / "fb", files)
    (repo / "src" / "app" / "cli.py").write_text("")
    p = pack(repo, sha, ["src/app/cli.py"], MIN_BUDGET, fit_to_imports=False)
    assert p.trimmed and p.used <= MIN_BUDGET
    text = _md(p, ["src/app/cli.py"])
    assert _built_line(text).endswith("current; working tree 1 file differs")
    assert _warning(text) == "# warning: changed since this pack: src/app/cli.py"
