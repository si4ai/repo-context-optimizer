"""Pure functions of tools/phase0_baseline.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import phase0_baseline as p0  # noqa: E402


def test_module_of_coarse():
    assert p0.module_of("agent_coord/server/api/mill/rpc.py") == "agent_coord/server"
    assert p0.module_of("agent_coord/config.py") == "agent_coord/config.py"
    assert p0.module_of("tests/server/test_x.py") == "tests"
    assert p0.module_of("README.md") == "README.md"


def test_module_of_package():
    assert p0.module_of("agent_coord/server/api/mill/rpc.py", "package") == "agent_coord/server/api/mill"
    assert p0.module_of("README.md", "package") == "."


def test_percentile_nearest_rank():
    vals = list(range(1, 11))
    assert p0.percentile(vals, 50) == 5
    assert p0.percentile(vals, 90) == 9
    assert p0.percentile(vals, 99) == 10
    assert p0.percentile([7], 90) == 7


def test_skeleton_strips_bodies_and_private_names():
    src = (
        '"""Mod doc.\n\nMore."""\n'
        "import os\n"
        "LIMIT = 3\n"
        "_hidden = 1\n"
        "def f(a: int, b: str = 'x') -> int:\n"
        '    """Do f.\n\n    detail"""\n'
        "    return a + len(b)\n"
        "def _g():\n    pass\n"
        "class C:\n"
        "    x: int = 5\n"
        "    def m(self):\n        return 1\n"
    )
    sk = p0.skeleton(src)
    assert "def f(a: int, b: str='x') -> int:" in sk
    assert '"""Do f."""' in sk and "detail" not in sk
    assert "return" not in sk and "import" not in sk
    assert "_g" not in sk and "_hidden" not in sk
    assert "LIMIT = 3" in sk and "x: int" in sk and "def m(self)" in sk
    assert p0.skeleton(src) == sk  # deterministic


def test_parse_cold_log_table_and_summary():
    log = (
        "| command | exit | stdout_bytes | PT |\n"
        "|---|---|---|---|\n"
        "| mill --help | 0 | 100 | 10:00:00 |\n"
        "| mill join --help | 0 | 50 | 10:00:05 |\n"
        "| mill join --link-file invite.txt | 0 | 700 | 10:00:09 |\n"
        "SUMMARY turn=join commands=3 wrong_guesses=1 retries=2 start=10:00:00 end=10:00:20 stdout_bytes=850 task_done=y\n"
        "10:01:00  exit=1  stdout_bytes=0  mill remove roster x --yes\n"
        "SUMMARY turn=leave commands=1 wrong_guesses=0 retries=0 start=10:01:00 end=10:01:04\n"
    )
    r = p0.parse_cold_log(log)
    assert r["rows"] == 4
    assert r["reads_before_first_useful"] == 2
    assert r["stdout_bytes_before_first_useful"] == 150
    join, leave = r["turns"]
    assert (join["commands"], join["wrong_guesses"], join["retries"], join["seconds"]) == (3, 1, 2, 20)
    assert leave["stdout_bytes"] is None and leave["seconds"] == 4


def test_parse_cold_log_without_join():
    r = p0.parse_cold_log("| ls | 0 | 5 | 01:02:03 |\n")
    assert r["first_useful_action"] is None and r["reads_before_first_useful"] is None


def test_est_tokens_rounds_up():
    # characters / 4, rounded up (not down): 1..3 chars -> 1, 4 -> 1, 5 -> 2
    assert p0.CHARS_PER_TOKEN == 4
    assert p0.est_tokens("") == 0
    assert p0.est_tokens("a") == 1
    assert p0.est_tokens("abc") == 1
    assert p0.est_tokens("abcd") == 1
    assert p0.est_tokens("abcde") == 2


def test_histogram_counts_each_value_once():
    assert p0.histogram([]) == {}
    assert p0.histogram([3]) == {"3": 1}
    assert p0.histogram([1, 2, 2, 5, 5, 5]) == {"1": 1, "2": 2, "5": 3}

