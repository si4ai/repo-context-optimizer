"""Card 1350: pack --for resolves Go, Rust and JS-workspace imports via manifests."""
from rco import fixture
from rco.pack import NOOP_NOTE, SMALL_NOTE, pack, pack_markdown


def _pad_go(name: str) -> str:
    body = "\n".join(
        f"func {name}{i}(x int) int {{\n"
        f"\ty := x * {i} + 1\n\treturn y - x / 3 + {i}\n}}"
        for i in range(40))
    return f"package {name}\n\n{body}\n"


def _pad_rs(name: str) -> str:
    body = "\n".join(
        f"pub fn {name}{i}(x: i32) -> i32 {{\n"
        f"    let y = x * {i} + 1;\n    y - x / 3 + {i}\n}}"
        for i in range(40))
    return f"{body}\n"


def _pad_ts(name: str) -> str:
    body = "\n".join(
        f"export function {name}{i}(x: number): number {{\n"
        f"    const y = x * {i} + 1\n    return y - ((x / 3) | 0) + {i}\n}}"
        for i in range(40))
    return f"{body}\n"


def _pack_for(tmp_path, name, files, for_path):
    repo, sha = fixture.make_repo(tmp_path / name, files)
    p = pack(repo, sha, [for_path], 12000)
    return p, pack_markdown(p, for_path)


def _lists(p, text, path: str) -> bool:
    """The imported file is in the pack text, a Tree, or the trimmed list."""
    if path in text:
        return True
    if any(n.path == path for t in p.trees for n in t.nodes):
        return True
    return any(path in item for item in p.trimmed)


def test_pack_for_resolves_go_module_import(tmp_path):
    p, text = _pack_for(tmp_path, "go", {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "cmd/app/main.go": (
            "package main\nimport (\n\t\"fmt\"\n\tsvc \"example.com/app/svc\"\n)\n"
            "import \"example.com/app/util\"\n"
        ),
        "svc/a.go": _pad_go("svc"),
        "util/u.go": _pad_go("util"),
    }, "cmd/app/main.go")
    assert NOOP_NOTE not in p.notes and SMALL_NOTE not in p.notes
    assert _lists(p, text, "svc/a.go") and _lists(p, text, "util/u.go")


def test_pack_for_resolves_rust_workspace_crate(tmp_path):
    p, text = _pack_for(tmp_path, "rs", {
        "Cargo.toml": '[package]\nname = "app"\n',
        "src/lib.rs": "pub mod net;\nuse shared::Item;\n",
        "src/net/mod.rs": "pub fn n() {}\n",
        "shared/Cargo.toml": '[package]\nname = "shared"\n',
        "shared/src/lib.rs": _pad_rs("thing") + "pub struct Item;\n",
    }, "src/lib.rs")
    assert NOOP_NOTE not in p.notes and SMALL_NOTE not in p.notes
    assert _lists(p, text, "shared/src/lib.rs")


def test_pack_for_resolves_js_workspace_package(tmp_path):
    p, text = _pack_for(tmp_path, "js", {
        "web/a.ts": (
            "import ui from '@acme/ui/button'\n\n"
            "export function use() { return ui }\n"
        ),
        "packages/ui/package.json": '{"name": "@acme/ui"}\n',
        "packages/ui/button.tsx": _pad_ts("button"),
    }, "web/a.ts")
    assert NOOP_NOTE not in p.notes and SMALL_NOTE not in p.notes
    assert _lists(p, text, "packages/ui/button.tsx")


def test_replay_resolves_go_module_import(tmp_path):
    from rco import metrics
    from rco.replay import replay
    files = {
        "go.mod": "module example.com/app\n\ngo 1.22\n",
        "cmd/app/main.go": (
            "package main\nimport (\n\t\"fmt\"\n\tsvc \"example.com/app/svc\"\n)\n"
            "import \"example.com/app/util\"\n"
        ),
        "svc/a.go": _pad_go("svc"),
        "util/u.go": _pad_go("util"),
    }
    repo, _ = fixture.make_repo(tmp_path / "rg", files)
    sha = fixture.commit(repo, {"cmd/app/main.go": files["cmd/app/main.go"] + "// edit\n"})
    row = {r["sha"]: r for r in
           replay(repo, 10, 12000, 50, metrics.estimate).to_obj()["commits"]}[sha]
    assert row["without"]["imported_files"] >= 2
