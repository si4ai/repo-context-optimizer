"""Store conformance suite (forest-contract section 5). Every backend runs it.

Call run_all(make) with a zero-argument factory that returns a fresh, empty
store. Each check raises AssertionError on a failure.
"""
from __future__ import annotations

import threading
from typing import Callable

from rco.forest_model import Prose, ProseSource
from rco.store import IndexRow, NodeKind, Store, StoreConflict

H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64


def _raises(exc, fn, *a) -> None:
    try:
        fn(*a)
    except exc:
        return
    raise AssertionError(f"{fn.__name__} did not raise {exc.__name__}")


def check_is_store(s: Store) -> None:
    assert isinstance(s, Store)


def check_nodes(s: Store) -> None:
    assert s.get_node(NodeKind.TREE, H1) is None
    s.put_node(NodeKind.TREE, H1, {"b": [1, 2], "a": "x"})
    s.put_node(NodeKind.TREE, H1, {"a": "x", "b": [1, 2]})  # same canonical bytes
    _raises(StoreConflict, s.put_node, NodeKind.TREE, H1, {"a": "y"})
    assert s.get_node(NodeKind.TREE, H1) == {"a": "x", "b": [1, 2]}
    assert s.get_node(NodeKind.EDGES, H1) is None  # kinds do not share keys
    s.put_node(NodeKind.EDGES, H1, {"depends_on": []})
    assert s.get_node(NodeKind.EDGES, H1) == {"depends_on": []}


def check_index(s: Store) -> None:
    rows = (IndexRow("b", H2, H2), IndexRow("a", H1, H1))
    assert s.list_by_sha("s1") == ()
    s.index_sha("s1", rows)
    s.index_sha("s1", tuple(reversed(rows)))
    assert s.list_by_sha("s1") == tuple(sorted(rows))
    _raises(StoreConflict, s.index_sha, "s1", rows[:1])
    assert s.list_by_sha("s1") == tuple(sorted(rows))


def check_pointer_cas(s: Store) -> None:
    assert s.get_pointer("main") is None
    assert not s.set_pointer("main", "x", "a")  # absent pointer is not "x"
    assert s.set_pointer("main", None, "a")
    assert not s.set_pointer("main", None, "b")  # first set only once
    assert not s.set_pointer("main", "x", "b")
    assert s.get_pointer("main") == "a"
    assert s.set_pointer("main", "a", "b") and s.get_pointer("main") == "b"
    assert s.get_pointer("other") is None


def check_pointer_threads(s: Store) -> None:
    s.set_pointer("race", None, "old")
    wins: list[int] = []
    go = threading.Barrier(8)

    def racer(i: int) -> None:
        go.wait()
        if s.set_pointer("race", "old", f"new{i}"):
            wins.append(i)

    ts = [threading.Thread(target=racer, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(wins) == 1 and s.get_pointer("race") == f"new{wins[0]}"


def check_prose(s: Store) -> None:
    a = Prose("old", ProseSource.MINED, H1)
    b = Prose("new", ProseSource.MINED, H2)
    assert s.get_prose("pkg") is None
    s.put_prose("pkg", a)
    s.put_prose("pkg", b)
    s.put_prose("pkg", a)  # same prose again: no-op, b stays newest
    assert s.get_prose("pkg") == b
    assert s.get_prose("pkg", H1) == a
    assert s.get_prose("pkg", "sha256:none") is None
    a2 = Prose("rewritten", ProseSource.MINED, H1)
    s.put_prose("pkg", a2)  # rewrite replaces and becomes newest
    assert s.get_prose("pkg") == a2 and s.get_prose("pkg", H1) == a2


def check_queue(s: Store) -> None:
    assert s.queued() == ()
    s.enqueue("pkg", H2)
    s.enqueue("pkg", H2)
    s.enqueue("a", H1)
    assert s.queued() == (("a", H1), ("pkg", H2))
    s.dequeue("pkg", H2)
    s.dequeue("pkg", H2)
    assert s.queued() == (("a", H1),)


def check_notice(s: Store) -> None:
    s.post_notice("stale Forest: pkg")  # must not raise


CHECKS: tuple[Callable[[Store], None], ...] = (
    check_is_store, check_nodes, check_index, check_pointer_cas, check_pointer_threads,
    check_prose, check_queue, check_notice,
)


def run_all(make: Callable[[], Store]) -> None:
    for chk in CHECKS:
        chk(make())
