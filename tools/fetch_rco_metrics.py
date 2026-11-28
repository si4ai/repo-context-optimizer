#!/usr/bin/env python3
"""A helper that gathers rco-metrics.json files from GitHub rco-report artifacts.

Uses the GitHub CLI (`gh`) already authenticated in the environment
(`gh auth` or GH_TOKEN). This script never accepts a token flag and
never puts a token in a URL or command line.

    python tools/fetch_rco_metrics.py --repo OWNER/NAME --out DIR [--limit N]

Then: rco metrics summary DIR
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def _gh(args: list[str], stdout: int = subprocess.PIPE) -> subprocess.CompletedProcess:
    """Run gh with the inherited environment; never pass a token argument."""
    cmd = ["gh", *args]
    p = subprocess.run(cmd, stdout=stdout, stderr=subprocess.PIPE)
    if p.returncode != 0:
        err = (p.stderr or b"").decode("utf-8", "replace").strip()
        # do not print env or headers; the CLI message is enough
        raise SystemExit(f"fetch_rco_metrics: gh failed ({p.returncode}): {err}")
    return p


def _artifacts(repo: str, limit: int) -> list[dict]:
    out = []
    page = 1
    while len(out) < limit:
        p = _gh([
            "api",
            f"repos/{repo}/actions/artifacts?name=rco-report&per_page=100&page={page}",
        ])
        payload = json.loads(p.stdout.decode())
        batch = payload.get("artifacts") or []
        if not batch:
            break
        out.extend(a for a in batch if not a.get("expired"))
        if len(batch) < 100:
            break
        page += 1
    return out[:limit]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="tools/fetch_rco_metrics.py",
                                 description=__doc__.split("\n")[0])
    ap.add_argument("--repo", required=True, help="OWNER/NAME")
    ap.add_argument("--out", required=True, type=Path,
                    help="directory to write one folder of rco-metrics.json per artifact")
    ap.add_argument("--limit", type=int, default=50,
                    help="newest rco-report artifacts to fetch (default: 50)")
    args = ap.parse_args(argv)
    if args.limit < 1:
        print("fetch_rco_metrics: --limit must be >= 1", file=sys.stderr)
        return 2
    if "/" not in args.repo or args.repo.count("/") != 1:
        print("fetch_rco_metrics: --repo must be OWNER/NAME", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    items = _artifacts(args.repo, args.limit)
    written = 0
    for i, art in enumerate(items, 1):
        aid = art.get("id")
        if not aid:
            continue
        dest = args.out / f"{i:03d}-{aid}"
        dest.mkdir(parents=True, exist_ok=True)
        zip_path = dest / "rco-report.zip"
        with open(zip_path, "wb") as fh:
            _gh(["api", f"repos/{args.repo}/actions/artifacts/{aid}/zip"],
                stdout=fh.fileno())
        try:
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(dest)
        except zipfile.BadZipFile:
            print(f"fetch_rco_metrics: artifact {aid} was not a zip; skipped",
                  file=sys.stderr)
            continue
        finally:
            zip_path.unlink(missing_ok=True)
        # accept rco-metrics.json at the zip root or one level down
        matches = list(dest.rglob("rco-metrics.json"))
        if matches:
            written += 1
        else:
            print(f"fetch_rco_metrics: artifact {aid} had no rco-metrics.json",
                  file=sys.stderr)
    print(f"fetched {written} rco-metrics.json file(s) from {len(items)} artifact(s) into {args.out}")
    return 0 if written else 1


if __name__ == "__main__":
    raise SystemExit(main())
