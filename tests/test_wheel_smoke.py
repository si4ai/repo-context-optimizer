"""Phase 2 step 6: the dist checks catch junk in the sdist and modules left out of the wheel.

CI's package job runs ci/check_dist.py on the real build and
ci/wheel_smoke.sh on the installed wheel; these tests pin the checker.
"""
import importlib.util
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("check_dist", ROOT / "ci" / "check_dist.py")
check_dist = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(check_dist)

P = "repo_context_optimizer-0.1.0/"


def test_clean_sdist_list():
    names = [P + n for n in ("PKG-INFO", "README.md", "pyproject.toml", "src/rco/cli.py",
                             "tests/test_build.py", "tests/fixtures/py_basic/src/demo/core.py")]
    assert check_dist.sdist_findings(names) == []


def test_sdist_junk_and_private_paths():
    bad = [P + ".rco/rco.db", P + "work/notes.md", P + "src/rco/__pycache__/x.cpython-312.pyc",
           P + "dist/old.whl", P + "src/rco/cli.py.bak"]
    found = check_dist.sdist_findings(bad)
    for name in bad:
        assert any(name in f for f in found), name


def test_wheel_must_carry_every_module(tmp_path):
    src = tmp_path / "src"
    (src / "rco" / "store").mkdir(parents=True)
    for rel in ("rco/__init__.py", "rco/cli.py", "rco/store/__init__.py"):
        (src / rel).write_text("")
    assert check_dist.wheel_findings(["rco/__init__.py", "rco/cli.py", "rco/store/__init__.py"],
                                     src) == []
    assert check_dist.wheel_findings(["rco/__init__.py", "rco/cli.py"], src) == [
        "missing from wheel: rco/store/__init__.py"]


def test_check_dist_usage_exit_2(tmp_path):
    p = subprocess.run(["python3", str(ROOT / "ci" / "check_dist.py"), str(tmp_path)],
                       capture_output=True)
    assert p.returncode == 2


def test_smoke_script_is_executable_and_strict():
    sh = ROOT / "ci" / "wheel_smoke.sh"
    text = sh.read_text()
    assert sh.stat().st_mode & 0o111
    assert "set -euo pipefail" in text and "expect 0" in text
