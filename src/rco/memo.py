"""A per-run cache for pure per-file work (card 1344).

`rco replay` builds a pack at every replayed commit, and most files are the
same blob from one commit to the next. Inside `scope()`, functions that
depend only on a file's path and bytes (its extracted nodes, its import
statements) keep their result, keyed by those inputs, until the scope ends.
With no scope open, nothing is cached, so other commands behave as before.
The values kept are immutable (tuples of frozen dataclasses), so a hit returns
exactly what a fresh call would.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

T = TypeVar("T")
_tables: dict[str, dict[object, object]] | None = None


@contextmanager
def scope() -> Iterator[None]:
    """Cache per-file results until the outermost scope exits."""
    global _tables
    outer = _tables
    if outer is None:
        _tables = {}
    try:
        yield
    finally:
        if outer is None:
            _tables = None


def active() -> bool:
    return _tables is not None


def cached(table: str, key: object, compute: Callable[[], T]) -> T:
    """compute(), or its earlier result for key while a scope is active."""
    if _tables is None:
        return compute()
    t = _tables.setdefault(table, {})
    if key in t:
        return t[key]  # type: ignore[return-value]
    value = t[key] = compute()
    return value
