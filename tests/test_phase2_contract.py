"""Phase 2 step 0: frozen types round-trip, the fake store keeps the CAS rule, stubs import."""
import importlib
import threading

import pytest

from rco import cli
from rco.canon import canonical_json
from rco.forest_model import (Forest, ForestEntry, HopLine, Owns, Pack, Prose, ProseSource,
                              estimate_tokens, forest_from_obj, forest_hash, forest_to_obj,
                              is_stale, pack_from_obj, pack_to_obj)
from rco.model import Kind, Node, Tree
from rco.output import Exit
from rco.store import IndexRow, MemoryStore, NodeKind, Store, StoreConflict
from rco.store.sqlite import SCHEMA_SQL

H1 = "sha256:" + "1" * 64
H2 = "sha256:" + "2" * 64


def _forest() -> Forest:
    a = ForestEntry("pkg", 1, ("util",), (), ("rco",), Owns(("tool run",), ("users",), ("GET /x",)),
                    H1, Prose("Core package.", ProseSource.MINED, H1))
    b = ForestEntry("util", 0, (), ("pkg",), (), Owns(), H2)
    return Forest("a" * 40, (a, b), ("cycle: none",))


def test_forest_roundtrip_and_canonical_order():
    f = _forest()
    obj = forest_to_obj(f)
    assert forest_from_obj(obj) == f
    rev = Forest(f.sha, tuple(reversed(f.entries)), f.notes)
    assert canonical_json(forest_to_obj(rev)) == canonical_json(obj)
    assert "line" not in canonical_json(obj).decode()  # no line numbers in a Forest


def test_forest_strict_keys():
    obj = forest_to_obj(_forest())
    obj["modules"][0]["stale"] = True  # rule 3: there is no stale field
    with pytest.raises(ValueError, match="unknown keys"):
        forest_from_obj(obj)


def test_forest_hash_inputs():
    base = forest_hash(H1, ("b", "a"))
    assert base == forest_hash(H1, ("a", "b", "a"))  # sorted, unique edges
    assert base != forest_hash(H2, ("a", "b"))
    assert base != forest_hash(H1, ("a",))
    assert base != forest_hash(H1, ("a", "b"), prompt_version="p/2")
    assert base != forest_hash(H1, ("a", "b"), model_id="m")


def test_staleness_is_computed():
    p = Prose("x", ProseSource.MINED, H1)
    assert not is_stale(p, H1)
    assert is_stale(p, H2)
    assert not is_stale(None, H2)
    assert "stale" not in SCHEMA_SQL.lower()


def test_pack_roundtrip():
    t = Tree("pkg", H1, (("python", "1"),), (Node(Kind.FUNCTION, "run", "pkg/a.py", "() -> int"),))
    p = Pack("a" * 40, 8000, 12, _forest().entries, (t,), (HopLine("util", "helpers"),),
             ("big",), ("pkg",))
    assert pack_from_obj(pack_to_obj(p)) == p
    assert estimate_tokens("") == 0 and estimate_tokens("abcde") == 2


def test_memory_store_is_a_store_and_cas():
    s = MemoryStore()
    assert isinstance(s, Store)
    assert s.set_pointer("main", None, "a")
    assert not s.set_pointer("main", None, "b")
    assert not s.set_pointer("main", "x", "b")
    assert s.set_pointer("main", "a", "b") and s.get_pointer("main") == "b"


def test_memory_store_cas_race_one_winner():
    s = MemoryStore()
    s.set_pointer("main", None, "old")
    wins = []
    go = threading.Barrier(8)

    def racer(i):
        go.wait()
        if s.set_pointer("main", "old", f"new{i}"):
            wins.append(i)

    ts = [threading.Thread(target=racer, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert len(wins) == 1 and s.get_pointer("main") == f"new{wins[0]}"


def test_memory_store_content_addressed():
    s = MemoryStore()
    s.put_node(NodeKind.TREE, H1, {"a": 1})
    s.put_node(NodeKind.TREE, H1, {"a": 1})
    with pytest.raises(StoreConflict):
        s.put_node(NodeKind.TREE, H1, {"a": 2})
    assert s.get_node(NodeKind.TREE, H1) == {"a": 1}
    assert s.get_node(NodeKind.EDGES, H1) is None
    rows = (IndexRow("b", H2, H2), IndexRow("a", H1, H1))
    s.index_sha("s1", rows)
    s.index_sha("s1", tuple(reversed(rows)))
    assert s.list_by_sha("s1") == tuple(sorted(rows))
    with pytest.raises(StoreConflict):
        s.index_sha("s1", rows[:1])
    assert s.list_by_sha("nope") == ()


def test_memory_store_prose_and_queue():
    s = MemoryStore()
    s.put_prose("pkg", Prose("old", ProseSource.MINED, H1))
    s.put_prose("pkg", Prose("new", ProseSource.MINED, H2))
    assert s.get_prose("pkg").text == "new"
    assert s.get_prose("pkg", H1).text == "old"
    assert s.get_prose("other") is None
    s.enqueue("pkg", H2)
    s.enqueue("pkg", H2)
    assert s.queued() == (("pkg", H2),)
    s.dequeue("pkg", H2)
    assert s.queued() == ()


@pytest.mark.parametrize("mod", ["rco.store.sqlite", "rco.imports", "rco.forest", "rco.update",
                                 "rco.pack", "rco.prose", "rco.prose.mine", "rco.prose.check",
                                 "rco.prose.llm"])
def test_stubs_import(mod):
    importlib.import_module(mod)


@pytest.mark.parametrize("argv", [["update"], ["pack"], ["pack", "--for", "a.py", "--budget", "10"],
                                  ["forest"]])
def test_phase2_commands_exist_and_exit_2(argv, capsys):
    assert cli.main(argv) == Exit.USAGE
    assert "not implemented" in capsys.readouterr().err
