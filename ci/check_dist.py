"""Check the built sdist and wheel before they can be published.

usage: python ci/check_dist.py DIST_DIR

- The sdist file list holds no junk or private paths: no .rco/, work/,
  .git/, dist/, __pycache__ or *.pyc, and passes the hygiene text checks
  (tools/check_hygiene.py) on its member names.
- The wheel carries every module under src/rco, so a new subpackage cannot
  be left out of the install.
Exit 0 clean, 1 findings, 2 usage. Findings name the member, never more.
"""
from __future__ import annotations

import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_hygiene as hygiene  # noqa: E402

FORBIDDEN_PARTS = {".rco", "work", ".git", "dist", "__pycache__"}


def sdist_findings(names: list[str]) -> list[str]:
    out: list[str] = []
    allow, vocab = hygiene.ordinary_words(ROOT, ROOT / hygiene.DEFAULT_WORDS_REL)
    for name in names:
        parts = name.split("/")[1:]  # drop the <project>-<version>/ prefix
        if FORBIDDEN_PARTS & set(parts) or name.endswith(".pyc"):
            out.append(f"forbidden path: {name}")
            continue
        junk = hygiene.junk_kind(name)
        if junk:
            out.append(f"{junk}: {name}")
        for kind in hygiene.kinds_in_text("/".join(parts), allow, vocab):
            out.append(f"{kind}: {name}")
    return out


def wheel_findings(names: list[str], src: Path = ROOT / "src") -> list[str]:
    want = sorted(p.relative_to(src).as_posix() for p in (src / "rco").rglob("*.py")
                  if "__pycache__" not in p.parts)
    have = set(names)
    return [f"missing from wheel: {w}" for w in want if w not in have]


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    dist = Path(argv[0])
    sdists = sorted(dist.glob("*.tar.gz"))
    wheels = sorted(dist.glob("*.whl"))
    if len(sdists) != 1 or len(wheels) != 1:
        print(f"check_dist: want one sdist and one wheel in {dist}", file=sys.stderr)
        return 2
    with tarfile.open(sdists[0]) as t:
        snames = [m.name for m in t.getmembers() if m.isfile()]
    with zipfile.ZipFile(wheels[0]) as z:
        wnames = z.namelist()
    found = sdist_findings(snames) + wheel_findings(wnames)
    for f in found:
        print(f"check_dist: {f}")
    print(f"check_dist: {len(snames)} sdist files, {len(wnames)} wheel files, {len(found)} findings")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
