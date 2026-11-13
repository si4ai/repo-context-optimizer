"""rco.toml settings: the store backend, its path, adapters, and affected ignores."""
import sys

import pytest

from rco import cli, config, fixture
from rco.output import Exit, UsageError
from rco.store import MemoryStore
from rco.store.sqlite import DB_RELPATH


def test_parse_defaults_and_errors(capsys):
    assert config.parse("") == config.Config()
    c = config.parse('backend = "sqlite"\n[store]\npath = "x/r.db"\n[affected]\nignore = ["*.md"]\n')
    assert c.store == {"path": "x/r.db"} and c.affected_ignore == ("*.md",)
    assert config.parse("[[module]\n") == config.Config()
    assert "not valid TOML" in capsys.readouterr().err
    for bad in ("backend = 3\n", "store = 1\n", '[store]\npath = ""\n', "[affected]\nignore = [1]\n"):
        with pytest.raises(UsageError):
            config.parse(bad)


def _repo(tmp_path):
    return fixture.make_repo(tmp_path / "r", {"pkg/a.py": '"""A."""\ndef f():\n    pass\n'})


def test_default_store_and_read_only_commands(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    assert config.open_store(repo, create=False) is None
    assert not (repo / ".rco").exists()  # read-only commands never create a store
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK
    assert (repo / DB_RELPATH).exists()
    assert config.open_store(repo / "pkg", create=False) is not None  # from a subdirectory too
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == ""


def test_store_path_from_rco_toml(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    (repo / "rco.toml").write_text('[store]\npath = "cache/rco/rco.db"\n')
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK
    assert (repo / "cache/rco/rco.db").exists() and not (repo / ".rco").exists()
    assert cli.main(["pack", "--repo", str(repo), "--for", "pkg/a.py"]) == Exit.OK
    assert "A." in capsys.readouterr().out


def test_unshipped_and_unknown_backends_are_usage_errors(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    for name, msg in (("mill", "not shipped"), ("nope", "no backend 'nope'")):
        (repo / "rco.toml").write_text(f'backend = "{name}"\n')
        assert cli.main(["update", "--repo", str(repo)]) == Exit.USAGE
        assert msg in capsys.readouterr().err


ADAPTER = '''
from rco.store import MemoryStore
CALLS = []
def make(repo, options):
    CALLS.append((repo.name, dict(options)))
    return STORE
STORE = MemoryStore()
'''


def test_entry_point_adapter(tmp_path, monkeypatch, capsys):
    site = tmp_path / "site"
    (site / "rco_fake_backend-1.0.dist-info").mkdir(parents=True)
    (site / "rco_fake_backend-1.0.dist-info" / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: rco-fake-backend\nVersion: 1.0\n")
    (site / "rco_fake_backend-1.0.dist-info" / "entry_points.txt").write_text(
        "[rco.backends]\nfake = rco_fake_backend:make\n")
    (site / "rco_fake_backend.py").write_text(ADAPTER)
    monkeypatch.syspath_prepend(str(site))
    repo, sha = _repo(tmp_path)
    (repo / "rco.toml").write_text('backend = "fake"\n[store]\nurl = "memory://"\n')
    assert cli.main(["update", "--repo", str(repo)]) == Exit.OK
    mod = sys.modules["rco_fake_backend"]
    assert mod.CALLS == [("r", {"url": "memory://"})]
    assert isinstance(mod.STORE, MemoryStore) and mod.STORE.get_pointer("main") == sha
    assert not (repo / ".rco").exists()


def test_affected_ignore_from_rco_toml(tmp_path, capsys):
    repo, s1 = _repo(tmp_path)
    s2 = fixture.commit(repo, {"README.md": "x\n"})
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--format", "json"]) == Exit.OK
    assert '"run_all":true' in capsys.readouterr().out
    (repo / "rco.toml").write_text('[affected]\nignore = ["*.md"]\n')
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--format", "json"]) == Exit.OK
    assert '"run_all":false' in capsys.readouterr().out


def test_a_store_restored_from_a_ci_cache_is_reused(tmp_path, capsys):
    """The CI cache recipe: save .rco/ after a run, restore it into a fresh clone."""
    import json
    import shutil
    import subprocess

    repo, s1 = fixture.make_repo(tmp_path / "r", {"a/x.py": "def f():\n    pass\n",
                                                   "b/y.py": "def g():\n    pass\n"})
    assert cli.main(["update", "--repo", str(repo), "--name", "main"]) == Exit.OK
    s2 = fixture.commit(repo, {"b/y.py": "def g(x):\n    pass\n"})
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    shutil.copytree(repo / ".rco", clone / ".rco")  # actions/cache restore
    capsys.readouterr()
    assert cli.main(["update", "--repo", str(clone), "--name", "main", "--format", "json"]) == Exit.OK
    res = json.loads(capsys.readouterr().out)
    assert res["swapped"] and res["old"] == s1 and res["new"] == s2
    assert res["extracted"] == ["b"]  # module a came from the cache
