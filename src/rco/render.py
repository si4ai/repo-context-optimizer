"""Markdown rendering of a Map (deterministic; ends with a newline)."""
from __future__ import annotations

from rco import canon
from rco.model import Kind, Map, Node

_PREFIX = {
    Kind.CLASS: "class ", Kind.FUNCTION: "def ", Kind.METHOD: "def ",
    Kind.COMMAND: "CLI: ", Kind.FLAG: "", Kind.TABLE: "table ", Kind.VIEW: "view ",
    Kind.INDEX: "index ", Kind.PROCEDURE: "procedure ", Kind.ROUTE: "route ",
}


def node_line(n: Node) -> str:
    if n.kind is Kind.PYMODULE:
        return f"module {n.name}"
    if n.kind is Kind.FILE:
        extra = ",".join(f"{k}={v}" for k, v in n.attrs)
        return f"file {n.path or n.name}" + (f" ({extra})" if extra else "")
    if n.kind in (Kind.FIELD, Kind.CONSTANT, Kind.COLUMN):
        val = dict(n.attrs).get("value") or dict(n.attrs).get("default")
        s = f"{n.name}: {n.sig}" if n.sig else n.name
        return s + (f" = {val}" if val else "")
    if n.kind is Kind.EXPORT:
        return f"__all__ = {n.sig}"
    pre = _PREFIX.get(n.kind, "")
    if n.kind in (Kind.FUNCTION, Kind.METHOD) and dict(n.attrs).get("async") == "1":
        pre = "async def "
    return f"{pre}{n.name}{n.sig}" if n.kind in (Kind.FUNCTION, Kind.METHOD, Kind.CLASS, Kind.PROCEDURE) \
        else f"{pre}{n.name}" + (f" {n.sig}" if n.sig else "")


def _lines(n: Node, depth: int, out: list[str]) -> None:
    out.append("  " * depth + node_line(n))
    for c in n.children:
        _lines(c, depth + 1, out)


def map_markdown(m: Map) -> str:
    out = [f"# rco tree  (SHA={m.sha[:12]}, modules={len(m.trees)})", ""]
    for note in m.notes:
        out.append(f"note: {note}")
    for t in m.trees:
        out.append(f"## {t.module}  ({canon.tree_hash(t)[:19]})")
        for n in t.nodes:
            _lines(n, 0, out)
        out.append("")
    return "\n".join(out).rstrip("\n") + "\n"
