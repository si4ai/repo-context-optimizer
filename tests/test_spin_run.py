"""verify/spin/run.sh exits nonzero when a result differs from its expectation."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from ci.test_select import load_map, select_jobs  # noqa: E402

_SPIN_STUB = r"""#!/bin/sh
if [ "$1" = "-V" ]; then
  echo "Spin Version stub"
  exit 0
fi
# Replay a trail (stdout is captured as trail.txt).
for a in "$@"; do
  if [ "$a" = "-t" ]; then
    [ "${SPIN_T_EMPTY:-0}" = "1" ] || echo "EV stub"
    exit ${SPIN_T_RC:-0}
  fi
done
# Generate (spin -a ...).
echo "/* stub pan.c */" > pan.c
exit ${SPIN_RC:-0}
"""

_GCC_STUB = r"""#!/bin/sh
out=a.out
prev=""
for a in "$@"; do
  if [ "$prev" = "-o" ]; then
    out=$a
  fi
  prev=$a
done
reach=0
for a in "$@"; do
  [ "$a" = "-DREACH" ] && reach=1
done
if [ "$reach" = "1" ] && [ "${GCC_REACH_RC:-0}" != "0" ]; then
  echo "gcc stub -DREACH failed" >&2
  exit "$GCC_REACH_RC"
fi
if [ "${GCC_RC:-0}" != "0" ]; then
  echo "gcc stub failed" >&2
  exit "$GCC_RC"
fi
# pan / pan_i: print a fixed verdict the test controls.
cat > "$out" << 'END'
#!/bin/sh
echo "      ${PAN_STATES:-10} states, stored"
case "$0" in
  *pan_i) [ "${PAN_I_RC:-0}" = "0" ] || exit "$PAN_I_RC";;
esac
echo "errors: ${PAN_ERRORS:-1}"
END
chmod +x "$out"
exit 0
"""


def _harness(tmp_path: Path, *, spin_rc: int = 0, gcc_rc: int = 0,
             pan_errors: int = 1, pan_states: int = 10, gcc_reach_rc: int = 0,
             pan_i_rc: int = 0, spin_t_rc: int = 0,
             spin_t_empty: bool = False) -> tuple[Path, dict[str, str]]:
    dest = tmp_path / "spin"
    dest.mkdir()
    src = ROOT / "verify" / "spin" / "run.sh"
    dest.joinpath("run.sh").write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    dest.joinpath("run.sh").chmod(dest.joinpath("run.sh").stat().st_mode | stat.S_IEXEC)
    dest.joinpath("rco_update.pml").write_text("/* stub */\n", encoding="utf-8")
    bindir = tmp_path / "bin"
    bindir.mkdir()
    spin = bindir / "spin"
    spin.write_text(_SPIN_STUB, encoding="utf-8")
    spin.chmod(0o755)
    gcc = bindir / "gcc"
    gcc.write_text(_GCC_STUB, encoding="utf-8")
    gcc.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bindir}:{env.get('PATH', '')}"
    env["SPIN_RC"] = str(spin_rc)
    env["GCC_RC"] = str(gcc_rc)
    env["PAN_ERRORS"] = str(pan_errors)
    env["PAN_STATES"] = str(pan_states)
    env["GCC_REACH_RC"] = str(gcc_reach_rc)
    env["PAN_I_RC"] = str(pan_i_rc)
    env["SPIN_T_RC"] = str(spin_t_rc)
    env["SPIN_T_EMPTY"] = "1" if spin_t_empty else "0"
    return dest, env


def _run(dest: Path, env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(dest / "run.sh"), *args],
        cwd=dest,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_expected_fail_exits_zero(tmp_path: Path):
    # Variant a / P1 is expected FAIL. A stub that reports errors: 1 must exit 0.
    dest, env = _harness(tmp_path, pan_errors=1)
    proc = _run(dest, env, "a", "1")
    assert proc.returncode == 0, proc.stderr or proc.stdout
    assert "FAIL" in proc.stdout
    assert "differs from expectation" not in proc.stdout
    summary = dest / "results" / "summary.txt"
    assert summary.is_file()
    text = summary.read_text(encoding="utf-8")
    assert "exit 0" in text
    assert "every check matches its expectation" in text


def test_mismatch_exits_nonzero(tmp_path: Path):
    # Variant fixed / P1 is expected PASS. The same failing stub must exit nonzero.
    dest, env = _harness(tmp_path, pan_errors=1)
    proc = _run(dest, env, "fixed", "1")
    assert proc.returncode != 0, proc.stdout
    assert "differs from expectation" in proc.stdout
    assert "exit 1" in (dest / "results" / "summary.txt").read_text(encoding="utf-8")


def test_spin_failure_exits_nonzero(tmp_path: Path):
    dest, env = _harness(tmp_path, spin_rc=1)
    proc = _run(dest, env, "a", "1")
    assert proc.returncode != 0, proc.stdout
    assert "ERROR" in proc.stdout
    assert "spin or compiler failed" in proc.stdout


@pytest.mark.parametrize("kwargs", [
    {"gcc_reach_rc": 1},
    {"pan_i_rc": 1},
    {"spin_t_rc": 1},
    {"spin_t_empty": True},
], ids=["reach-gcc", "pan_i", "spin-t-rc", "spin-t-empty"])
def test_counterexample_step_failure_exits_nonzero(tmp_path: Path, kwargs):
    # a / P1 is expected FAIL, so the FAIL path builds pan_i and replays the trail.
    # A failure in either step is an ERROR, not a matching FAIL.
    dest, env = _harness(tmp_path, pan_errors=1, **kwargs)
    proc = _run(dest, env, "a", "1")
    assert proc.returncode != 0, proc.stdout
    assert "ERROR" in proc.stdout
    assert "every check matches" not in proc.stdout


def test_pipefail_is_set():
    text = (ROOT / "verify" / "spin" / "run.sh").read_text(encoding="utf-8")
    assert "set -uo pipefail" in text or "set -o pipefail" in text


def test_run_sh_change_selects_pytest_and_spin():
    rules, jobs = load_map(ROOT / "ci" / "test-select.toml")
    assert "pytest" in jobs and "spin" in jobs
    assert select_jobs(["verify/spin/run.sh"], rules) == {"pytest", "spin"}
