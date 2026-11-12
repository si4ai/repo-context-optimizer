"""Step-1 skeleton: the package imports, the registry is sorted, the CLI dispatches."""
from pathlib import Path

from rco import canon, cli, extract
from rco.model import Kind, Map, Node, Tree, walk
from rco.output import Exit


def test_registry_sorted_and_complete():
    names = [e.name for e in extract.EXTRACTORS]
    assert names == sorted(names) == ["cli", "python", "routes", "sql"]
    vers = extract.versions()
    assert vers == tuple(sorted(vers))
    assert "redact" in dict(vers)


def test_canonical_roundtrip():
    t = Tree("pkg", "sha256:" + "0" * 64, (("python", "1"),),
             (Node(Kind.PYMODULE, "pkg.core", "pkg/core.py",
                   children=(Node(Kind.FUNCTION, "run", "", "() -> int"),)),))
    m = Map("a" * 40, (t,))
    obj = canon.map_to_obj(m)
    assert canon.map_from_obj(obj) == m
    assert [k for k, _ in walk(t)] == ["pymodule:pkg/core.py:pkg.core", "function:pkg/core.py:run"]


def test_cli_usage_exit_2(capsys):
    assert cli.main(["nope"]) == Exit.USAGE
    assert cli.main(["check"]) == Exit.USAGE

def test_readme_names_landed_diff_and_output_contract():
    """D3: Planned commands must not call rco diff or the output contract unimplemented."""
    text = (Path(__file__).resolve().parents[1] / "README.md").read_text(encoding="utf-8")
    assert "Nothing below is implemented yet" not in text
    planned = text.split("## Planned commands", 1)[1].split("\n## ", 1)[0]
    assert "rco diff" in planned and "have landed" in planned
    assert "output contract" in planned.lower()
    assert "rco pack" in planned and "not implemented yet" in planned
