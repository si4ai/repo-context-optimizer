"""Forest: manifest modules, the rco.toml override, and non-Python import edges."""
from rco import build, canon, cli, fixture
from rco.edges import file_edges, repo_facts
from rco.forest import build_forest
from rco.forest_model import forest_to_obj
from rco.modules import Layout, glob_regex, layout_from
from rco.output import Exit


def test_glob_regex():
    assert glob_regex("web/**").match("web/a/b.js")
    assert glob_regex("web/*.js").match("web/a.js")
    assert not glob_regex("web/*.js").match("web/x/a.js")
    assert glob_regex("**/test_*.py").match("test_a.py")
    assert glob_regex("**/test_*.py").match("a/b/test_a.py")
    assert not glob_regex("a?c").match("a/c")


def test_default_layout_is_the_directory_grain():
    lay = Layout()
    assert lay.module_of("README.md") == "."
    assert lay.module_of("src/rco/cli.py") == "src/rco"


def test_manifests_group_their_language_only():
    lay = layout_from({
        "package.json": b'{"name": "root"}',
        "apps/web/package.json": b'{"name": "web"}',
        "crates/core/Cargo.toml": b'[package]\nname = "core"\n',
        "Cargo.toml": b"[workspace]\nmembers = []\n",  # no [package]: not a module root
    })
    assert lay.module_of("apps/web/src/ui/button.tsx") == "apps/web"
    assert lay.module_of("apps/web/package.json") == "apps/web"
    assert lay.module_of("apps/web/src/ui/style.css") == "apps/web/src/ui"  # not JS: dir grain
    assert lay.module_of("tools/build.js") == "."  # the root package.json owns other JS
    assert lay.module_of("tools/build.py") == "tools"  # Python keeps the directory grain
    assert lay.module_of("crates/core/src/deep/x.rs") == "crates/core"
    assert lay.module_of("src/lib.rs") == "src"  # the workspace Cargo.toml is not a root


def test_override_rules_win_in_file_order_and_bad_rules_are_notes():
    toml = (b'[[module]]\nid = "web"\npaths = ["web/**", "static/*.js"]\n'
            b'[[module]]\nid = "all-py"\npaths = ["**/*.py"]\n'
            b'[[module]]\nid = "web"\npaths = []\n'
            b'[[module]]\npaths = ["x/**"]\n')
    lay = layout_from({"rco.toml": toml, "web/package.json": b'{"name": "w"}'})
    assert lay.module_of("web/app/main.py") == "web"
    assert lay.module_of("static/a.js") == "web"
    assert lay.module_of("svc/core.py") == "all-py"
    assert lay.module_of("svc/core.go") == "svc"
    assert len(lay.notes) == 2 and all(n.startswith("rco.toml: module ") for n in lay.notes)
    bad = layout_from({"rco.toml": b"[[module]\n"})
    assert bad.rules == () and "not valid TOML" in bad.notes[0]


def _edges(files, path):
    srcs = {p: v.encode() for p, v in files.items()}
    return file_edges(path, srcs[path], repo_facts(frozenset(srcs), srcs))


def test_js_ts_edges():
    files = {
        "web/a.ts": ('import { b } from "./b";\nimport type { T } from "../types/t";\n'
                     'export * from "./c.js";\nconst d = require("./d");\n'
                     '// import "./dead";\nconst s = "import x from \'./str\'";\n'
                     'import ui from "@acme/ui/button";\nimport lodash from "lodash";\n'),
        "web/b.ts": "", "types/t.d.ts": "", "types/t.ts": "", "web/c.ts": "", "web/d/index.js": "",
        "web/dead.ts": "", "web/str.ts": "",
        "packages/ui/package.json": '{"name": "@acme/ui"}', "packages/ui/button.tsx": "",
    }
    assert _edges(files, "web/a.ts") == ("packages/ui/button.tsx", "types/t.ts", "web/b.ts",
                                         "web/c.ts", "web/d/index.js")


def test_go_edges():
    files = {"go.mod": "module example.com/app\n\ngo 1.22\n",
             "cmd/app/main.go": ('package main\nimport (\n\t"fmt"\n\tsvc "example.com/app/svc"\n'
                                 '\t// "example.com/app/dead"\n)\nimport "example.com/app/util"\n'),
             "svc/a.go": "package svc\n", "svc/a_test.go": "package svc\n",
             "util/u.go": "package util\n", "dead/d.go": "package dead\n"}
    assert _edges(files, "cmd/app/main.go") == ("svc/a.go", "util/u.go")


def test_rust_edges():
    files = {"Cargo.toml": '[package]\nname = "app"\n',
             "src/lib.rs": "pub mod net;\nmod util;\nuse crate::net::http::Client;\nuse shared::Thing;\n",
             "src/net/mod.rs": "pub mod http;\nuse super::util::helper;\n",
             "src/net/http.rs": "", "src/util.rs": "",
             "shared/Cargo.toml": '[package]\nname = "shared"\n', "shared/src/lib.rs": ""}
    assert _edges(files, "src/lib.rs") == ("shared/src/lib.rs", "src/net/http.rs",
                                           "src/net/mod.rs", "src/util.rs")
    assert _edges(files, "src/net/mod.rs") == ("src/net/http.rs", "src/util.rs")


def test_shell_and_powershell_edges():
    files = {"ops/deploy.sh": ('#!/bin/sh\n. "$(dirname "$0")/lib.sh"\nsource scripts/env.sh\n'
                               '# source ./dead.sh\nsource "$HOME/x.sh"\n'),
             "ops/lib.sh": "", "scripts/env.sh": "", "ops/dead.sh": "",
             "ops/setup.ps1": ". $PSScriptRoot\\helpers.ps1\nImport-Module ./mods/tools.psm1\n",
             "ops/helpers.ps1": "", "ops/mods/tools.psm1": ""}
    assert _edges(files, "ops/deploy.sh") == ("ops/lib.sh", "scripts/env.sh")
    assert _edges(files, "ops/setup.ps1") == ("ops/helpers.ps1", "ops/mods/tools.psm1")


POLY = {
    "rco.toml": '[[module]]\nid = "frontend"\npaths = ["web/**", "shared/**/*.ts"]\n',
    "web/package.json": '{"name": "web"}',
    "web/src/app.ts": 'import { fmt } from "../../shared/fmt";\nexport function app() {}\n',
    "shared/fmt.ts": "export function fmt() {}\n",
    "lib/package.json": '{"name": "lib"}',
    "lib/src/index.js": "module.exports = {};\n",
    "web/src/main.js": 'const lib = require("lib");\n',
    "go.mod": "module example.com/x\n",
    "svc/main.go": 'package main\nimport "example.com/x/store"\nfunc main() {}\n',
    "store/s.go": "package store\nfunc Get() {}\n",
    "ops/run.sh": '. "$(dirname "$0")/../svc/env.sh"\n',
    "svc/env.sh": "X=1\n",
}


def test_forest_edges_and_modules_across_languages(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "poly", POLY)
    f = build_forest(repo, sha)
    deps = {e.id: e.depends_on for e in f.entries}
    assert deps["frontend"] == ("lib",)
    assert deps["svc"] == ("store",)
    assert deps["ops"] == ("svc",)
    assert {e.id for e in f.entries} >= {"frontend", "lib", "svc", "store", "ops"}
    a = canon.canonical_json(forest_to_obj(build_forest(repo, sha)))
    assert a == canon.canonical_json(forest_to_obj(f))
    m = build.build_map(repo, sha, only=["shared/fmt.ts"])
    assert [t.module for t in m.trees] == ["frontend"]


def test_bad_override_is_a_note_not_an_error(tmp_path, capsys):
    repo, sha = fixture.make_repo(tmp_path / "bad", {"rco.toml": "[[module]\n", "a/x.py": ""})
    m = build.build_map(repo, sha)
    assert any("rco.toml: not valid TOML" in n for n in m.notes)
    assert cli.main(["tree", "--repo", str(repo)]) == Exit.OK


def test_check_edges_hold_for_non_python_edges(tmp_path, capsys):
    repo, sha = fixture.make_repo(tmp_path / "poly", POLY)
    out = tmp_path / "forest.json"
    assert cli.main(["forest", "--repo", str(repo), "--format", "json", "--out", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["check", "--repo", str(repo), str(out), sha]) == Exit.OK


def test_update_equals_full_rebuild_when_the_layout_changes(tmp_path):
    from rco.store import MemoryStore
    from rco.update import update

    repo, s1 = fixture.make_repo(tmp_path / "lay", {
        "web/src/a.js": "export function a() {}\n", "web/b.js": "export function b() {}\n",
        "py/x.py": "def x():\n    pass\n"})
    s2 = fixture.commit(repo, {"web/package.json": '{"name": "web"}'})
    s3 = fixture.commit(repo, {"rco.toml": '[[module]]\nid = "all"\npaths = ["**"]\n'})
    inc = MemoryStore()
    for sha in (s1, s2, s3):
        update(repo, inc, sha)
        full = MemoryStore()
        update(repo, full, sha)
        got = [(r.module, r.module_hash, r.tree_hash) for r in inc.list_by_sha(sha)]
        assert got == [(r.module, r.module_hash, r.tree_hash) for r in full.list_by_sha(sha)]
    assert [r.module for r in inc.list_by_sha(s1)] == ["py", "web", "web/src"]
    assert [r.module for r in inc.list_by_sha(s2)] == ["py", "web"]
    assert [r.module for r in inc.list_by_sha(s3)] == ["all"]
