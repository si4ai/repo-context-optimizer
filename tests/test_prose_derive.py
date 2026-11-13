"""Derived prose: deterministic text from Tree facts, calls and callers (no model)."""
import json
from pathlib import Path

import pytest

from rco import build, canon, fixture
from rco.forest_model import ProseSource, pack_to_obj
from rco.model import Kind, Node, Tree, walk
from rco.pack import pack, pack_markdown
from rco.prose import drain_queue
from rco.prose.check import unknown_names
from rco.prose.derive import (call_index, module_prose, name_words, params_and_return,
                              symbol_doc, with_derived_docs)
from rco.store import IndexRow, MemoryStore, NodeKind

HERE = Path(__file__).parent


@pytest.mark.parametrize("name,words", [
    ("build_map", "Build map"), ("getJobById", "Get job by id"), ("Install-Tools", "Install tools"),
    ("_private", "Private"), ("HTTPServer", "HTTP server"), ("parse_URL_v2", "Parse URL v2"),
])
def test_name_words(name, words):
    assert name_words(name) == words


@pytest.mark.parametrize("sig,params,ret", [
    ("(repo: Path, sha: str, *, only: list[str] | None = None) -> Map", ["repo", "sha", "only"], "Map"),
    ("(self, key: str) -> bytes | None", ["key"], "bytes | None"),
    ("(id: JobId, opts: { cache?: boolean } = {}): Promise<Job>", ["id", "opts"], "Promise<Job>"),
    ("(ctx Context, port int) (err error)", ["ctx", "port"], "(err error)"),
    ("<T: Runner>(&self, r: T) -> Option<T::Output> where T: Send", ["r"], "Option<T::Output>"),
    ('([string]$Version = "1.0", [switch]$Force)', ["Version", "Force"], ""),
    ("() -> None", [], ""),
])
def test_params_and_return(sig, params, ret):
    assert params_and_return(sig) == (params, ret)


def test_symbol_doc_keeps_param_order_and_sorts_names():
    n = Node(Kind.FUNCTION, "load_rows", sig="(path: str, limit: int) -> list[Row]")
    got = symbol_doc(n, calls={"b", "a", "load_rows"}, callers={"z.py:f", "y.py:g"})
    assert got == ("Load rows. Takes path, limit. Returns list[Row]. Calls a, b. "
                   "Called by y.py:g, z.py:f.")
    many = symbol_doc(n, callers={f"m{i}.py:f" for i in range(5)})
    assert many.endswith("Called by m0.py:f, m1.py:f, m2.py:f (+2 more).")


FILES = {
    "pkg/__init__.py": "",
    "pkg/core.py": (
        "from pkg import util\n"
        "from pkg.helpers import clamp\n\n"
        "def run(x: int) -> int:\n    return clamp(util.scale(x))\n\n"
        "def run_twice(x: int) -> int:\n    return run(run(x))\n\n"
        "class Store:\n"
        "    def get(self, k: str) -> str:\n        return self.load(k)\n"
        "    def load(self, k: str) -> str:\n        d = {}\n        return d.get(k)\n"
    ),
    "pkg/helpers.py": '"""Helpers."""\ndef clamp(x: int) -> int:\n    """Clamp x."""\n    return x\n',
    "pkg/util.py": "def scale(x: int) -> int:\n    return x\n\ndef get(k):\n    return k\n",
    "web/a.js": "function draw(x) { return paint(x) + helper(); }\nfunction helper() { return 1; }\n",
    "web/b.js": "function paint(x) { return x; }\n",
}


def _map(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", FILES)
    return repo, sha, build.build_map(repo, sha)


def test_call_index_resolves_imports_self_and_same_file(tmp_path):
    repo, sha, m = _map(tmp_path)
    idx = call_index(m.trees, {p: v.encode() for p, v in FILES.items()})
    assert idx.calls[("pkg/core.py", "run")] == {"helpers.py:clamp", "util.py:scale"}
    assert idx.calls[("pkg/core.py", "run_twice")] == {"run"}
    assert idx.calls[("pkg/core.py", "Store.get")] == {"Store.load"}
    # d.get(k) is a method call on an unknown receiver: not resolved to util.get
    assert ("pkg/util.py", "get") not in idx.callers
    assert idx.callers[("pkg/helpers.py", "clamp")] == {"core.py:run"}
    assert idx.calls[("web/a.js", "draw")] == {"b.js:paint", "helper"}
    assert idx.callers[("web/b.js", "paint")] == {"a.js:draw"}


def test_with_derived_docs_fills_only_missing_docs(tmp_path):
    repo, sha, m = _map(tmp_path)
    idx = call_index(m.trees, {p: v.encode() for p, v in FILES.items()})
    t = next(t for t in m.trees if t.module == "pkg")
    got = {k: n for k, n in walk(with_derived_docs(t, idx))}
    clamp = got["function:pkg/helpers.py:clamp"]
    assert clamp.doc == "Clamp x." and "derived_doc" not in dict(clamp.attrs)
    run = got["function:pkg/core.py:run"]
    assert dict(run.attrs)["derived_doc"] == "1"
    assert run.doc == ("Run. Takes x. Returns int. Calls helpers.py:clamp, util.py:scale. "
                       "Called by core.py:run_twice.")
    assert got["class:pkg/core.py:Store"].doc == "Store. Methods get, load."
    assert t == next(t for t in m.trees if t.module == "pkg")  # the Tree itself is untouched


def test_module_prose_cites_only_tree_names(tmp_path):
    for case in ("polyglot_basic", "py_basic", "cli_basic", "sql_basic", "routes_basic"):
        root = HERE / "fixtures" / case
        files = {p.relative_to(root).as_posix(): p.read_bytes()
                 for p in sorted(root.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
        repo, sha = fixture.make_repo(tmp_path / case, files)
        from rco.forest import owns_of
        for t in build.build_map(repo, sha).trees:
            o = owns_of(t)
            text = module_prose(t, o.commands, o.tables, o.routes)
            assert unknown_names(text, t) == (), (case, t.module, text)


def test_drain_writes_derived_prose_when_nothing_is_mined(tmp_path):
    repo, sha, m = _map(tmp_path)
    s = MemoryStore()
    for t in m.trees:
        s.put_node(NodeKind.TREE, t.module_hash, canon.tree_to_obj(t))
        s.enqueue(t.module, canon.tree_hash(t))
    s.index_sha(sha, tuple(IndexRow(t.module, t.module_hash, canon.tree_hash(t)) for t in m.trees))
    s.set_pointer("main", None, sha)
    drain_queue(repo, s)
    web = s.get_prose("web")
    assert web.source is ProseSource.DERIVED and web.prompt_version == "derived/1"
    assert web.text == "Defines functions draw, helper, paint."
    assert s.get_prose("pkg").source is ProseSource.MINED  # the Helpers docstring wins


def test_pack_shows_docs_and_is_byte_identical(tmp_path):
    repo, sha, _ = _map(tmp_path)
    a = pack(repo, sha, ["pkg/core.py"])
    b = pack(repo, sha, ["pkg/core.py"])
    ja = json.dumps(pack_to_obj(a), sort_keys=True)
    assert ja == json.dumps(pack_to_obj(b), sort_keys=True)
    md = pack_markdown(a, "pkg/core.py")
    assert "def clamp(x: int) -> int  # Clamp x." in md
    assert "# Run. Takes x. Returns int." in md
    assert "- web: layer 0; Defines functions draw, helper, paint." in md


def test_function_local_imports_resolve(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/conf.py": "def home():\n    return 1\n",
             "pkg/run.py": "def go():\n    from pkg.conf import home\n    return home()\n\n"
                           "def stay():\n    return home()\n"}
    repo, sha = fixture.make_repo(tmp_path / "r", files)
    m = build.build_map(repo, sha)
    idx = call_index(m.trees, {p: v.encode() for p, v in files.items()})
    assert idx.calls[("pkg/run.py", "go")] == {"conf.py:home"}
    assert idx.calls[("pkg/run.py", "stay")] == set()  # the import is local to go()
