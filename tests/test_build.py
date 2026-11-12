"""Determinism and hashing of build_map (tree-contract, Determinism tests)."""
from pathlib import Path

from rco import build, canon, fixture, modules
from rco.model import Module, SourceFile

FILES = {
    "pkg/__init__.py": '"""Pkg."""\n',
    "pkg/a.py": "def f(x: int) -> int:\n    return x\n",
    "README.md": "# r\n",
}


def _bytes(repo: Path, sha: str) -> bytes:
    return canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))


def _tree(m, mod):
    (t,) = [t for t in m.trees if t.module == mod]
    return t


def test_same_sha_same_bytes_twice(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", FILES)
    assert _bytes(repo, sha) == _bytes(repo, sha)


def test_reordered_input_same_tree():
    mod = Module("pkg", ("pkg/a.py", "pkg/b.py"))
    files = [SourceFile("pkg/a.py", "1" * 40, b"def f(): pass\n"),
             SourceFile("pkg/b.py", "2" * 40, b"def g(): pass\nclass A: pass\n")]
    assert build.build_tree(mod, files) == build.build_tree(mod, list(reversed(files)))


def test_body_change_same_treehash_new_modulehash(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    b = fixture.commit(repo, {"pkg/a.py": "def f(x: int) -> int:\n    y = x\n    return y\n"})
    ta, tb = _tree(build.build_map(repo, a), "pkg"), _tree(build.build_map(repo, b), "pkg")
    assert canon.tree_hash(ta) == canon.tree_hash(tb)
    assert ta.module_hash != tb.module_hash


def test_signature_change_moves_contract(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    b = fixture.commit(repo, {"pkg/a.py": "def f(x: int, y: int) -> int:\n    return x\n"})
    ta, tb = _tree(build.build_map(repo, a), "pkg"), _tree(build.build_map(repo, b), "pkg")
    assert ta.nodes != tb.nodes
    assert canon.tree_hash(ta) != canon.tree_hash(tb)


def test_same_content_at_two_shas_same_hashes(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    b = fixture.commit(repo, {"README.md": "# changed\n"})
    assert a != b
    assert _tree(build.build_map(repo, a), "pkg") == _tree(build.build_map(repo, b), "pkg")


def test_reads_git_not_worktree(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", FILES)
    before = _bytes(repo, sha)
    (repo / "pkg" / "a.py").write_text("def changed(): pass\n")
    assert _bytes(repo, sha) == before


def test_unknown_language_is_file_outline_and_for_filters(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", FILES)
    m = build.build_map(repo, sha, only=["README.md"])
    assert [t.module for t in m.trees] == ["."]
    assert [(n.kind.value, n.path) for n in m.trees[0].nodes] == [("file", "README.md")]


def test_lfs_pointer_symlink_and_submodule(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "r", {
        "big.bin": b"version https://git-lfs.github.com/spec/v1\noid sha256:00\nsize 1\n",
    })
    (repo / "link").symlink_to("big.bin")
    sub, subsha = fixture.make_repo(tmp_path / "sub", {"x.txt": "x\n"})
    fixture.git(repo, "add", "link")
    fixture.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{subsha},vendor/sub")
    fixture.git(repo, "commit", "-q", "-m", "links")
    sha = fixture.git(repo, "rev-parse", "HEAD")
    m = build.build_map(repo, sha)
    assert m.notes == ("skipped submodule vendor/sub",)
    attrs = {n.path: dict(n.attrs) for t in m.trees for n in t.nodes}
    assert attrs["big.bin"] == {"lfs": "pointer"}
    assert attrs["link"] == {"link": "1"}


def test_no_line_numbers_in_any_key(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", FILES)
    obj = canon.map_to_obj(build.build_map(repo, sha))

    def keys(o):
        if isinstance(o, dict):
            for k, v in o.items():
                yield k
                yield from keys(v)
        elif isinstance(o, list):
            for v in o:
                yield from keys(v)
    assert not [k for k in keys(obj) if "line" in k]


def test_module_of_package_grain():
    assert modules.module_of("a/b/c.py") == "a/b"
    assert modules.module_of("README.md") == "."
    assert [m.id for m in modules.group(["b/x", "a/y", "z"])] == [".", "a", "b"]


def test_cli_tree_survives_a_deeply_nested_file(tmp_path, capsys):
    from rco import cli
    from rco.output import Exit
    repo, sha = fixture.make_repo(tmp_path / "r", {
        **FILES, "pkg/deep.py": "X: " + "a." * 1000 + "b = 1\n",
    })
    assert cli.main(["tree", sha, "--repo", str(repo)]) == Exit.OK
    out = capsys.readouterr().out
    assert "deep.py" in out and "error" in out
