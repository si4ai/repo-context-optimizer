"""phase 2 step 7: zero-config acceptance rows as smoke tests (README table)."""
from __future__ import annotations

import json

from rco import build, canon, cli, fixture, gitread
from rco.output import Exit
from rco.pack import pack
from rco.store import MemoryStore
from rco.update import update
from rco.prose import flags as prose_flags
from rco.forest_model import Prose, ProseSource
from rco.check import prose_name_findings
from rco.pack import pack_markdown
from rco.model import Kind, walk


SMALL = {
    "pkg/__init__.py": '"""pkg does the work."""\n',
    "pkg/core.py": "def run(x: int) -> int:\n    return x\n",
    "pkg/cli.py": (
        "import argparse\n\n\n"
        "def make_parser() -> argparse.ArgumentParser:\n"
        "    p = argparse.ArgumentParser(prog='pkg')\n"
        "    p.add_argument('--out', metavar='FILE')\n"
        "    return p\n"
    ),
    "db/schema.sql": "CREATE TABLE users (id BIGINT PRIMARY KEY);\n",
}


def test_small_python_repo_pack_and_diff(tmp_path, capsys):
    repo, a = fixture.make_repo(tmp_path / "small", SMALL)
    b = fixture.commit(repo, {
        "pkg/core.py": "def run(x: int, y: int = 0) -> int:\n    return x + y\n",
    }, "change sig")
    assert cli.main(["pack", "--repo", str(repo)]) == Exit.OK
    capsys.readouterr()
    assert cli.main(["pack", "--repo", str(repo), "--for", "pkg/core.py"]) == Exit.OK
    capsys.readouterr()
    assert cli.main(["diff", "--repo", str(repo), a, b]) == Exit.FINDINGS
    capsys.readouterr()
    m = build.build_map(repo, b)
    assert any(t.module == "pkg" for t in m.trees)
    assert any(n.kind is Kind.FUNCTION and n.name == "run" for t in m.trees for _, n in walk(t))


def test_polyglot_bundled_parsers_and_unknown_language_outline(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "poly", {
        **SMALL,
        "native/demo.rs": "pub fn main() {}\n",
        "native/demo.go": "package main\nfunc Main() {}\n",
        "native/demo.zig": "pub fn main() void {}\n",
    })
    m = build.build_map(repo, sha)
    paths = {n.path: n for t in m.trees for _, n in walk(t)}
    assert paths["native/demo.rs"].kind is Kind.SRCMODULE
    assert paths["native/demo.go"].kind is Kind.SRCMODULE
    assert paths["native/demo.zig"].kind is Kind.FILE  # unknown language: an outline
    assert "error" not in dict(paths["native/demo.zig"].attrs)
    names = {n.name for t in m.trees for _, n in walk(t)}
    assert {"main", "Main"} <= names
    assert cli.main(["pack", "--repo", str(repo)]) == 0


def test_large_layout_budget_and_incremental_extract(tmp_path):
    files = {f"m{i:02d}/__init__.py": f'"""module {i}."""\nVALUE = {i}\n' for i in range(24)}
    files["app/__init__.py"] = '"""app."""\n'
    files["app/core.py"] = "def run() -> int:\n    return 0\n"
    repo, a = fixture.make_repo(tmp_path / "large", files)
    p = pack(repo, a, budget=64)
    assert p.used <= 64
    assert p.trimmed  # a 24-module forest cannot all fit in 64 tokens
    store = MemoryStore()
    first = update(repo, store, a)
    assert first.swapped and first.extracted
    b = fixture.commit(repo, {"app/core.py": "def run() -> int:\n    return 1\n"}, "body")
    second = update(repo, store, b)
    assert second.swapped
    assert second.extracted == ("app",)


def test_submodule_is_skipped_with_a_note(tmp_path):
    repo, _ = fixture.make_repo(tmp_path / "super", SMALL)
    sub, subsha = fixture.make_repo(tmp_path / "sub", {"x.py": "X = 1\n"})
    fixture.git(repo, "update-index", "--add", "--cacheinfo", f"160000,{subsha},vendor/sub")
    fixture.git(repo, "commit", "-q", "-m", "add submodule")
    sha = fixture.git(repo, "rev-parse", "HEAD")
    m = build.build_map(repo, sha)
    assert any(n.startswith("skipped submodule") for n in m.notes)
    paths = [n.path for t in m.trees for _, n in walk(t)]
    assert not any(p.startswith("vendor/sub") for p in paths)


def test_lfs_pointer_is_opaque(tmp_path):
    pointer = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"00" * 32 + b"\nsize 1\n"
    repo, sha = fixture.make_repo(tmp_path / "lfs", {**SMALL, "big.bin": pointer})
    m = build.build_map(repo, sha)
    attrs = {n.path: dict(n.attrs) for t in m.trees for _, n in walk(t) if n.path == "big.bin"}
    assert attrs["big.bin"] == {"lfs": "pointer"}
    entries = {e.path: e for e in gitread.ls_tree(repo, sha)}
    data = gitread.read_blobs(repo, [entries["big.bin"].blob])
    assert data[entries["big.bin"].blob] == pointer


def test_empty_repo_empty_pack_exit_0(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "empty", {})
    p = pack(repo, sha)
    assert p.forest == () and p.trees == () and p.stale == ()
    assert cli.main(["pack", "--repo", str(repo)]) == Exit.OK


def test_name_in_tree_flags_in_pack_and_check(tmp_path, capsys):
    repo, sha = fixture.make_repo(tmp_path / "flags", SMALL)
    store = MemoryStore()
    update(repo, store, sha)
    m = build.build_map(repo, sha)
    pkg = next(t for t in m.trees if t.module == "pkg")
    store.put_prose("pkg", Prose("uses `nope_here`.", ProseSource.MINED, canon.tree_hash(pkg)))
    assert prose_flags(store.get_prose("pkg"), pkg) == ("nope_here",)
    p = pack(repo, sha, store=store)
    assert any(n.startswith("name-in-tree: pkg:") and "nope_here" in n for n in p.notes)
    text = pack_markdown(p)
    assert "[name flags: nope_here]" in text
    findings = prose_name_findings(store, m)
    assert any(f.key == "prose:pkg:nope_here" for f in findings)
    # write the map and the store to the repo so the CLI check sees it
    from rco.store.sqlite import open_repo_store
    sql = open_repo_store(repo)
    update(repo, sql, sha)
    sql.put_prose("pkg", Prose("uses `nope_here`.", ProseSource.MINED, canon.tree_hash(pkg)))
    map_path = tmp_path / "map.json"
    map_path.write_bytes(canon.canonical_json(canon.map_to_obj(m)))
    code = cli.main(["check", "--repo", str(repo), "--format", "json", str(map_path)])
    out = capsys.readouterr().out
    assert code == Exit.FINDINGS
    obj = json.loads(out)
    assert any(f["key"] == "prose:pkg:nope_here" for f in obj["findings"])
