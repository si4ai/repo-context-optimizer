"""Routes extractor: decorators, route tables, handler dispatch, purity, parse errors."""
import os
from pathlib import Path

from rco import build, canon, fixture
from rco.extract.routes import RoutesExtractor
from rco.model import Kind, Node, SourceFile

EX = RoutesExtractor()
HERE = Path(__file__).parent
CASE = "routes_basic"


def _routes(name: str) -> dict[str, str]:
    p = HERE / "fixtures" / CASE / "svc" / name
    nodes = EX.extract(SourceFile(f"svc/{name}", "0" * 40, p.read_bytes()))
    assert all(n.kind is Kind.ROUTE and n.path == f"svc/{name}" for n in nodes)
    assert all(n.sig == "" and n.attrs == () and n.children == () for n in nodes)
    return {n.name: n.doc for n in nodes}


def _src(src: str, path: str = "app/m.py") -> list[Node]:
    return EX.extract(SourceFile(path, "0" * 40, src.encode()))


def test_claims_python_and_version_bumped():
    assert EX.claims("app/server.py") and not EX.claims("app/schema.sql")
    assert EX.version != "0"


def test_decorators_fastapi_style():
    assert _routes("fast.py") == {
        "GET /api/v1/items/{item_id}": "Read one item.",
        "POST /api/v1/items": "Create an item.",
        "GET /api/v1/sync": "",
        "POST /api/v1/sync": "",
        "GET /api/v1/legacy": "",
        "WS /ws/{desk}": "Live feed for one desk.",
    }


def test_decorators_and_add_url_rule_flask_style():
    assert _routes("flaskapp.py") == {
        "GET /health": "Liveness probe.",
        "GET /users/<int:user_id>": "One user.",
        "DELETE /users/<int:user_id>": "One user.",
        "POST /export": "Export everything.",
        "ANY /dynamic": "",
    }


def test_route_table_starlette_style_with_methods_name():
    assert _routes("star.py") == {
        "WS /ws/{desk}": "Desk socket.",
        "GET /ping": "Ping.",
        "GET /{path:path}": "Every HTTP verb goes to the same dispatcher.",
        "HEAD /{path:path}": "Every HTTP verb goes to the same dispatcher.",
        "POST /{path:path}": "Every HTTP verb goes to the same dispatcher.",
    }


def test_route_table_aiohttp_style():
    assert _routes("aio.py") == {
        "GET /": "Front page.",
        "POST /submit": "Front page.",
        "GET /stats": "Counters.",
        "PUT /stats": "Counters.",
        "ANY /any": "Front page.",
    }


def test_handler_dispatch_follows_self_calls():
    assert _routes("handler.py") == {
        "GET /health": "",
        "GET /api/rows": "",
        "GET /api/strip": "",
        "GET /api/labels": "",
        "POST /api/collect": "",
    }


def test_client_calls_are_not_routes():
    assert _routes("client_use.py") == {}


def test_one_node_per_route_even_when_compared_twice():
    src = ("class H:\n    def do_GET(self):\n"
           "        if self.path == '/x': pass\n        if self.path == '/x': pass\n")
    assert [n.name for n in _src(src)] == ["GET /x"]


def test_body_change_keeps_route_path_change_moves_it():
    a = _src("@app.get('/a')\ndef f():\n    return 1\n")
    b = _src("@app.get('/a')\ndef f():\n    x = 2\n    return x\n")
    c = _src("@app.get('/b')\ndef f():\n    return 1\n")
    assert a == b and a != c


def test_no_line_numbers_in_nodes():
    src = "@app.post('/a')\ndef f():\n    pass\n"
    assert _src(src) == _src("\n\n\n" + src)


def test_parse_error_is_a_file_node_not_an_exception():
    assert _src("x = '/a'\ndef (:\n", "app/bad.py") == [
        Node(Kind.FILE, "bad.py", "app/bad.py", attrs=(("error", "parse"),))]


def test_python_and_routes_parse_errors_collapse_to_one_node():
    nodes = build.nodes_for(SourceFile("app/bad.py", "0" * 40, b"def (:\n"))
    assert nodes == [Node(Kind.FILE, "bad.py", "app/bad.py", attrs=(("error", "parse"),))]


def test_never_raises_on_deep_nesting_or_bad_bytes():
    assert isinstance(_src("x = " + "(" * 5000 + ")" * 5000 + "\n"), list)
    assert isinstance(EX.extract(SourceFile("a.py", "0" * 40, bytes(range(256)))), list)


def test_pure_same_input_same_output():
    p = HERE / "fixtures" / CASE / "svc" / "fast.py"
    src = SourceFile("svc/fast.py", "0" * 40, p.read_bytes())
    assert EX.extract(src) == EX.extract(src)


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_golden_routes_basic(tmp_path):
    """Regenerate with RCO_REGEN=1 and say why in the PR."""
    repo, sha = fixture.make_repo(tmp_path / CASE, _files(HERE / "fixtures" / CASE))
    got = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    assert got == canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    golden = HERE / "golden" / f"{CASE}.json"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()


def test_file_without_a_quoted_slash_is_not_parsed():
    # no string literal starts with "/", so there is nothing to find
    assert _src("def (:\n") == []
    assert _src("@app.get(\"/a\")\ndef f(): pass\n") != []
