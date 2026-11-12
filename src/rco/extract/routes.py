"""HTTP routes from Python decorators, route tables and handler dispatch (step 3).

One ROUTE node per method, named `METHOD /path`, with the handler's first
docstring line as doc. The path is the string literal as written, so
`{id}`, `<int:id>` and `:id` stay as the framework spells them. Only
literal paths that start with `/` count. Three styles are read:

- Decorators: `@x.get("/p")` and the other HTTP verbs, `@x.route("/p",
  methods=[...])` (Flask, default GET), `@x.api_route(...)` (FastAPI,
  default GET), and `@x.websocket("/p")` (method `WS`).
- Route tables: `Route("/p", ep, methods=[...])` (Starlette, default GET),
  `WebSocketRoute("/p", ep)`, `x.add_url_rule("/p", ...)`,
  `x.add_api_route("/p", ...)`, `x.add_get("/p", h)` and the other
  `add_<verb>` calls, `x.add_route("GET", "/p", h)` (aiohttp) and
  `web.get("/p", h)`.
- Handler dispatch: inside `do_GET`, `do_POST` and the other `do_<VERB>`
  methods of a `BaseHTTPRequestHandler`-style class, and the methods of
  that class they reach through `self.<name>(...)` calls, `p == "/x"` and
  `p in ("/x", "/y")` comparisons, with the verb from the `do_` name.

`methods=` may be a literal list, tuple or set of strings, or a name bound
to one in the same function or at module level. Anything else gives the
method `ANY`. Mount points, router prefixes, `startswith` prefixes and
computed paths are not resolved yet, so a route is the literal as written.
A test client call such as `client.get("/p")` is not a route.

Pure and never raises: a file that does not parse gives one FILE node with
attrs error=parse (the python extractor gives the same node; build.py
keeps one). A file with no quote directly followed by `/` cannot hold a
route and is not parsed; it gives no nodes.
"""
from __future__ import annotations

import ast
import re
from pathlib import PurePosixPath
from typing import ClassVar, Iterator

from rco.extract.python import first_doc_line
from rco.model import Kind, Node, SourceFile

VERBS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
_ANY = "ANY"

# decorator or call name -> its methods when methods= is absent
_DECORATORS = {**{v: (v.upper(),) for v in VERBS},
               "route": ("GET",), "api_route": ("GET",),
               "websocket": ("WS",), "websocket_route": ("WS",)}
_TABLE_CALLS = {"Route": ("GET",), "WebSocketRoute": ("WS",),
                "add_url_rule": ("GET",), "add_api_route": ("GET",),
                "add_api_websocket_route": ("WS",), "add_websocket_route": ("WS",),
                **{f"add_{v}": (v.upper(),) for v in VERBS}}
_DO_PREFIX = "do_"
_QUOTED_SLASH = re.compile(r"[\"']/")


def _str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _route_path(node: ast.AST | None) -> str | None:
    s = _str(node)
    return s if s is not None and s.startswith("/") else None


def _call_name(func: ast.AST) -> str:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _kw(call: ast.Call, name: str) -> ast.AST | None:
    for k in call.keywords:
        if k.arg == name:
            return k.value
    return None


class _Scopes:
    """Literal string-sequence bindings per function scope and at module level."""

    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.module: dict[str, tuple[str, ...] | None] | None = None
        self.funcs: dict[int, dict[str, tuple[str, ...] | None]] = {}

    @staticmethod
    def _bindings(body: list[ast.stmt]) -> dict[str, tuple[str, ...] | None]:
        out: dict[str, tuple[str, ...] | None] = {}
        for st in body:
            if isinstance(st, ast.Assign):
                targets, value = st.targets, st.value
            elif isinstance(st, ast.AnnAssign) and st.value is not None:
                targets, value = [st.target], st.value
            else:
                continue
            seq = _str_seq(value)
            for t in targets:
                if isinstance(t, ast.Name):
                    # a second, different binding makes the name unknown
                    out[t.id] = seq if t.id not in out or out[t.id] == seq else None
        return out

    def resolve(self, name: str, stack: list[ast.AST]) -> tuple[str, ...] | None:
        for fn in reversed(stack):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if id(fn) not in self.funcs:
                self.funcs[id(fn)] = self._bindings(fn.body)
            b = self.funcs[id(fn)]
            if name in b:
                return b[name]
        if self.module is None:
            self.module = self._bindings(self.tree.body)
        return self.module.get(name)


def _str_seq(node: ast.AST | None) -> tuple[str, ...] | None:
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        vals = [_str(e) for e in node.elts]
        if all(v is not None for v in vals):
            return tuple(vals)  # type: ignore[arg-type]
    return None


def _methods(node: ast.AST | None, default: tuple[str, ...], scopes: _Scopes,
             stack: list[ast.AST]) -> tuple[str, ...]:
    if node is None:
        return default
    seq = _str_seq(node)
    if seq is None and isinstance(node, ast.Name):
        seq = scopes.resolve(node.id, stack)
    if not seq:
        return (_ANY,)
    return tuple(sorted({m.upper() for m in seq}))


def _func_docs(tree: ast.Module) -> dict[str, str]:
    """Module-level and nested function name -> first docstring line (first def wins)."""
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name not in out:
            out[node.name] = first_doc_line(node)
    return out


def _endpoint_doc(node: ast.AST | None, docs: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        return docs.get(node.id, "")
    if isinstance(node, ast.Attribute):
        return docs.get(node.attr, "")
    return ""


def _dispatch_paths(fn: ast.AST) -> Iterator[str]:
    """Literal paths compared with == or `in` inside a do_<VERB> method."""
    for node in ast.walk(fn):
        if not isinstance(node, ast.Compare):
            continue
        operands = [node.left, *node.comparators]
        for op, a, b in zip(node.ops, operands, operands[1:]):
            if isinstance(op, ast.Eq):
                for lit, other in ((a, b), (b, a)):
                    p = _route_path(lit)
                    if p is not None and isinstance(other, (ast.Name, ast.Attribute)):
                        yield p
            elif isinstance(op, ast.In) and isinstance(a, (ast.Name, ast.Attribute)) \
                    and isinstance(b, (ast.Tuple, ast.List, ast.Set)):
                for e in b.elts:
                    p = _route_path(e)
                    if p is not None:
                        yield p


def _self_reach(fn: ast.AST, cls: ast.ClassDef) -> list[ast.AST]:
    """fn plus the methods of cls it reaches through self.<name>(...) calls."""
    methods = {st.name: st for st in cls.body
               if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef))}
    out, todo, seen = [], [fn], {id(fn)}
    while todo:
        cur = todo.pop(0)
        out.append(cur)
        for node in ast.walk(cur):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                    and isinstance(node.func.value, ast.Name) and node.func.value.id == "self":
                target = methods.get(node.func.attr)
                if target is not None and id(target) not in seen:
                    seen.add(id(target))
                    todo.append(target)
    return out


def routes(path: str, tree: ast.Module) -> list[Node]:
    scopes = _Scopes(tree)
    _docs: list[dict[str, str]] = []

    def docs() -> dict[str, str]:
        if not _docs:
            _docs.append(_func_docs(tree))
        return _docs[0]

    found: list[tuple[str, str]] = []     # (name, doc), in walk order

    def add(methods: tuple[str, ...], route: str, doc: str) -> None:
        for m in methods:
            found.append((f"{m} {route}", doc))

    def visit(node: ast.AST, stack: list[ast.AST]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) \
                        and dec.func.attr in _DECORATORS:
                    route = _route_path(dec.args[0] if dec.args else _kw(dec, "path")
                                        or _kw(dec, "rule"))
                    if route is not None:
                        ms = _DECORATORS[dec.func.attr]
                        if dec.func.attr in ("route", "api_route"):
                            ms = _methods(_kw(dec, "methods"), ms, scopes, stack)
                        add(ms, route, first_doc_line(node))
            if node.name.startswith(_DO_PREFIX) and node.name[3:].isupper() and stack \
                    and isinstance(stack[-1], ast.ClassDef):
                verb = node.name[3:]
                for fn in _self_reach(node, stack[-1]):
                    for route in _dispatch_paths(fn):
                        add((verb,), route, "")
            stack = stack + [node]
        elif isinstance(node, ast.ClassDef):
            stack = stack + [node]
        elif isinstance(node, ast.Call):
            _table_call(node, stack)
        for child in ast.iter_child_nodes(node):
            visit(child, stack)

    def _table_call(call: ast.Call, stack: list[ast.AST]) -> None:
        name = _call_name(call.func)
        fn_stack = [s for s in stack if not isinstance(s, ast.ClassDef)]
        if name == "add_route" and len(call.args) >= 2:
            verb, route = _str(call.args[0]), _route_path(call.args[1])
            if verb is not None and route is not None:
                ep = call.args[2] if len(call.args) > 2 else _kw(call, "handler")
                ms = (_ANY,) if verb == "*" else (verb.upper(),)
                add(ms, route, _endpoint_doc(ep, docs()))
            return
        if name in VERBS and isinstance(call.func, ast.Attribute) \
                and isinstance(call.func.value, ast.Name) and call.func.value.id == "web":
            route = _route_path(call.args[0] if call.args else None)
            if route is not None:
                ep = call.args[1] if len(call.args) > 1 else _kw(call, "handler")
                add((name.upper(),), route, _endpoint_doc(ep, docs()))
            return
        if name not in _TABLE_CALLS:
            return
        route = _route_path(call.args[0] if call.args else _kw(call, "path") or _kw(call, "rule"))
        if route is None:
            return
        ep = call.args[1] if len(call.args) > 1 else (
            _kw(call, "endpoint") or _kw(call, "view_func") or _kw(call, "handler"))
        default = _TABLE_CALLS[name]
        ms = default
        if name in ("Route", "add_url_rule", "add_api_route"):
            ms = _methods(_kw(call, "methods"), default, scopes, fn_stack)
        add(ms, route, _endpoint_doc(ep, docs()))

    visit(tree, [])
    out: dict[str, Node] = {}
    for name, doc in found:
        prev = out.get(name)
        if prev is None or (not prev.doc and doc):
            out[name] = Node(Kind.ROUTE, name, path, doc=doc)
    return list(out.values())


class RoutesExtractor:
    name: ClassVar[str] = "routes"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(".py")

    def extract(self, src: SourceFile) -> list[Node]:
        error = [Node(Kind.FILE, PurePosixPath(src.path).name, src.path,
                      attrs=(("error", "parse"),))]
        text = src.data.decode("utf-8-sig", errors="replace")
        if not _QUOTED_SLASH.search(text):
            return []       # no string literal starts with "/", so no route
        try:
            tree = ast.parse(text, filename=src.path)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return error
        try:
            return routes(src.path, tree)
        except RecursionError:
            return error
