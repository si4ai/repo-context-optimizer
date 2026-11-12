"""Python code contracts via ast (tree-contract section 6).

Public classes, functions and methods with signatures, type hints and the
first docstring line; UPPER_CASE constants; annotated class fields; and
__all__. Bodies, imports and private names are dropped. Only top-level
statements of a module and of a public class are read, so a definition
under `if` or `try` is not part of the Tree.
"""
from __future__ import annotations

import ast
import copy
import json
from pathlib import PurePosixPath
from typing import ClassVar

from rco.model import Kind, Node, SourceFile

DEFAULT_MAX = 40   # a default longer than this, or not a literal, becomes ...
VALUE_MAX = 80     # same for constant and field values


def is_public(name: str) -> bool:
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


def module_name(path: str) -> str:
    parts = list(PurePosixPath(path).with_suffix("").parts)
    if parts and parts[0] == "src":
        parts = parts[1:]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts) or "."


def first_doc_line(node) -> str:
    try:
        doc = ast.get_docstring(node, clean=True)
    except TypeError:
        return ""
    if not doc:
        return ""
    lines = doc.strip().splitlines()
    return lines[0].strip() if lines else ""


def _is_literal(node: ast.AST) -> bool:
    try:
        ast.literal_eval(node)
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        return False
    return True


def literal_or_ellipsis(node: ast.AST | None, limit: int) -> str:
    if node is None:
        return ""
    if _is_literal(node):
        s = ast.unparse(node)
        if len(s) <= limit:
            return s
    return "..."


def _decorators(node) -> tuple[tuple[str, str], ...]:
    if not node.decorator_list:
        return ()
    return (("decorators", ", ".join(ast.unparse(d) for d in node.decorator_list)),)


def func_sig(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    args = copy.deepcopy(fn.args)
    args.defaults = [d if literal_or_ellipsis(d, DEFAULT_MAX) != "..." else ast.Constant(...)
                     for d in args.defaults]
    args.kw_defaults = [None if d is None
                        else (d if literal_or_ellipsis(d, DEFAULT_MAX) != "..." else ast.Constant(...))
                        for d in args.kw_defaults]
    sig = f"({ast.unparse(args)})"
    if fn.returns is not None:
        sig += f" -> {ast.unparse(fn.returns)}"
    tps = getattr(fn, "type_params", None)
    if tps:
        sig = "[" + ", ".join(ast.unparse(t) for t in tps) + "]" + sig
    return sig


def _func(fn, kind: Kind) -> Node:
    attrs = list(_decorators(fn))
    if isinstance(fn, ast.AsyncFunctionDef):
        attrs.append(("async", "1"))
    return Node(kind, fn.name, sig=func_sig(fn), doc=first_doc_line(fn), attrs=tuple(sorted(attrs)))


def _class(cls: ast.ClassDef) -> Node:
    bases = [ast.unparse(b) for b in cls.bases] + [ast.unparse(k) for k in cls.keywords]
    sig = f"({', '.join(bases)})" if bases else ""
    tps = getattr(cls, "type_params", None)
    if tps:
        sig = "[" + ", ".join(ast.unparse(t) for t in tps) + "]" + sig
    return Node(Kind.CLASS, cls.name, sig=sig, doc=first_doc_line(cls),
                attrs=_decorators(cls), children=tuple(_members(cls.body, in_class=True)))


def _assign_nodes(st: ast.Assign | ast.AnnAssign, in_class: bool) -> list[Node]:
    targets = st.targets if isinstance(st, ast.Assign) else [st.target]
    names = [t.id for t in targets if isinstance(t, ast.Name)]
    ann = ast.unparse(st.annotation) if isinstance(st, ast.AnnAssign) else ""
    out: list[Node] = []
    for name in names:
        if name == "__all__" and not in_class:
            out.append(Node(Kind.EXPORT, name, sig=_exports(st.value)))
        elif name.isupper() and is_public(name):
            val = literal_or_ellipsis(st.value, VALUE_MAX)
            out.append(Node(Kind.CONSTANT, name, sig=ann,
                            attrs=(("value", val),) if val else ()))
        elif in_class and isinstance(st, ast.AnnAssign) and is_public(name):
            val = literal_or_ellipsis(st.value, VALUE_MAX)
            out.append(Node(Kind.FIELD, name, sig=ann, attrs=(("value", val),) if val else ()))
    return out


def _exports(value: ast.AST | None) -> str:
    if isinstance(value, (ast.List, ast.Tuple)) and all(
            isinstance(e, ast.Constant) and isinstance(e.value, str) for e in value.elts):
        return json.dumps([e.value for e in value.elts])
    return "..."


def _members(stmts: list[ast.stmt], in_class: bool) -> list[Node]:
    out: list[Node] = []
    fkind = Kind.METHOD if in_class else Kind.FUNCTION
    for st in stmts:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_public(st.name):
                out.append(_func(st, fkind))
        elif isinstance(st, ast.ClassDef):
            if is_public(st.name):
                out.append(_class(st))
        elif isinstance(st, (ast.Assign, ast.AnnAssign)):
            out.extend(_assign_nodes(st, in_class))
    return out


class PythonExtractor:
    name: ClassVar[str] = "python"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(".py")

    def extract(self, src: SourceFile) -> list[Node]:
        text = src.data.decode("utf-8-sig", errors="replace")
        try:
            tree = ast.parse(text, filename=src.path)
            return [Node(Kind.PYMODULE, module_name(src.path), src.path,
                         doc=first_doc_line(tree),
                         children=tuple(_members(tree.body, in_class=False)))]
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return [Node(Kind.FILE, PurePosixPath(src.path).name, src.path,
                         attrs=(("error", "parse"),))]
