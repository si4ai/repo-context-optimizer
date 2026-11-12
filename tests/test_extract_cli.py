"""CLI extractor: commands and flags match what argparse really builds.

The parity tests build the live parser (rco's own cli.py, the fixture) and
compare its command tree with the static extract, so they keep holding as
those parsers change.
"""
import argparse
import importlib.util
import os
from pathlib import Path

import pytest

from rco import build, canon, check, fixture
from rco.extract.cli import CliExtractor
from rco.model import Kind, Module, Node, SourceFile, walk

HERE = Path(__file__).parent
ROOT = HERE.parent
FIX = HERE / "fixtures" / "cli_basic"
EX = CliExtractor()


def _extract(src: str, path: str = "pkg/cli.py") -> list[Node]:
    return EX.extract(SourceFile(path, "0" * 40, src.encode()))


def _by_name(nodes: list[Node]) -> dict[str, Node]:
    return {n.name: n for n in nodes}


def _sig(n: Node) -> dict[str, str]:
    return dict(kv.split("=", 1) for kv in n.sig.split(";") if kv)


def _live(parser: argparse.ArgumentParser) -> dict[str, dict[str, argparse.Action]]:
    """{command path: {flag name: action}} from a built parser."""
    out: dict[str, dict[str, argparse.Action]] = {}

    def rec(p: argparse.ArgumentParser, path: str) -> None:
        flags = {}
        for a in p._actions:
            if isinstance(a, argparse._SubParsersAction):
                for name, child in a.choices.items():
                    rec(child, f"{path} {name}")
            elif not isinstance(a, argparse._HelpAction):
                longs = [s for s in a.option_strings if s.startswith("--")]
                name = (longs or a.option_strings or [a.dest])[0]
                flags[name] = a
        out[path] = flags

    rec(parser, parser.prog)
    return out


def _assert_parity(parser: argparse.ArgumentParser, nodes: list[Node]) -> None:
    live = _live(parser)
    static = {n.name: {c.name: c for c in n.children} for n in nodes}
    assert sorted(static) == sorted(live)
    for cmd, flags in live.items():
        assert sorted(static[cmd]) == sorted(flags), cmd
        for name, act in flags.items():
            sig = _sig(static[cmd][name])
            assert sig.get("metavar") == act.metavar, (cmd, name)
            if act.choices is not None:
                assert "choices" in sig, (cmd, name)
            default = dict(static[cmd][name].attrs).get("default")
            if default not in (None, "..."):
                assert default == repr(act.default), (cmd, name)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rco_own_cli_matches_its_live_parser():
    from rco import cli
    path = ROOT / "src" / "rco" / "cli.py"
    nodes = EX.extract(SourceFile("src/rco/cli.py", "0" * 40, path.read_bytes()))
    _assert_parity(cli.make_parser(), nodes)
    tree = _by_name(nodes)
    assert {"rco", "rco tree", "rco check", "rco diff"} <= set(tree)
    out = _by_name(tree["rco diff"].children)["--out"]
    assert out.sig == "type=Path" and dict(out.attrs) == {"default": "None"}


def test_fixture_matches_its_live_parser():
    mod = _load(FIX / "tool" / "cli.py", "cli_basic_fixture")
    nodes = EX.extract(SourceFile("tool/cli.py", "0" * 40, (FIX / "tool" / "cli.py").read_bytes()))
    _assert_parity(mod.make_parser(), nodes)


def test_patterns_helpers_tables_choices_walkers_parents():
    tree = _by_name(EX.extract(SourceFile("tool/cli.py", "0" * 40,
                                          (FIX / "tool" / "cli.py").read_bytes())))
    # subclass of ArgumentParser, description first line as doc
    assert tree["tool"].doc == "Fixture tool."
    run = _by_name(tree["tool run"].children)
    # parents=[shared] copies flags; the parent-only parser is not a command
    assert "--quiet" in run and "cli.py" not in tree
    # helper call in the same file, module constant as help
    assert run["--out"].sig == "metavar=FILE" and run["--out"].doc == "write the report to FILE"
    assert run["--format"].sig == 'choices=["markdown","json"]'
    assert run["--retries"].sig == "required=False;type=int"
    # loop over a literal tuple, with an if on the loop variable
    assert "--force" in _by_name(tree["tool stop"].children)
    assert "--force" not in _by_name(tree["tool start"].children)
    # loop over a dict registry with tuple unpacking; f-string help
    assert tree["tool show"].doc == "print one item" and tree["tool start"].doc == "start the service"
    assert _sig(_by_name(tree["tool show"].children)["item"]) == {"metavar": "ID"}
    # sub.choices[NAME] reaches an existing subcommand
    assert "--limit" in _by_name(tree["tool list"].children)
    # nested subparsers; dest on an option does not rename it
    assert "--to" in _by_name(tree["tool db migrate"].children)
    # recursive walker over parser._actions adds --yes to leaves only
    assert "--yes" in _by_name(tree["tool db migrate"].children)
    assert "--yes" not in _by_name(tree["tool db"].children)
    assert "--yes" not in _by_name(tree["tool db version"].children)
    assert not any(dict(n.attrs).get("dynamic") for n in tree.values())


def test_full_command_path_keys():
    t = build.build_tree(Module("tool", ("tool/cli.py",)),
                         [SourceFile("tool/cli.py", "0" * 40, (FIX / "tool" / "cli.py").read_bytes())])
    keys = {k for k, _ in walk(t)}
    assert "command:tool/cli.py:tool db migrate" in keys
    assert "flag:tool/cli.py:tool db migrate --to" in keys
    assert "flag:tool/cli.py:tool run job" in keys


def test_no_prog_names_the_command_after_the_file():
    (n,) = EX.extract(SourceFile("scripts/plain.py", "0" * 40,
                                 (FIX / "scripts" / "plain.py").read_bytes()))
    assert n.name == "plain.py" and n.path == "scripts/plain.py"
    assert n.doc == "A script with no prog: the command is named after the file."
    assert dict(_by_name(n.children)["paths"].attrs) == {"default": "[]"}


def test_unknown_registry_is_dynamic():
    nodes = _by_name(_extract(
        "import argparse\n"
        "def main(plugins):\n"
        "    p = argparse.ArgumentParser(prog='tool')\n"
        "    sub = p.add_subparsers()\n"
        "    for name in plugins:\n"
        "        sp = sub.add_parser(name)\n"
        "        sp.add_argument('--x')\n"
        "    p.add_argument(make_flag())\n"
    ))
    assert set(nodes) == {"tool", "tool {name}"}
    assert dict(nodes["tool {name}"].attrs) == {"dynamic": "1"}
    assert [c.name for c in nodes["tool {name}"].children] == ["--x"]
    assert dict(nodes["tool"].attrs) == {"dynamic": "1"}
    assert [c.name for c in nodes["tool"].children] == ["{make_flag()}"]


def test_literal_flag_values_and_defaults():
    (n,) = _extract(
        "import argparse\nN = 7\n"
        "p = argparse.ArgumentParser(prog='t')\n"
        "p.add_argument('-n', '--num', type=int, default=N, nargs=2, help='how many\\nmore')\n"
        "p.add_argument('--mode', choices=['a', 'b'], default='a' * 50)\n"
        "p.add_argument('--rest', nargs=argparse.REMAINDER)\n"
        "p.add_argument('-s', action='count')\n"
    )
    f = _by_name(n.children)
    assert f["--num"].sig == "nargs=2;type=int" and dict(f["--num"].attrs) == {"default": "7"}
    assert f["--num"].doc == "how many"
    assert f["--mode"].sig == 'choices=["a","b"]' and dict(f["--mode"].attrs) == {"default": "..."}
    assert f["--rest"].sig == "nargs=argparse.REMAINDER"
    assert f["-s"].sig == "action=count"


def test_no_argument_parser_no_nodes():
    assert _extract("def add_argument(x):\n    return x\n") == []
    assert _extract("x = 1\n", "pkg/helper.py") == []


def test_parse_error_is_a_file_node_not_an_exception():
    (n,) = _extract("import argparse\nargparse.ArgumentParser(\n", "pkg/bad.py")
    assert n == Node(Kind.FILE, "bad.py", "pkg/bad.py", attrs=(("error", "parse"),))


@pytest.mark.parametrize("src", [
    "import argparse\ndef f(p):\n    f(p)\nf(argparse.ArgumentParser(prog='t'))\n",
    "import argparse\np = argparse.ArgumentParser(prog='t')\n"
    "for i in range(10**9):\n    p.add_argument(i)\n",
    "import argparse\np = argparse.ArgumentParser(prog='t')\nwhile True:\n    p = p\n",
    "import argparse\np = argparse.ArgumentParser(prog=1)\np.add_argument(*xs, **kw)\n",
    "import argparse\nclass A(argparse.ArgumentParser): pass\nA(parents=x).add_argument('--a')\n",
])
def test_odd_input_never_raises(src):
    out = _extract(src)
    assert all(isinstance(n, Node) for n in out)
    assert out == _extract(src)


def test_pure_and_position_free():
    src = (FIX / "tool" / "cli.py").read_text()
    a = _extract(src)
    assert a == _extract(src)
    shifted = _extract("\n\n\n" + src.replace("\n\n\ndef ", "\n\n\n\n\ndef "))
    assert shifted == a


def test_selftest_plants_flag_drift_on_a_real_flag():
    with fixture.drift_repo() as (repo, sha):
        m = build.build_map(repo, sha)
    flags = [n for t in m.trees for _, n in walk(t) if n.kind is Kind.FLAG]
    assert any(n.name == "--out" for n in flags), "drift fixture needs a real CLI flag"
    assert check.selftest_on(m).exit_code() == 0


def test_golden(tmp_path):
    files = {p.relative_to(FIX).as_posix(): p.read_bytes()
             for p in sorted(FIX.rglob("*")) if p.is_file() and "__pycache__" not in p.parts}
    repo, sha = fixture.make_repo(tmp_path / "cli_basic", files)
    got = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    golden = HERE / "golden" / "cli_basic.json"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()
