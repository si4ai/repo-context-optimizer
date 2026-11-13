"""Non-Python code contracts: JS/TS, Go, Rust, shell and PowerShell."""
import random
from pathlib import Path

import pytest

from rco import build, canon, fixture
from rco.extract.langs import GoExtractor, JsExtractor, RustExtractor, ShellExtractor
from rco.model import Kind, SourceFile, Tree, walk
from rco.render import map_markdown

HERE = Path(__file__).parent
FIX = HERE / "fixtures" / "polyglot_basic"


def _nodes(ex, path, text):
    return ex.extract(SourceFile(path, "0", text.encode()))


def _flat(nodes):
    t = Tree("m", "", (), tuple(nodes))
    return {key.split(":", 2)[2]: n for key, n in walk(t)}


def _fix(rel):
    return (FIX / rel).read_text()


def test_ts_module_exports_only():
    flat = _flat(_nodes(JsExtractor(), "web/api.ts", _fix("web/api.ts")))
    top = flat["web/api.ts"]
    assert top.kind is Kind.SRCMODULE
    assert dict(top.attrs) == {"lang": "ts", "module": "esm"}
    assert top.doc == "Typed client for the jobs API."
    assert flat["getJob"].sig == "(id: JobId, opts: { cache?: boolean } = {}): Promise<Job>"
    assert dict(flat["getJob"].attrs) == {"async": "1"}
    assert flat["getJob"].doc == "Fetch one job by id."
    assert flat["listJobs"].sig == "(limit = 10): Promise<Job[]>"
    assert "helper" not in flat  # not exported
    assert flat["Job"].doc == "One job row as the API returns it."
    assert flat["Job.state"].sig == '"queued" | "done"'
    assert flat["Job.retry"].kind is Kind.METHOD
    assert flat["JobId"].sig == "= string | number"
    assert {"Color.Red", "Color.Green"} <= set(flat)
    assert dict(flat["MAX_JOBS"].attrs)["value"] == "50"
    cls = flat["JobClient"]
    assert cls.sig == "extends Base implements Client"
    assert {"JobClient.constructor", "JobClient.cancel", "JobClient.count",
            "JobClient.VERSION"} <= set(flat)
    assert not {"JobClient.token", "JobClient.#secret", "JobClient._internal"} & set(flat)
    assert flat["JobClient.cancel"].doc == "Cancel a job."
    assert flat["exports"].sig == '["default", "half"]'


def test_js_script_iife_and_global_exports():
    flat = _flat(_nodes(JsExtractor(), "web/http.js", _fix("web/http.js")))
    assert dict(flat["web/http.js"].attrs)["module"] == "script"
    assert flat["request"].sig == "(url, opts)"
    assert "_private" not in flat
    assert flat["exports"].sig == '["WebHttp"]'


def test_js_braces_in_strings_regex_and_comments_do_not_shift_depth():
    src = ('const A = "{";\nconst B = /[{]/;\n// {\n/* { */\nconst C = `${"{"}`;\n'
           'function after(x) { return x; }\n')
    flat = _flat(_nodes(JsExtractor(), "a.js", src))
    assert "after" in flat


def test_go_exported_only_and_methods_on_types():
    flat = _flat(_nodes(GoExtractor(), "svc/server.go", _fix("svc/server.go")))
    top = flat["svc/server.go"]
    assert dict(top.attrs) == {"lang": "go", "package": "svc"}
    assert top.doc == "Package svc serves the jobs API."
    assert flat["New"].sig == "(addr string) *Server"
    assert flat["Server.Start"].sig == "(ctx Context, port int) (err error)"
    assert flat["Server.Start"].doc == "Start listens on Addr."
    assert "Server.stop" not in flat and "internal" not in flat
    assert flat["Server.Addr"].sig == "string"
    assert "Server.mux" not in flat
    assert dict(flat["Server.Logger"].attrs) == {"embedded": "1"}
    assert flat["Handler.Serve"].kind is Kind.METHOD
    assert "Handler.name" not in flat
    assert dict(flat["ModeFast"].attrs)["value"] == '"fast"'
    assert "modeSlow" not in flat
    assert dict(flat["DefaultName"].attrs) == {"value": '"svc"', "var": "1"}
    assert dict(flat["ID"].attrs)["kind"] == "alias"


def test_rust_pub_items_impls_and_lifetimes():
    flat = _flat(_nodes(RustExtractor(), "core/lib.rs", _fix("core/lib.rs")))
    assert flat["core/lib.rs"].doc == "Core types for the job engine."
    job = flat["Job"]
    assert job.sig == "<'a>"
    assert dict(job.attrs) == {"derive": "Clone, Debug", "kind": "struct"}
    assert job.doc == "One job."
    assert {"Job.id", "Job.name", "Job.new"} <= set(flat)
    assert "Job.secret" not in flat and "Job.hidden" not in flat
    assert flat["Job.new"].sig == "(id: JobId, name: &'a str) -> Self"
    assert flat["State.Running"].sig == "{ pid: u32 }"
    assert dict(flat["State"].attrs)["trait"] == "fmt::Display"
    assert flat["State.fmt"].kind is Kind.METHOD
    assert flat["Runner.run"].kind is Kind.METHOD
    assert flat["spawn"].sig == "<T: Runner>(r: T) -> Option<T::Output> where T: Send"
    assert "private_fn" not in flat
    assert flat["JobId"].sig == "= u64"
    assert dict(flat["MAX_JOBS"].attrs)["value"] == "64"
    assert flat["pub use"].sig == '["crate::queue::{Queue, Item}"]'


def test_shell_and_powershell_functions():
    flat = _flat(_nodes(ShellExtractor(), "scripts/deploy.sh", _fix("scripts/deploy.sh")))
    assert flat["scripts/deploy.sh"].doc == "Deploy the service to one host."
    assert flat["usage"].doc == "Print usage and exit."
    assert "push_files" in flat
    assert dict(flat["TARGET_DIR"].attrs)["value"] == "/srv/app"
    ps = _flat(_nodes(ShellExtractor(), "scripts/setup.ps1", _fix("scripts/setup.ps1")))
    assert dict(ps["scripts/setup.ps1"].attrs)["lang"] == "powershell"
    assert ps["Install-Tools"].sig == '([string]$Version = "1.0", [switch]$Force)'
    assert ps["Install-Tools"].doc == "Install the toolchain."
    assert ps["Test-Ready"].sig == "($Path)"


@pytest.mark.parametrize("ex,path", [(JsExtractor(), "x.ts"), (JsExtractor(), "x.js"),
                                     (GoExtractor(), "x.go"), (RustExtractor(), "x.rs"),
                                     (ShellExtractor(), "x.sh"), (ShellExtractor(), "x.ps1")])
def test_scanner_never_raises_and_is_deterministic(ex, path):
    rng = random.Random(1234)
    alphabet = "{}()[]<>;:,.=/*'\"`#\\\n abcfnpu_$r!?"
    for _ in range(200):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 120)))
        a = ex.extract(SourceFile(path, "0", text.encode()))
        b = ex.extract(SourceFile(path, "0", text.encode()))
        assert a == b and len(a) == 1


def test_polyglot_map_is_byte_identical_twice(tmp_path):
    files = {p.relative_to(FIX).as_posix(): p.read_bytes()
             for p in sorted(FIX.rglob("*")) if p.is_file()}
    repo, sha = fixture.make_repo(tmp_path / "poly", files)
    a = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    b = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    assert a == b
    md = map_markdown(build.build_map(repo, sha))
    assert "fn new(id: JobId, name: &'a str) -> Self" in md
    assert "func New(addr string) *Server" in md
    assert "async function getJob(" in md


def test_js_signature_change_is_a_contract_change_and_body_change_is_not(tmp_path):
    from rco import diff
    base = {"web/a.js": "export function f(a) { return a; }\n"}
    repo, a = fixture.make_repo(tmp_path / "d", base)
    _, b = fixture.make_repo(tmp_path / "d", {"web/a.js": "export function f(a) { return a + 1; }\n"})
    _, c = fixture.make_repo(tmp_path / "d", {"web/a.js": "export function f(a, b) { return a; }\n"})
    assert diff.diff_maps(build.build_map(repo, a), build.build_map(repo, b)).exit_code() == 0
    rep = diff.diff_maps(build.build_map(repo, b), build.build_map(repo, c))
    assert rep.exit_code() == 1
    assert any("f" in str(ch) for ch in rep.to_obj()["changes"])


def test_secrets_in_other_languages_are_redacted(tmp_path):
    token = "ghp_" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8"
    repo, sha = fixture.make_repo(tmp_path / "s", {
        "web/k.js": f'const API_KEY = "{token}";\nexport function g(t = "{token}") {{}}\n',
        "svc/k.go": f'package svc\nconst Token = "{token}"\n',
        "ops/k.sh": f"GITHUB_TOKEN={token}\n",
    })
    out = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    assert token.encode() not in out


def test_js_nested_template_literals_keep_later_functions():
    src = ("function first(rows) {\n"
           "  return `<tr>${rows.map(r => `<td>${r}</td>`).join(\"\")}</tr>`;\n"
           "}\n"
           "function second() { return `a ${ {x: 1}.x } b`; }\n"
           "function third() {}\n")
    flat = _flat(_nodes(JsExtractor(), "a.js", src))
    assert {"first", "second", "third"} <= set(flat)
