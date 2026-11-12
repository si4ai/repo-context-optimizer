"""CLI commands and flags for argparse-style parsers (tree-contract section 6).

A small static interpreter over the module's ast. It follows parser objects
through assignments, helper calls in the same file, loops over literal
command tables, `sub.choices[NAME]`, and the common "walk every subparser"
helpers that read `parser._actions`. Nothing is imported or run.

Every command, the root and each subcommand, is one top-level COMMAND node
named by its full path (`rco diff`); its options and positionals are FLAG
children. A command or flag name that is not a literal (an unknown registry,
a computed name) appears as `{expr}` and sets `dynamic=1` on its COMMAND.
A file that never names `ArgumentParser` yields no nodes.
"""
from __future__ import annotations

import ast
import json
import warnings
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import ClassVar

from rco.model import Kind, Node, SourceFile

SIG_KEYS = ("action", "choices", "metavar", "nargs", "required", "type")
DEFAULT_MAX = 40      # a default longer than this, or not a literal, becomes ...
EXPR_MAX = 80         # a non-literal sig value longer than this becomes ...
LOOP_MAX = 256        # iterations unrolled per loop
DEPTH_MAX = 32        # nested helper calls
STEP_MAX = 200_000    # statements interpreted per file


class _Unknown:
    __slots__ = ()

    def __repr__(self) -> str:
        return "?"


U = _Unknown()


@dataclass(frozen=True)
class _P:
    """A parser, or an argument group of it: both add flags to `path`."""
    path: str


@dataclass(frozen=True)
class _S:
    """The subparsers action of the parser at `path`."""
    path: str


@dataclass(frozen=True)
class _Choices:
    """`_S.choices`: the subcommands of `path` known so far."""
    path: str


@dataclass(frozen=True)
class _A:
    """One option or positional action of the parser at `path` (opaque)."""
    path: str
    name: str


@dataclass(frozen=True)
class _Fn:
    node: ast.FunctionDef | ast.AsyncFunctionDef
    env: "_Env"


@dataclass(frozen=True)
class _Ret:
    value: object


@dataclass
class _Cmd:
    doc: str = ""
    dynamic: bool = False
    flags: list[Node] = field(default_factory=list)
    subs: list[str] = field(default_factory=list)
    parent_only: bool = False


class _Budget(Exception):
    pass


class _Env:
    __slots__ = ("vars", "parent")

    def __init__(self, parent: "_Env | None" = None):
        self.vars: dict[str, object] = {}
        self.parent = parent

    def get(self, name: str) -> object:
        e: _Env | None = self
        while e is not None:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        return U

    def set(self, name: str, v: object) -> None:
        self.vars[name] = v


_LITERAL_TYPES = (str, int, float, bool, type(None))


def _known(v: object) -> bool:
    if isinstance(v, _LITERAL_TYPES):
        return True
    if isinstance(v, tuple):
        return all(_known(x) for x in v)
    if isinstance(v, dict):
        return all(_known(k) and _known(x) for k, x in v.items())
    if isinstance(v, frozenset):
        return all(_known(x) for x in v)
    return False


def _first_line(v: object) -> str:
    if not isinstance(v, str):
        return ""
    for line in v.strip().splitlines():
        return line.strip()
    return ""


def _dotted(e: ast.AST) -> str | None:
    if isinstance(e, ast.Name):
        return e.id
    if isinstance(e, ast.Attribute):
        base = _dotted(e.value)
        return None if base is None else f"{base}.{e.attr}"
    return None


def _render_default(v: object, expr: ast.AST) -> str:
    try:  # a literal in the source keeps its own spelling ([] stays a list)
        v = ast.literal_eval(expr)
        s = repr(v) if not isinstance(v, (set, dict)) else "..."
        return s if len(s) <= DEFAULT_MAX else "..."
    except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
        pass
    if _known(v) and not isinstance(v, (dict, frozenset)):
        s = repr(v)
        if len(s) <= DEFAULT_MAX:
            return s
    return "..."


def _render_sig(v: object, expr: ast.AST) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, tuple) and all(isinstance(x, _LITERAL_TYPES) for x in v):
        return json.dumps(list(v), ensure_ascii=False, separators=(",", ":"))
    if isinstance(v, _LITERAL_TYPES):
        return repr(v)
    s = ast.unparse(expr)
    return s if len(s) <= EXPR_MAX else "..."


class _Interp:
    def __init__(self, path: str, tree: ast.Module):
        self.path = path
        self.tree = tree
        self.cmds: dict[str, _Cmd] = {}
        self.roots: list[str] = []
        self.steps = 0
        self.depth = 0
        self.parser_classes = self._parser_classes(tree)
        self.dests: dict[tuple[str, str], object] = {}
        self.module = _Env()
        self.module.set("__doc__", ast.get_docstring(tree, clean=True) or U)

    # ---- setup -------------------------------------------------------
    @staticmethod
    def _parser_classes(tree: ast.Module) -> set[str]:
        found = {"ArgumentParser"}
        classes = [st for st in tree.body if isinstance(st, ast.ClassDef)]
        changed = True
        while changed:
            changed = False
            for c in classes:
                if c.name in found:
                    continue
                for b in c.bases:
                    d = _dotted(b)
                    if d is not None and d.rsplit(".", 1)[-1] in found:
                        found.add(c.name)
                        changed = True
                        break
        return found

    def run(self) -> list[Node]:
        try:
            self.exec_block(self.tree.body, self.module)
            for st in self.tree.body:
                if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    self.call_fn(_Fn(st, self.module), [], {}, entry=True)
                elif isinstance(st, ast.ClassDef):
                    for m in st.body:
                        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            self.call_fn(_Fn(m, self.module), [], {}, entry=True)
        except _Budget:
            for c in self.cmds.values():
                c.dynamic = True
        return self.nodes()

    def nodes(self) -> list[Node]:
        out: list[Node] = []
        for name, c in self.cmds.items():
            if c.parent_only:
                continue
            flags: list[Node] = []
            seen: set[Node] = set()
            for f in c.flags:
                if f not in seen:
                    seen.add(f)
                    flags.append(f)
            flags.sort(key=lambda n: (n.name, n.sig, n.attrs, n.doc))
            out.append(Node(Kind.COMMAND, name, self.path, doc=c.doc,
                            attrs=(("dynamic", "1"),) if c.dynamic else (),
                            children=tuple(flags)))
        out.sort(key=lambda n: n.name)
        return out

    def cmd(self, path: str) -> _Cmd:
        c = self.cmds.get(path)
        if c is None:
            c = self.cmds[path] = _Cmd()
        return c

    # ---- statements --------------------------------------------------
    def tick(self) -> None:
        self.steps += 1
        if self.steps > STEP_MAX:
            raise _Budget

    def exec_block(self, body: list[ast.stmt], env: _Env) -> _Ret | None:
        ret: _Ret | None = None
        for st in body:
            r = self.exec_stmt(st, env)
            if r is not None and (ret is None or r.value is not U):
                ret = r
        return ret

    def exec_stmt(self, st: ast.stmt, env: _Env) -> _Ret | None:
        self.tick()
        if isinstance(st, ast.Assign):
            v = self.ev(st.value, env)
            for t in st.targets:
                self.assign(t, v, env)
        elif isinstance(st, ast.AnnAssign):
            if st.value is not None:
                self.assign(st.target, self.ev(st.value, env), env)
        elif isinstance(st, ast.AugAssign):
            self.ev(st.value, env)
            self.assign(st.target, U, env)
        elif isinstance(st, ast.Expr):
            self.ev(st.value, env)
        elif isinstance(st, ast.Return):
            return _Ret(U if st.value is None else self.ev(st.value, env))
        elif isinstance(st, ast.If):
            t = self.truth(self.ev(st.test, env))
            r1 = self.exec_block(st.body, env) if t is not False else None
            r2 = self.exec_block(st.orelse, env) if t is not True else None
            return r1 if r1 is not None and r1.value is not U else r2 or r1
        elif isinstance(st, (ast.For, ast.AsyncFor)):
            return self.exec_for(st, env)
        elif isinstance(st, ast.While):
            self.ev(st.test, env)
            return self.exec_block(st.body, env) or self.exec_block(st.orelse, env)
        elif isinstance(st, (ast.With, ast.AsyncWith)):
            for item in st.items:
                v = self.ev(item.context_expr, env)
                if item.optional_vars is not None:
                    self.assign(item.optional_vars, U if isinstance(v, (_P, _S)) else v, env)
            return self.exec_block(st.body, env)
        elif isinstance(st, (ast.Try, getattr(ast, "TryStar", ast.Try))):
            rets = [self.exec_block(st.body, env)]
            for h in st.handlers:
                if h.name:
                    env.set(h.name, U)
                rets.append(self.exec_block(h.body, env))
            rets.append(self.exec_block(st.orelse, env))
            rets.append(self.exec_block(st.finalbody, env))
            return next((r for r in rets if r is not None and r.value is not U),
                        next((r for r in rets if r is not None), None))
        elif isinstance(st, ast.Match):
            self.ev(st.subject, env)
            for case in st.cases:
                self.exec_block(case.body, env)
        elif isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            env.set(st.name, _Fn(st, env))
        elif isinstance(st, ast.ClassDef):
            env.set(st.name, U)
        elif isinstance(st, (ast.Import, ast.ImportFrom)):
            for a in st.names:
                env.set((a.asname or a.name).split(".")[0], U)
        return None

    def exec_for(self, st: ast.For | ast.AsyncFor, env: _Env) -> _Ret | None:
        it = self.iterate(self.ev(st.iter, env))
        ret: _Ret | None = None
        if it is None:
            self.assign(st.target, U, env)
            ret = self.exec_block(st.body, env)
        else:
            for item in it[:LOOP_MAX]:
                self.assign(st.target, item, env)
                r = self.exec_block(st.body, env)
                if r is not None and (ret is None or r.value is not U):
                    ret = r
        return self.exec_block(st.orelse, env) or ret

    def assign(self, target: ast.AST, v: object, env: _Env) -> None:
        if isinstance(target, ast.Name):
            env.set(target.id, v)
        elif isinstance(target, (ast.Tuple, ast.List)):
            elts = target.elts
            if (isinstance(v, tuple) and len(v) == len(elts)
                    and not any(isinstance(e, ast.Starred) for e in elts)):
                for e, x in zip(elts, v):
                    self.assign(e, x, env)
            else:
                for e in elts:
                    self.assign(e.value if isinstance(e, ast.Starred) else e, U, env)
        elif isinstance(target, ast.Attribute):
            base = self.ev(target.value, env)
            if isinstance(base, _A):
                self.set_action_attr(base, target.attr, v)
                return
            d = _dotted(target)
            if d is not None:
                env.set(d, v)
        elif isinstance(target, ast.Starred):
            self.assign(target.value, U, env)

    # ---- values ------------------------------------------------------
    @staticmethod
    def truth(v: object) -> bool | None:
        if v is U or isinstance(v, (_Fn,)):
            return None
        if isinstance(v, (_P, _S, _Choices, _A)):
            return True
        try:
            return bool(v)
        except Exception:  # noqa: BLE001 - unknown objects stay unknown
            return None

    def iterate(self, v: object) -> list | None:
        if isinstance(v, tuple):
            return list(v)
        if isinstance(v, dict):
            return list(v.keys())
        if isinstance(v, frozenset) and all(isinstance(x, str) for x in v):
            return sorted(v)
        if isinstance(v, _Choices):
            return list(self.cmds[v.path].subs) if v.path in self.cmds else []
        return None

    def ev(self, e: ast.AST, env: _Env) -> object:
        self.tick()
        if isinstance(e, ast.Constant):
            return e.value if isinstance(e.value, _LITERAL_TYPES) else U
        if isinstance(e, ast.Name):
            return env.get(e.id)
        if isinstance(e, ast.Attribute):
            return self.ev_attr(e, env)
        if isinstance(e, (ast.Tuple, ast.List)):
            out = []
            for x in e.elts:
                if isinstance(x, ast.Starred):
                    inner = self.iterate(self.ev(x.value, env))
                    if inner is None:
                        return U
                    out.extend(inner)
                else:
                    out.append(self.ev(x, env))
            return tuple(out)
        if isinstance(e, ast.Set):
            vals = [self.ev(x, env) for x in e.elts]
            return frozenset(vals) if all(_known(x) for x in vals) else U
        if isinstance(e, ast.Dict):
            d: dict = {}
            for k, x in zip(e.keys, e.values):
                xv = self.ev(x, env)
                if k is None:
                    return U
                kv = self.ev(k, env)
                if not isinstance(kv, _LITERAL_TYPES):
                    return U
                d[kv] = xv
            return d
        if isinstance(e, ast.JoinedStr):
            parts = []
            for p in e.values:
                if isinstance(p, ast.Constant):
                    parts.append(str(p.value))
                elif isinstance(p, ast.FormattedValue) and p.format_spec is None and p.conversion == -1:
                    v = self.ev(p.value, env)
                    if not isinstance(v, (str, int)) or isinstance(v, bool):
                        return U
                    parts.append(str(v))
                else:
                    return U
            return "".join(parts)
        if isinstance(e, ast.BinOp):
            a, b = self.ev(e.left, env), self.ev(e.right, env)
            if isinstance(e.op, ast.Add) and type(a) is type(b) and isinstance(a, (str, tuple)):
                return a + b
            if (isinstance(e.op, ast.Mod) and isinstance(a, str)
                    and isinstance(b, (str, int, tuple)) and _known(b)):
                try:
                    return a % b
                except (TypeError, ValueError):
                    return U
            return U
        if isinstance(e, ast.BoolOp):
            # Python semantics: the result is an operand, not a bool
            is_and = isinstance(e.op, ast.And)
            last: object = U
            for x in e.values:
                last = self.ev(x, env)
                t = self.truth(last)
                if t is None:
                    for rest in e.values[e.values.index(x) + 1:]:
                        self.ev(rest, env)
                    return U
                if t is not is_and:
                    return last
            return last
        if isinstance(e, ast.UnaryOp):
            v = self.ev(e.operand, env)
            if isinstance(e.op, ast.Not):
                t = self.truth(v)
                return U if t is None else not t
            if isinstance(e.op, ast.USub) and isinstance(v, (int, float)) and not isinstance(v, bool):
                return -v
            return U
        if isinstance(e, ast.Compare):
            return self.ev_compare(e, env)
        if isinstance(e, ast.IfExp):
            t = self.truth(self.ev(e.test, env))
            if t is True:
                return self.ev(e.body, env)
            if t is False:
                return self.ev(e.orelse, env)
            self.ev(e.body, env)
            self.ev(e.orelse, env)
            return U
        if isinstance(e, ast.Subscript):
            base = self.ev(e.value, env)
            key = self.ev(e.slice, env)
            if isinstance(base, _Choices) and isinstance(key, str):
                return _P(f"{base.path} {key}")
            if isinstance(base, dict) and isinstance(key, _LITERAL_TYPES):
                return base.get(key, U)
            if isinstance(base, tuple) and isinstance(key, int) and not isinstance(key, bool):
                return base[key] if -len(base) <= key < len(base) else U
            return U
        if isinstance(e, ast.Call):
            return self.ev_call(e, env)
        if isinstance(e, (ast.GeneratorExp, ast.ListComp, ast.SetComp)):
            return self.ev_comp(e, env)
        if isinstance(e, ast.NamedExpr):
            v = self.ev(e.value, env)
            self.assign(e.target, v, env)
            return v
        if isinstance(e, ast.Lambda):
            return U
        for child in ast.iter_child_nodes(e):
            if isinstance(child, ast.expr):
                self.ev(child, env)
        return U

    def ev_attr(self, e: ast.Attribute, env: _Env) -> object:
        d = _dotted(e)
        if d is not None:
            v = env.get(d)
            if v is not U:
                return v
        base = self.ev(e.value, env)
        if isinstance(base, _S) and e.attr == "choices":
            return _Choices(base.path)
        if isinstance(base, _P):
            return self.parser_attr(base, e.attr)
        if isinstance(base, _A):
            return self.action_attr(base, e.attr)
        return U

    def _flag(self, a: _A) -> Node | None:
        c = self.cmds.get(a.path)
        return next((f for f in c.flags if f.name == a.name), None) if c else None

    def action_attr(self, a: _A, attr: str) -> object:
        f = self._flag(a)
        if f is None:
            return U
        if attr == "option_strings":
            return (a.name,) if a.name.startswith("-") else ()
        if attr == "dest":
            return self.dests.get((a.path, a.name), U)
        if attr in SIG_KEYS:
            sig = dict(kv.split("=", 1) for kv in f.sig.split(";") if kv)
            return sig.get(attr) if attr in ("metavar", "action") else U
        return U

    def set_action_attr(self, a: _A, attr: str, v: object) -> None:
        """`act.metavar = "X"` after the fact: rewrite that key of the flag's sig."""
        c = self.cmds.get(a.path)
        if c is None or attr not in SIG_KEYS:
            return
        for i, f in enumerate(c.flags):
            if f.name != a.name:
                continue
            sig = dict(kv.split("=", 1) for kv in f.sig.split(";") if kv)
            if v is None:
                sig.pop(attr, None)
            elif isinstance(v, str):
                sig[attr] = v
            else:
                sig[attr] = "..."
            c.flags[i] = Node(f.kind, f.name, f.path, ";".join(f"{k}={sig[k]}" for k in SIG_KEYS if k in sig),
                              f.doc, f.attrs, f.children)

    def parser_attr(self, p: _P, attr: str) -> object:
        c = self.cmds.get(p.path)
        if attr == "prog":
            return p.path
        if attr == "_actions":
            acts: list[object] = []
            if c is not None and c.subs:
                acts.append(_S(p.path))
            if c is not None:
                acts.extend(_A(p.path, f.name) for f in c.flags)
            return tuple(acts)
        if attr == "_option_string_actions":
            names = {f.name for f in c.flags if f.name.startswith("-")} if c else set()
            return frozenset(names)
        return U

    def ev_compare(self, e: ast.Compare, env: _Env) -> object:
        left = self.ev(e.left, env)
        result: object = True
        for op, rexpr in zip(e.ops, e.comparators):
            right = self.ev(rexpr, env)
            r = self.compare(op, left, right)
            if r is False:
                return False
            if r is None:
                result = U
            left = right
        return result

    def compare(self, op: ast.cmpop, a: object, b: object) -> bool | None:
        if isinstance(op, (ast.In, ast.NotIn)):
            if isinstance(b, _Choices):
                b = frozenset(self.cmds[b.path].subs if b.path in self.cmds else ())
                b = frozenset(s.rsplit(" ", 1)[-1] for s in b)
            if not (isinstance(a, _LITERAL_TYPES) and _known(b)
                    and isinstance(b, (tuple, frozenset, dict, str))):
                return None
            try:
                r = a in b
            except TypeError:
                return None
            return r if isinstance(op, ast.In) else not r
        if isinstance(op, (ast.Is, ast.IsNot)):
            if a is U or b is U:
                return None
            if isinstance(a, (_P, _S)) or isinstance(b, (_P, _S)):
                r = a == b
            elif _known(a) and _known(b) and (a is None or b is None):
                r = a is None and b is None
            else:
                return None
            return r if isinstance(op, ast.Is) else not r
        if not (_known(a) and _known(b)):
            return None
        try:
            if isinstance(op, ast.Eq):
                return a == b
            if isinstance(op, ast.NotEq):
                return a != b
        except Exception:  # noqa: BLE001
            return None
        return None

    def ev_comp(self, e, env: _Env) -> object:
        if len(e.generators) != 1:
            return U
        g = e.generators[0]
        it = self.iterate(self.ev(g.iter, env))
        if it is None:
            return U
        local = _Env(env)
        out = []
        for item in it[:LOOP_MAX]:
            self.assign(g.target, item, local)
            keep = [self.truth(self.ev(c, local)) for c in g.ifs]
            if False in keep:
                continue
            if None in keep:
                return U
            out.append(self.ev(e.elt, local))
        if isinstance(e, ast.SetComp):
            return frozenset(out) if all(_known(x) for x in out) else U
        return tuple(out)

    # ---- calls -------------------------------------------------------
    def ev_call(self, e: ast.Call, env: _Env) -> object:
        func = e.func
        obj: object = None
        meth = ""
        fval: object = U
        if isinstance(func, ast.Attribute):
            obj = self.ev(func.value, env)
            meth = func.attr
        else:
            fval = self.ev(func, env)
        args = []
        for a in e.args:
            if isinstance(a, ast.Starred):
                inner = self.iterate(self.ev(a.value, env))
                args.extend(inner if inner is not None else [U])
            else:
                args.append(self.ev(a, env))
        kwargs: dict[str, tuple[object, ast.AST]] = {}
        for k in e.keywords:
            v = self.ev(k.value, env)
            if k.arg is not None:
                kwargs[k.arg] = (v, k.value)

        name = func.id if isinstance(func, ast.Name) else meth
        if isinstance(obj, _P):
            return self.parser_method(obj, meth, e, args, kwargs)
        if isinstance(obj, _S):
            if meth == "add_parser":
                return self.add_parser(obj, e, args, kwargs)
            return U
        if isinstance(obj, _Choices):
            subs = self.cmds[obj.path].subs if obj.path in self.cmds else []
            if meth == "values":
                return tuple(_P(s) for s in subs)
            if meth == "keys":
                return tuple(s.rsplit(" ", 1)[-1] for s in subs)
            if meth == "items":
                return tuple((s.rsplit(" ", 1)[-1], _P(s)) for s in subs)
            if meth == "get" and args and isinstance(args[0], str):
                p = f"{obj.path} {args[0]}"
                return _P(p) if p in subs else (args[1] if len(args) > 1 else None)
            return U
        if isinstance(obj, dict) and meth in ("items", "keys", "values"):
            if meth == "keys":
                return tuple(obj.keys())
            if meth == "values":
                return tuple(obj.values())
            return tuple(obj.items())
        if isinstance(obj, str) and meth in ("split", "strip", "lower", "upper") and not kwargs:
            if all(isinstance(a, str) for a in args):
                try:
                    r = getattr(obj, meth)(*args)
                except (TypeError, ValueError):
                    return U
                return tuple(r) if isinstance(r, list) else r
            return U
        if isinstance(obj, frozenset) and meth == "isdisjoint" and args and _known(args[0]):
            other = args[0]
            if isinstance(other, (tuple, frozenset, dict)):
                return obj.isdisjoint(other)
            return U

        # a parser class: argparse.ArgumentParser, an alias, or a subclass in this file
        if name and (name in self.parser_classes or name.endswith("ArgumentParser")) \
                and not isinstance(fval, _Fn) and (obj is None or obj is U):
            return self.new_root(e, args, kwargs)
        if isinstance(func, ast.Name):
            return self.builtin_or_fn(func.id, fval, e, args, kwargs)
        return U

    def builtin_or_fn(self, name: str, fval: object, e: ast.Call, args, kwargs) -> object:
        if isinstance(fval, _Fn):
            if any(isinstance(a, (_P, _S, _Choices)) for a in args) or any(
                    isinstance(v, (_P, _S, _Choices)) for v, _ in kwargs.values()):
                return self.call_fn(fval, args, {k: v for k, (v, _) in kwargs.items()})
            return U
        if fval is not U:
            return U
        if name in ("sorted", "tuple", "list", "reversed") and len(args) == 1 and not kwargs:
            it = self.iterate(args[0])
            if it is None:
                return U
            if name == "sorted":
                try:
                    return tuple(sorted(it))
                except TypeError:
                    return U
            return tuple(reversed(it)) if name == "reversed" else tuple(it)
        if name in ("set", "frozenset") and len(args) == 1:
            it = self.iterate(args[0])
            return frozenset(it) if it is not None and all(_known(x) for x in it) else U
        if name in ("any", "all") and len(args) == 1:
            it = self.iterate(args[0])
            if it is None:
                return U
            ts = [self.truth(x) for x in it]
            if name == "any":
                return True if True in ts else (False if None not in ts else U)
            return False if False in ts else (True if None not in ts else U)
        if name == "isinstance" and len(args) == 2:
            cls = e.args[1]
            d = _dotted(cls) or ""
            if d.endswith("_SubParsersAction"):
                if isinstance(args[0], _S):
                    return True
                if isinstance(args[0], (_P, _Choices, _A)):
                    return False
            return U
        if name == "getattr" and len(args) >= 2 and isinstance(args[0], _P) and isinstance(args[1], str):
            v = self.parser_attr(args[0], args[1])
            return v if v is not U or len(args) < 3 else U
        if name == "dict" and not args:
            return {k: v for k, (v, _) in kwargs.items()} if all(
                _known(v) or isinstance(v, (_P, _S)) for v, _ in kwargs.values()) else U
        return U

    def call_fn(self, fn: _Fn, args: list, kwargs: dict, entry: bool = False) -> object:
        if self.depth >= DEPTH_MAX:
            return U
        a = fn.node.args
        local = _Env(fn.env)
        params = list(a.posonlyargs) + list(a.args)
        defaults = [None] * (len(params) - len(a.defaults)) + list(a.defaults)
        for i, p in enumerate(params):
            if i < len(args) and not entry:
                v = args[i]
            elif p.arg in kwargs and not entry:
                v = kwargs[p.arg]
            elif defaults[i] is not None:
                v = self.ev(defaults[i], fn.env)
                v = v if _known(v) else U
            else:
                v = U
            local.set(p.arg, v)
        for p, d in zip(a.kwonlyargs, a.kw_defaults):
            if p.arg in kwargs and not entry:
                v = kwargs[p.arg]
            elif d is not None:
                v = self.ev(d, fn.env)
                v = v if _known(v) else U
            else:
                v = U
            local.set(p.arg, v)
        if a.vararg:
            local.set(a.vararg.arg, tuple(args[len(params):]) if not entry else U)
        if a.kwarg:
            local.set(a.kwarg.arg, U)
        self.depth += 1
        try:
            r = self.exec_block(fn.node.body, local)
        finally:
            self.depth -= 1
        return U if r is None else r.value

    # ---- argparse ----------------------------------------------------
    def new_root(self, e: ast.Call, args, kwargs) -> object:
        dynamic = False
        if "prog" in kwargs:
            v, expr = kwargs["prog"]
        elif e.args and not isinstance(e.args[0], ast.Starred):
            v, expr = args[0], e.args[0]
        else:
            v, expr = PurePosixPath(self.path).name, None
        if isinstance(v, str) and v:
            name = v
        else:
            name, dynamic = "{" + ast.unparse(expr) + "}", True
        c = self.cmd(name)
        c.dynamic = c.dynamic or dynamic
        c.doc = c.doc or _first_line(kwargs.get("description", (U, None))[0])
        if name not in self.roots:
            self.roots.append(name)
        self.copy_parents(name, kwargs)
        return _P(name)

    def copy_parents(self, path: str, kwargs) -> None:
        if "parents" not in kwargs:
            return
        v, _ = kwargs["parents"]
        if not isinstance(v, tuple):
            self.cmd(path).dynamic = True
            return
        for par in v:
            if isinstance(par, _P) and par.path in self.cmds:
                pc = self.cmds[par.path]
                if par.path in self.roots:
                    pc.parent_only = True
                self.cmd(path).flags.extend(pc.flags)
            else:
                self.cmd(path).dynamic = True

    def add_parser(self, s: _S, e: ast.Call, args, kwargs) -> object:
        parent = self.cmd(s.path)
        dynamic = False
        if args and isinstance(args[0], str):
            name = args[0]
        elif "name" in kwargs and isinstance(kwargs["name"][0], str):
            name = kwargs["name"][0]
        else:
            expr = e.args[0] if e.args else kwargs.get("name", (U, ast.Constant("")))[1]
            name, dynamic = "{" + ast.unparse(expr) + "}", True
        path = f"{s.path} {name}"
        c = self.cmd(path)
        c.dynamic = c.dynamic or dynamic
        doc = _first_line(kwargs.get("help", (U, None))[0]) or _first_line(
            kwargs.get("description", (U, None))[0])
        c.doc = c.doc or doc
        if path not in parent.subs:
            parent.subs.append(path)
        self.copy_parents(path, kwargs)
        return _P(path)

    def parser_method(self, p: _P, meth: str, e: ast.Call, args, kwargs) -> object:
        if meth == "add_argument":
            self.add_argument(p, e, args, kwargs)
            return U
        if meth == "add_subparsers":
            self.cmd(p.path)
            return _S(p.path)
        if meth in ("add_argument_group", "add_mutually_exclusive_group"):
            return p
        return U

    def add_argument(self, p: _P, e: ast.Call, args, kwargs) -> None:
        c = self.cmd(p.path)
        if any(k.arg is None for k in e.keywords):  # **kwargs: the shape is not all literal
            c.dynamic = True
        strs = [a for a in args if isinstance(a, str)]
        if len(strs) != len(args) or not args:
            exprs = ", ".join(ast.unparse(a) for a in e.args) or "?"
            name = "{" + exprs + "}"
            c.dynamic = True
        elif any(s.startswith("-") for s in strs):
            longs = [s for s in strs if s.startswith("--")]
            name = longs[0] if longs else strs[0]
        else:
            dest = kwargs.get("dest", (U, None))[0]
            name = dest if isinstance(dest, str) else strs[0]
        sig = []
        for k in SIG_KEYS:
            if k in kwargs:
                v, expr = kwargs[k]
                sig.append(f"{k}={_render_sig(v, expr)}")
        attrs: tuple[tuple[str, str], ...] = ()
        if "default" in kwargs:
            attrs = (("default", _render_default(*kwargs["default"])),)
        doc = _first_line(kwargs.get("help", (U, None))[0])
        if "dest" in kwargs:
            self.dests[(p.path, name)] = kwargs["dest"][0]
        elif name.startswith("--"):
            self.dests[(p.path, name)] = name[2:].replace("-", "_")
        elif not name.startswith("-"):
            self.dests[(p.path, name)] = name
        c.flags.append(Node(Kind.FLAG, name, sig=";".join(sig), doc=doc, attrs=attrs))


class CliExtractor:
    name: ClassVar[str] = "cli"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(".py")

    def extract(self, src: SourceFile) -> list[Node]:
        if b"ArgumentParser" not in src.data:
            return []
        error = [Node(Kind.FILE, PurePosixPath(src.path).name, src.path,
                      attrs=(("error", "parse"),))]
        text = src.data.decode("utf-8-sig", errors="replace")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                tree = ast.parse(text, filename=src.path)
        except (SyntaxError, ValueError, RecursionError, MemoryError):
            return error
        try:
            return _Interp(src.path, tree).run()
        except Exception:  # noqa: BLE001 - extractors never raise (contract rule 2)
            return error
