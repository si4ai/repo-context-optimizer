"""Card 1341: no pack when nothing in-repo is imported; budget = min(--budget, half the imported tokens)."""
import json

from rco import cli, fixture
from rco.forest_model import estimate_tokens
from rco.output import Exit
from rco.pack import MIN_BUDGET, pack, pack_markdown


def _fns(prefix: str, n: int) -> str:
    return "\n".join(f"def {prefix}{i}(x: int) -> int:\n    return x + {i}\n" for i in range(n))


def _repo(tmp_path, n=400):
    files = {
        "app/__init__.py": "",
        "app/solo.py": "import json\n\n\ndef solo() -> str:\n    return json.dumps(1)\n",
        "app/user.py": "from lib.big import f0\n\n\ndef use() -> int:\n    return f0(1)\n",
        "lib/__init__.py": "",
        "lib/big.py": _fns("f", n),
        "other/__init__.py": "",
        "other/z.py": _fns("z", 50),
    }
    return fixture.make_repo(tmp_path / "scaled", files)


def test_no_in_repo_imports_prints_short_noop_pack(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, ["app/solo.py"])
    text = pack_markdown(p, "app/solo.py")
    assert p.forest == () and p.trees == () and p.hop == ()
    assert "nothing to add for these files" in text
    assert "## Forest" not in text
    assert text.count("\n") == 1 and estimate_tokens(text) < 40  # one short line
    assert cli.main(["pack", "--repo", str(repo), "--for", "app/solo.py"]) == Exit.OK
    assert "nothing to add for these files" in capsys.readouterr().out


def test_used_at_most_half_the_imported_tokens(tmp_path):
    repo, sha = _repo(tmp_path)
    imported = estimate_tokens((repo / "lib/big.py").read_text())
    for cap in (12000, 3000, 1000):
        p = pack(repo, sha, ["app/user.py"], cap)
        limit = min(cap, imported // 2)
        assert p.budget == limit, (cap, p.budget, imported)
        assert p.used <= limit
        assert estimate_tokens(pack_markdown(p, "app/user.py")) <= limit


def test_budget_is_a_cap_not_a_target(tmp_path):
    repo, sha = _repo(tmp_path, n=40)  # small import: the pack stays far below 12000
    imported = estimate_tokens((repo / "lib/big.py").read_text())
    p = pack(repo, sha, ["app/user.py"])
    assert p.used <= imported // 2 < 12000


def test_whole_repo_pack_keeps_the_given_budget(tmp_path):
    repo, sha = _repo(tmp_path)
    assert pack(repo, sha, None, 5000).budget == 5000


def test_noop_pack_keeps_the_changed_warning(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    (repo / "app" / "solo.py").write_text("x = 1\n")
    assert cli.main(["pack", "--repo", str(repo), "--for", "app/solo.py"]) == Exit.OK
    lines = capsys.readouterr().out.splitlines()
    assert "nothing to add for these files" in lines[0]
    assert lines[1:] == ["# warning: changed since this pack: app/solo.py"]


def _sized(tokens: int) -> str:
    """Python source whose estimated tokens are exactly `tokens`."""
    head = "def g(x: int) -> int:\n    return x\n"
    pad = ""
    text = head + "# " + pad + "\n"
    while estimate_tokens(text) < tokens:
        pad += "z9 "
        text = head + "# " + pad + "\n"
        if len(pad) > tokens * 16:
            break
    # if the last chunk jumped past `tokens`, trim one character at a time
    while estimate_tokens(text) > tokens and pad:
        pad = pad[:-1]
        text = head + "# " + pad + "\n"
    assert estimate_tokens(text) == tokens, (estimate_tokens(text), tokens)
    return text


def _small_repo(tmp_path, imported_tokens: int):
    files = {
        "app/__init__.py": "",
        "app/tiny.py": "from lib.small import g\n\n\ndef use() -> int:\n    return g(1)\n",
        "lib/__init__.py": "",
        "lib/small.py": _sized(imported_tokens),
    }
    return fixture.make_repo(tmp_path / f"small{imported_tokens}", files)


def test_below_min_budget_skips_like_the_noop(tmp_path, capsys):
    # half of 255 is 127 < MIN_BUDGET: no pack, one short line, exit 0
    repo, sha = _small_repo(tmp_path, 255)
    p = pack(repo, sha, ["app/tiny.py"])
    text = pack_markdown(p, "app/tiny.py")
    assert p.forest == () and p.trees == () and p.hop == ()
    assert text.startswith("# rco pack  (SHA=") and "nothing to add for these files" in text
    assert "## Forest" not in text
    assert text.count("\n") == 1 and estimate_tokens(text) < 40
    assert cli.main(["pack", "--repo", str(repo), "--for", "app/tiny.py"]) == Exit.OK
    assert "nothing to add for these files" in capsys.readouterr().out


def test_at_min_budget_still_packs(tmp_path):
    # half of 256 is 128 == MIN_BUDGET: a real pack at budget 128
    repo, sha = _small_repo(tmp_path, 256)
    p = pack(repo, sha, ["app/tiny.py"])
    assert p.budget == MIN_BUDGET
    assert "nothing to add for these files" not in pack_markdown(p, "app/tiny.py")


def test_scaled_budget_is_noted(tmp_path, capsys):
    # card 1344 (validator 7172 W1 on #99): the note says why the budget is lower
    repo, sha = _repo(tmp_path)
    imported = estimate_tokens((repo / "lib/big.py").read_text())
    p = pack(repo, sha, ["app/user.py"], 12000)
    note = f"budget scaled to {imported // 2}: half the estimated tokens of the imported files"
    assert note in p.notes
    assert cli.main(["pack", "--repo", str(repo), "--for", "app/user.py", "--format", "json"]) == Exit.OK
    assert note in json.loads(capsys.readouterr().out)["notes"]
    # a cap already below half the imported tokens is not a scaling
    assert not [n for n in pack(repo, sha, ["app/user.py"], 200).notes if n.startswith("budget scaled")]
