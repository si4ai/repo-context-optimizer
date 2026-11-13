"""Golden Maps: fixture repo in, canonical JSON bytes out.

Regenerate with RCO_REGEN=1 and say why in the PR. Goldens are pinned to
the CI Python (3.12): the Python signature text comes from ast.unparse.
"""
import os
from pathlib import Path

import pytest

from rco import build, canon, fixture

HERE = Path(__file__).parent
CASES = ["py_basic", "polyglot_basic"]


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.mark.parametrize("case", CASES)
def test_golden(case, tmp_path):
    repo, sha = fixture.make_repo(tmp_path / case, _files(HERE / "fixtures" / case))
    got = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    golden = HERE / "golden" / f"{case}.json"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()
