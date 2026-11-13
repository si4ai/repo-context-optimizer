"""rco plan (MP): simple merge planning from Tree and Forest data, no LLM."""
import json

import pytest

from rco import cli, fixture, plan
from rco.output import Exit, UsageError

BASE = {
    "pyproject.toml": '[project]\nname = "x"\n',
    "core/__init__.py": "",
    "core/io.py": "def load(path: str) -> str:\n    return path\n\n\nLIMIT = 3\n",
    "app/__init__.py": "",
    "app/main.py": "from core.io import load\n\n\ndef main() -> str:\n    return load('a')\n",
    "app/extra.py": "from core import io\n\n\ndef side() -> int:\n    return 1\n",
    "tools/__init__.py": "",
    "tools/fmt.py": "def fmt(s: str) -> str:\n    return s\n",
    "tests/test_io.py": "from core.io import load\n\n\ndef test_load():\n    assert load('x') == 'x'\n",
    "tests/test_main.py": "from app.main import main\n\n\ndef test_main():\n    assert main() == 'a'\n",
    "tests/test_fmt.py": "from tools.fmt import fmt\n\n\ndef test_fmt():\n    assert fmt('') == ''\n",
}


def _setup(tmp_path):
    """sig changes load(); uses adds a call to load(); dep edits a module importing core;
    lone touches tools only."""
    repo, base = fixture.make_repo(tmp_path / "r", BASE)
    fixture.git(repo, "switch", "-q", "-c", "sig")
    fixture.commit(repo, {"core/io.py": BASE["core/io.py"].replace("path: str", "path: str, mode: str = 'r'")})
    fixture.git(repo, "switch", "-q", "-c", "uses", base)
    fixture.commit(repo, {"app/main.py": BASE["app/main.py"] + "\n\ndef again() -> str:\n    return load('b')\n"})
    fixture.git(repo, "switch", "-q", "-c", "dep", base)
    fixture.commit(repo, {"app/extra.py": BASE["app/extra.py"].replace("return 1", "return 2")})
    fixture.git(repo, "switch", "-q", "-c", "lone", base)
    fixture.commit(repo, {"tools/fmt.py": BASE["tools/fmt.py"].replace("return s", "return s.strip()")})
    fixture.git(repo, "switch", "-q", "main")
    return repo, base


def _pair(p, first, then):
    return next(x for x in p.pairs if x.first == first and x.then == then)


def test_plan_finds_uses_module_deps_tests_and_order(tmp_path):
    repo, base = _setup(tmp_path)
    p = plan.plan(repo, ["sig", "uses", "dep", "lone"])
    assert p.base == base
    sig = next(s for s in p.sides if s.branch == "sig")
    assert sig.modules == ("core",) and [c.op for c in sig.changes] == ["changed"]
    u = _pair(p, "sig", "uses")
    assert u.uses == (("load", "app/main.py:5"), ("load", "app/main.py:9")) and u.module_deps == () and u.overlap == ()
    assert u.tests == ("tests/test_main.py",)
    d = _pair(p, "sig", "dep")
    assert d.uses == () and d.module_deps == (("app", "core"),)
    assert _pair(p, "uses", "sig").clean        # sig does not use what uses changed
    assert _pair(p, "uses", "dep").clean        # same module, but no Tree change in uses
    for other in ("sig", "uses", "dep"):
        assert _pair(p, other, "lone").clean and _pair(p, "lone", other).clean
        assert _pair(p, other, "lone").tests == ()
    assert p.order == (("lone", "sig"), ("dep", "uses"))
    assert p.exit_code() is Exit.FINDINGS
    md = p.to_markdown()
    assert "## Cannot prove" in md and "`load` at `app/main.py:9`" in md


def test_plan_clean_pair_and_overlap(tmp_path):
    repo, _ = _setup(tmp_path)
    clean = plan.plan(repo, ["lone", "uses"])
    assert all(x.clean for x in clean.pairs) and clean.exit_code() is Exit.OK
    assert clean.order == (("lone", "uses"),)
    fixture.git(repo, "switch", "-q", "-c", "both", "uses")
    fixture.commit(repo, {"core/io.py": BASE["core/io.py"] + "\n\nOTHER = 1\n"})
    p = plan.plan(repo, ["sig", "both"])
    assert _pair(p, "sig", "both").overlap == ("core/io.py",)


def test_plan_cycle_shares_a_layer(tmp_path):
    repo, base = _setup(tmp_path)
    fixture.git(repo, "switch", "-q", "-c", "back", "uses")
    fixture.commit(repo, {"core/io.py": BASE["core/io.py"].replace("LIMIT = 3", "LIMIT: int = 3")
                          + "\n\nfrom app.main import again\n"})
    fixture.git(repo, "switch", "-q", "-c", "fwd", "sig")
    fixture.commit(repo, {"app/main.py": BASE["app/main.py"].replace("def main() -> str", "def main(x: int = 0) -> str")
                          + "\n\nfrom core.io import LIMIT\n"})
    p = plan.plan(repo, ["back", "fwd"], base=base)
    assert len(p.order) == 1 and p.notes and "cycle" in p.notes[0]


def test_plan_is_deterministic_and_cli(tmp_path, capsysbinary):
    repo, _ = _setup(tmp_path)
    a = json.dumps(plan.plan(repo, ["sig", "uses", "dep"]).to_obj(), sort_keys=True)
    b = json.dumps(plan.plan(repo, ["sig", "uses", "dep"]).to_obj(), sort_keys=True)
    assert a == b
    rc = cli.main(["plan", "sig", "uses", "--repo", str(repo), "--format", "json"])
    obj = json.loads(capsysbinary.readouterr().out)
    assert rc == Exit.FINDINGS and obj["schema"] == "rco.plan/1" and obj["limits"]
    with pytest.raises(UsageError):
        plan.plan(repo, ["sig"])
    with pytest.raises(UsageError):
        plan.plan(repo, ["sig", "sig"])


def test_plan_ignores_added_only_contracts_and_lists_unseen_files(tmp_path):
    repo, base = _setup(tmp_path)
    fixture.git(repo, "switch", "-q", "-c", "grow", base)
    fixture.commit(repo, {"core/io.py": BASE["core/io.py"] + "\n\ndef save(path: str) -> None:\n    pass\n",
                          "core/schema.json": "{}\n"})
    p = plan.plan(repo, ["grow", "dep", "uses"])
    grow = next(s for s in p.sides if s.branch == "grow")
    assert grow.modules == ("core",) and grow.breaking == () and grow.unseen == ("core/schema.json",)
    assert _pair(p, "grow", "dep").clean and _pair(p, "grow", "uses").clean
    assert "`core/schema.json`" in p.to_markdown()

def test_plan_added_only_symbol_is_not_a_use(tmp_path):
    """A symbol A only added is not a use, even if B also adds and calls it (M11)."""
    repo, base = _setup(tmp_path)
    fixture.git(repo, "switch", "-q", "-c", "grow", base)
    fixture.commit(repo, {
        "core/io.py": BASE["core/io.py"].replace(
            "path: str", "path: str, mode: str = 'r'"
        ) + "\n\ndef save(path: str) -> None:\n    pass\n",
    })
    fixture.git(repo, "switch", "-q", "-c", "also", base)
    fixture.commit(repo, {
        "core/io.py": BASE["core/io.py"] + "\n\ndef save(path: str) -> None:\n    pass\n",
        "app/main.py": BASE["app/main.py"] + "\n\nfrom core.io import save\n\n\ndef persist() -> None:\n    save('a')\n",
    })
    p = plan.plan(repo, ["grow", "also"])
    pair = _pair(p, "grow", "also")
    assert pair.overlap == ("core/io.py",)
    assert {q for q, _ in pair.uses} == {"load"}
    assert not any(q == "save" for q, _ in pair.uses)


def test_plan_stacked_branch_orders_after_its_base(tmp_path):
    """A branch that has all of another's commits is stacked and merges after it (M12)."""
    repo, _ = _setup(tmp_path)
    fixture.git(repo, "switch", "-q", "-c", "stacked", "lone")
    fixture.commit(repo, {"tools/fmt.py": BASE["tools/fmt.py"] + "\n\nEXTRA = 1\n"})
    p = plan.plan(repo, ["lone", "stacked"])
    pair = _pair(p, "lone", "stacked")
    assert pair.contains and pair.depends
    assert not _pair(p, "stacked", "lone").contains
    assert p.order == (("lone",), ("stacked",))

def test_plan_compares_each_pair_against_its_own_merge_base(tmp_path):
    """An older branch must not inflate overlap between two recent ones."""
    repo, old = fixture.make_repo(tmp_path / "r", {
        "shared.py": "X = 1\n",
        "a.py": "A = 1\n",
        "b.py": "B = 1\n",
    })
    # main moves on; many files change after `old`
    fixture.commit(repo, {"shared.py": "X = 2\n", "extra.py": "E = 1\n", "more.py": "M = 1\n"})
    main = fixture.git(repo, "rev-parse", "HEAD")
    fixture.git(repo, "switch", "-q", "-c", "ancient", old)
    fixture.commit(repo, {"a.py": "A = 9\n"})
    fixture.git(repo, "switch", "-q", "-c", "left", main)
    fixture.commit(repo, {"shared.py": "X = 3\n", "left.py": "L = 1\n"})
    fixture.git(repo, "switch", "-q", "-c", "right", main)
    fixture.commit(repo, {"shared.py": "X = 4\n", "right.py": "R = 1\n"})
    p = plan.plan(repo, ["ancient", "left", "right"])
    pair = _pair(p, "left", "right")
    assert pair.overlap == ("shared.py",)
    assert pair.base == main
    # the common base of all three is `old`; that must not be this pair's base
    assert pair.base != p.base
    assert p.base == old

