"""Phase 2 fold: update, prose and pack work together on one SQLite store."""
import json

from rco import cli, fixture
from rco.output import Exit
from rco.prose import drain_queue
from rco.store.sqlite import open_repo_store

FILES = {
    "pkg/__init__.py": '"""Pkg does the work."""\n',
    "pkg/core.py": "from util import helpers\n\n\ndef run(x: int) -> int:\n    return helpers.clamp(x)\n",
    "util/__init__.py": '"""Small helpers."""\n',
    "util/helpers.py": "def clamp(x: int) -> int:\n    return max(0, x)\n",
}


def _pack(repo, capsys, *args):
    code = cli.main(["pack", "--repo", str(repo), "--format", "json", *args])
    return code, json.loads(capsys.readouterr().out)


def test_update_drain_pack(tmp_path, capsys):
    repo, a = fixture.make_repo(tmp_path / "r", FILES)
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK
    capsys.readouterr()
    store = open_repo_store(repo)
    assert drain_queue(repo, store) == 0  # CLI already mined the queued rows
    code, p = _pack(repo, capsys, "--for", "pkg/core.py")
    # read from the store; the only note is the card 1341 budget scaling
    assert code == Exit.OK and p["stale"] == []
    assert [n for n in p["notes"] if not n.startswith("budget scaled to ")] == []
    forest = {e["id"]: e for e in p["forest"]}
    assert forest["pkg"]["prose"]["text"] == "Pkg does the work."
    assert forest["pkg"]["depends_on"] == ["util"]
    # the 1341 budget (half of util/helpers.py) may cut the hop line; it is still named
    assert ([h["id"] for h in p["hop"]] + [t[4:] for t in p["trimmed"] if t.startswith("hop:")]
            == ["util"])

    b = fixture.commit(repo, {"util/helpers.py": "def clamp(x: int, lo: int = 0) -> int:\n    return max(lo, x)\n"})
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK
    capsys.readouterr()
    code, p = _pack(repo, capsys)
    assert code == 0 and p["stale"] == [] and p["sha"] == b  # already mined after update
    assert drain_queue(repo, store) == 0
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""
