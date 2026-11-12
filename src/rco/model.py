"""Frozen Phase 1 data model (docs/design/tree-contract.md, section 2).

Changing a type here is a step-1 follow-up; every open step hears first.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterator


class Kind(str, Enum):
    """Node kinds. Declaration order is the sort order."""

    FILE = "file"
    PYMODULE = "pymodule"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    FIELD = "field"
    CONSTANT = "constant"
    EXPORT = "export"
    COMMAND = "command"
    FLAG = "flag"
    TABLE = "table"
    VIEW = "view"
    COLUMN = "column"
    INDEX = "index"
    PROCEDURE = "procedure"
    ROUTE = "route"


KIND_ORDER: dict[Kind, int] = {k: i for i, k in enumerate(Kind)}

# Kinds whose children join the parent qualname with a space (CLI paths).
_SPACE_JOIN = frozenset({Kind.COMMAND, Kind.FLAG})


@dataclass(frozen=True, slots=True)
class Node:
    kind: Kind
    name: str
    path: str = ""
    sig: str = ""
    doc: str = ""
    attrs: tuple[tuple[str, str], ...] = ()
    children: tuple["Node", ...] = ()

    def shape(self) -> tuple[str, tuple[tuple[str, str], ...]]:
        """The contract shape: a change here is a contract change."""
        return (self.sig, self.attrs)


@dataclass(frozen=True, slots=True)
class SourceFile:
    path: str
    blob: str
    data: bytes

    def text(self) -> str:
        return self.data.decode("utf-8", errors="replace")


@dataclass(frozen=True, slots=True)
class Module:
    id: str
    files: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Tree:
    module: str
    module_hash: str
    extractors: tuple[tuple[str, str], ...]
    nodes: tuple[Node, ...]


@dataclass(frozen=True, slots=True)
class Map:
    sha: str
    trees: tuple[Tree, ...]
    notes: tuple[str, ...] = ()


def node_sort_key(n: Node) -> tuple[int, str, str, str]:
    return (KIND_ORDER[n.kind], n.name, n.path, n.sig)


def qualname(parent: str, n: Node) -> str:
    if not parent:
        return n.name
    sep = " " if n.kind in _SPACE_JOIN else "."
    return f"{parent}{sep}{n.name}"


def node_key(path: str, qn: str, kind: Kind) -> str:
    return f"{kind.value}:{path}:{qn}"


def walk(tree: Tree) -> Iterator[tuple[str, Node]]:
    """Yield (key, node) depth-first; children inherit the top-level path.

    A PYMODULE is a container: its children's qualnames do not include the
    module name, since the path already names the file.
    """

    def rec(path: str, parent_qn: str, n: Node) -> Iterator[tuple[str, Node]]:
        qn = qualname(parent_qn, n)
        yield node_key(path, qn, n.kind), n
        child_parent = "" if n.kind is Kind.PYMODULE else qn
        for c in n.children:
            yield from rec(path, child_parent, c)

    for top in tree.nodes:
        yield from rec(top.path, "", top)
