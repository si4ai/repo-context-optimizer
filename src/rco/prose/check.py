"""Name-in-Tree check for prose (Phase 2 step 4).

Every code-shaped name the prose cites must exist in the module's Tree, or
the entry is flagged. Flags are computed at read time, like staleness.
Code-shaped: anything in `backticks`, a call `name()`, a dotted or
snake_case identifier, or a CamelCase word with an inner capital.
"""
from __future__ import annotations

import re

from rco.model import Kind, Tree, walk

_BACKTICK = re.compile(r"`([^`\s]+)`")
_CALL = re.compile(r"\b([A-Za-z_][\w.]*)\(\)")
_SNAKE = re.compile(r"\b([A-Za-z]\w*_\w+)\b")
_DOTTED = re.compile(r"\b([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+)\b")
_CAMEL = re.compile(r"\b([A-Z][a-z0-9]+[A-Z]\w*)\b")
_FILE_EXT = re.compile(r"\.(py|md|sql|toml|txt|json|yml|yaml|cfg|ini|sh)$")


_URL = re.compile(r"\S+://\S+")


def cited_names(text: str) -> tuple[str, ...]:
    text = _URL.sub(" ", text)
    names: set[str] = set()
    for rx in (_BACKTICK, _CALL, _SNAKE, _DOTTED, _CAMEL):
        for m in rx.finditer(text):
            n = m.group(1).strip("().,;:")
            if n and not _FILE_EXT.search(n) and not n.startswith(("http", "-")):
                names.add(n)
    return tuple(sorted(names))


def tree_names(tree: Tree) -> frozenset[str]:
    out: set[str] = set()
    for key, n in walk(tree):
        qn = key.split(":", 2)[2]
        out.update({n.name, qn, qn.rsplit(".", 1)[-1], qn.rsplit(" ", 1)[-1]})
        if n.kind is Kind.PYMODULE:
            parts = n.name.split(".")
            out.update(parts)
            out.update(".".join(parts[: i + 1]) for i in range(len(parts)))
        if n.kind is Kind.FLAG:
            out.add(n.name.lstrip("-"))
        if n.kind is Kind.ROUTE:  # path parameters like <int:user_id> are part of the route
            out.update(re.findall(r"[A-Za-z_]\w*", n.name))
    out.add(tree.module)
    return frozenset(x for x in out if x)


def unknown_names(text: str, tree: Tree) -> tuple[str, ...]:
    """Code-shaped names the prose cites that are not in the Tree, sorted."""
    known = tree_names(tree)
    return tuple(n for n in cited_names(text) if n not in known)
