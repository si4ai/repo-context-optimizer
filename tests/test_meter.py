"""card 1359: without-rco counts importers of a changed public signature."""
from rco import cli, fixture, metrics
from rco.meter import (call_site_in_pack, call_sites, changed_names,
                       interface_changed, merge_graphs, public_contract,
                       read_set)
from rco.output import Exit


LIB_V1 = "def add(a: int, b: int) -> int:\n    return a + b\n"
LIB_BODY = "def add(a: int, b: int) -> int:\n    return a + b + 0\n"
LIB_SIG = "def add(a: int, b: int, c: int = 0) -> int:\n    return a + b + c\n"
LIB_PRIV = (
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n\n"
    "def _helper(x: int) -> int:\n"
    "    return x\n"
)
LIB_PRIV2 = (
    "def add(a: int, b: int) -> int:\n"
    "    return a + b\n\n"
    "def _helper(x: int, y: int = 0) -> int:\n"
    "    return x + y\n"
)
USE = (
    "from pkg.lib import add\n\n\n"
    "def run() -> int:\n"
    "    return add(1, 2)\n"
)
OTHER = "def other() -> int:\n    return 1\n"


def test_public_contract_empty_when_file_is_missing():
    assert public_contract("pkg/gone.py", None) == frozenset()
    assert public_contract("pkg/gone.py", b"") == frozenset()


def test_interface_changed_on_signature_not_body_or_private():
    assert interface_changed("pkg/lib.py", LIB_V1.encode(), LIB_SIG.encode())
    assert not interface_changed("pkg/lib.py", LIB_V1.encode(), LIB_BODY.encode())
    assert not interface_changed("pkg/lib.py", LIB_PRIV.encode(), LIB_PRIV2.encode())
    # a new file with a public name is a contract change
    assert interface_changed("pkg/lib.py", None, LIB_V1.encode())
    # a deleted public file is a contract change
    assert interface_changed("pkg/lib.py", LIB_V1.encode(), None)


def test_read_set_adds_importers_only_when_the_signature_changed():
    graph = {"pkg/use.py": ("pkg/lib.py",), "pkg/lib.py": (), "pkg/other.py": ()}
    changed, imported, importers = read_set(["pkg/lib.py"], graph, ["pkg/lib.py"])
    assert changed == ["pkg/lib.py"]
    assert imported == []
    assert importers == ["pkg/use.py"]
    # body-only: no interface path, so no importers
    changed, imported, importers = read_set(["pkg/lib.py"], graph, [])
    assert importers == []
    # the changed file itself is not also listed as an importer
    graph2 = {"pkg/lib.py": ("pkg/lib.py",), "pkg/use.py": ("pkg/lib.py",)}
    _c, _i, importers = read_set(["pkg/lib.py"], graph2, ["pkg/lib.py"])
    assert "pkg/lib.py" not in importers
    assert importers == ["pkg/use.py"]


def test_deleted_file_still_finds_importers_on_the_base_graph():
    head = {"pkg/use.py": ()}  # lib.py is gone; the import edge is only at the base
    base = {"pkg/use.py": ("pkg/lib.py",), "pkg/lib.py": ()}
    _c, _i, importers = read_set(
        ["pkg/use.py"], head, ["pkg/lib.py"], merge_graphs(head, base))
    # use.py is already in changed, so it is not also an importer
    assert importers == []
    _c, _i, importers = read_set(
        [], head, ["pkg/lib.py"], merge_graphs(head, base))
    assert importers == ["pkg/use.py"]


def test_measure_adds_importer_tokens_to_without_only():
    changed = ["pkg/lib.py"]
    graph = {"pkg/use.py": ("pkg/lib.py",), "pkg/lib.py": ()}
    blobs = {"pkg/lib.py": LIB_SIG.encode(), "pkg/use.py": USE.encode()}
    base = {"pkg/lib.py": LIB_V1.encode(), "pkg/use.py": USE.encode()}
    pack = "pack-text"
    got = metrics.measure(changed, graph, blobs, pack, metrics.estimate,
                          base_blobs=base)
    assert got["without_rco"]["files"] == 2
    assert got["without_rco"]["importer_files"] == 1
    assert got["without_rco"]["importer_tokens"] == metrics.estimate(USE)
    assert got["without_rco"]["tokens"] == (
        metrics.estimate(LIB_SIG) + metrics.estimate(USE))
    # with stays pack plus the changed file, not the importer
    assert got["with_rco"]["files"] == 2
    assert got["with_rco"]["tokens"] == metrics.estimate(pack) + metrics.estimate(LIB_SIG)
    # body-only does not add the importer
    body = metrics.measure(changed, graph, {"pkg/lib.py": LIB_BODY.encode(),
                                            "pkg/use.py": USE.encode()},
                           pack, metrics.estimate, base_blobs=base)
    assert body["without_rco"]["importer_files"] == 0
    assert body["without_rco"]["files"] == 1
    assert body["without_rco"]["tokens"] == metrics.estimate(LIB_BODY)


def test_call_sites_and_pack_coverage():
    text = "from pkg.lib import add\n\n\ndef run() -> int:\n    return add(1, 2)\n"
    assert call_sites(text, ["add", "run", "missing"]) == ("add", "run")
    # pack must mention both the importer and the name
    pack = "pkg/use.py calls add with a new argument"
    assert call_site_in_pack(pack, "pkg/use.py", "add")
    assert not call_site_in_pack("add only", "pkg/use.py", "add")
    assert not call_site_in_pack("pkg/use.py only", "pkg/use.py", "add")
    names = changed_names("pkg/lib.py", LIB_V1.encode(), LIB_SIG.encode())
    assert "add" in names


def test_measure_and_replay_agree_on_a_signature_change(tmp_path):
    repo, _base = fixture.make_repo(tmp_path / "r", {
        "pkg/__init__.py": "",
        "pkg/lib.py": LIB_V1,
        "pkg/use.py": USE,
        "pkg/other.py": OTHER,
    })
    fixture.commit(repo, {"pkg/lib.py": LIB_SIG})
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    parent = fixture.git(repo, "rev-parse", "HEAD^").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "pkg/lib.py",
                     "--out", str(pack)]) == Exit.OK
    pack_text = pack.read_text()
    name, count = metrics.counter()
    from rco import replay as replay_mod
    doc = metrics.write_metrics(repo, parent, head, 1, pack_text, 12000, count, name)
    recs = replay_mod.replay(repo, last=1, budget=12000, max_files=200,
                             count=count, tokenizer=name)
    one = recs.to_obj()["commits"][0]
    assert one["sha"] == head
    assert doc["without_rco"]["tokens"] == one["without"]["tokens"]
    assert doc["with_rco"]["tokens"] == one["with"]["tokens"]
    assert doc["without_rco"]["importer_files"] == 1
    assert one["without"]["importer_files"] == 1
    assert one["coverage"]["importers"] == 1
    assert one["coverage"]["call_sites"] >= 1
    assert one["coverage"]["call_sites_in_pack"] >= 0
