#!/usr/bin/env python3
"""Recompute rco-metrics.json files under the card 1351 with/without definition.

Each input directory holds one fetched rco-report artifact: rco-metrics.json
and pack.md. The git repo must contain that file's base and head SHAs.

    python tools/recompute_pipeline_meter.py \
        --metrics-dir DIR --repo REPO --out docs/baseline/pipeline-meter-47.md

The run does not bump any adopter pin.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path


def _rows(metrics_dir: Path, repo: Path) -> list[dict]:
    from rco import metrics
    tokenizer, count = metrics.counter()
    rows: list[dict] = []
    skipped: list[str] = []
    for d in sorted(p for p in metrics_dir.iterdir() if p.is_dir()):
        src = d / "rco-metrics.json"
        pack_path = d / "pack.md"
        if not src.is_file():
            skipped.append(d.name)
            continue
        old = json.loads(src.read_text())
        pack_text = pack_path.read_text() if pack_path.is_file() else ""
        budget = old.get("pack_budget") or 12000
        new = metrics.write_metrics(
            repo, old["base_sha"], old["head_sha"], int(old["pr"]),
            pack_text, int(budget), count, tokenizer,
        )
        rows.append({
            "artifact": d.name,
            "pr": int(old["pr"]),
            "base_sha": old["base_sha"],
            "head_sha": old["head_sha"],
            "changed_files": new["changed_files"],
            "old_ratio": old.get("ratio_tokens"),
            "old_with_tokens": old["with_rco"]["tokens"],
            "old_without_tokens": old["without_rco"]["tokens"],
            "ratio": new["ratio_tokens"],
            "without_tokens": new["without_rco"]["tokens"],
            "with_tokens": new["with_rco"]["tokens"],
            "pack_tokens": new["with_rco"].get("pack_tokens"),
            "changed_tokens": new["with_rco"].get("changed_tokens"),
            "pack_skipped": new["with_rco"].get("pack_skipped"),
            "pack_overrun": new.get("pack_overrun") or 0,
            "pack_used": new.get("pack_used"),
            "tokenizer": tokenizer,
        })
    return rows, skipped


def _md(rows: list[dict], skipped: list[str], repo_name: str) -> str:
    ratios = [r["ratio"] for r in rows if r["ratio"] is not None]
    old_ratios = [r["old_ratio"] for r in rows if r["old_ratio"] is not None]
    worse = [r for r in rows if r["ratio"] is not None and r["ratio"] > 1]
    over = [r for r in rows if r["pack_overrun"] > 0]
    skipped_packs = [r for r in rows if r["pack_skipped"]]
    unique_prs = sorted({r["pr"] for r in rows})

    def med(xs: list[float]) -> float:
        return statistics.median(xs) if xs else float("nan")

    def sh(s: str) -> str:
        return s[:7]

    out = [
        "# Card 1351 meter recompute",
        "",
        "The previous `rco metrics measure` counted only the pack on the with-rco",
        "side, which flattered rco. The shared definition now counts the pack",
        "plus the changed files (the files the agent still opens). A skipped pack",
        "is a tie. This file recomputes fetched the-mill `rco-report` artifacts",
        f"against `{repo_name}` at the SHAs recorded in each `rco-metrics.json`.",
        "",
        "To re-run:",
        "",
        "```sh",
        "python tools/recompute_pipeline_meter.py \\",
        "    --metrics-dir DIR --repo THE_MILL_CLONE \\",
        "    --out docs/baseline/pipeline-meter-47.md",
        "```",
        "",
        "`DIR` is a directory of fetched `rco-report` artifacts (each child has",
        "`rco-metrics.json` and `pack.md`). The clone must contain those base and",
        "head commits. This does not bump the-mill's rco pin.",
        "",
        "## Summary",
        "",
        f"- artifacts recomputed: {len(rows)} (unique PRs: {len(unique_prs)})",
        f"- tokenizer: `{rows[0]['tokenizer'] if rows else ''}`",
        f"- median ratio (old, pack alone): {med(old_ratios):.4f}",
        f"- median ratio (new, pack plus changed files): {med(ratios):.4f}",
        f"- ratio above 1 (pack plus changed costs more than without): {len(worse)}",
        f"- pack tokens over budget: {len(over)}",
        f"- skipped packs (ties): {len(skipped_packs)}",
    ]
    if skipped:
        out.append(f"- input dirs with no `rco-metrics.json` (omitted): {len(skipped)}")
        out.append("")
        out.append(", ".join(f"`{n}`" for n in skipped))
    out += [
        "",
        "## New ratio above 1",
        "",
    ]
    if not worse:
        out.append("None of these artifacts has a new ratio above 1.")
    else:
        out += [
            "| artifact | pr | head | ratio | with | without | pack | changed | overrun |",
            "|---|---:|---|---:|---:|---:|---:|---:|---:|",
        ]
        for r in worse:
            out.append(
                f"| `{r['artifact']}` | {r['pr']} | `{sh(r['head_sha'])}` | "
                f"{r['ratio']:.4f} | {r['with_tokens']} | {r['without_tokens']} | "
                f"{r['pack_tokens']} | {r['changed_tokens']} | {r['pack_overrun']} |"
            )
    out += [
        "",
        "## Pack tokens over budget",
        "",
    ]
    if not over:
        out.append("None of these artifacts has `pack_overrun` above 0.")
    else:
        out += [
            "| artifact | pr | head | used | budget overrun | ratio |",
            "|---|---:|---|---:|---:|---:|",
        ]
        for r in over:
            out.append(
                f"| `{r['artifact']}` | {r['pr']} | `{sh(r['head_sha'])}` | "
                f"{r['pack_used']} | {r['pack_overrun']} | {r['ratio']:.4f} |"
            )
    out += [
        "",
        "## Every artifact",
        "",
        "| artifact | pr | head | old ratio | new ratio | without | with | pack | changed | skipped | overrun |",
        "|---|---:|---|---:|---:|---:|---:|---:|---:|---|---:|",
    ]
    for r in rows:
        old = "" if r["old_ratio"] is None else f"{r['old_ratio']:.4f}"
        new = "" if r["ratio"] is None else f"{r['ratio']:.4f}"
        skip = "yes" if r["pack_skipped"] else "no"
        out.append(
            f"| `{r['artifact']}` | {r['pr']} | `{sh(r['head_sha'])}` | "
            f"{old} | {new} | {r['without_tokens']} | {r['with_tokens']} | "
            f"{r['pack_tokens']} | {r['changed_tokens']} | {skip} | {r['pack_overrun']} |"
        )
    out.append("")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--metrics-dir", required=True, type=Path)
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--json", dest="json_out", type=Path, default=None)
    args = ap.parse_args(argv)
    rows, skipped = _rows(args.metrics_dir, args.repo)
    text = _md(rows, skipped, "si4ai/the-mill")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text)
    if args.json_out is not None:
        args.json_out.write_text(json.dumps({"rows": rows, "skipped": skipped}, indent=2) + "\n")
    print(f"wrote {args.out} ({len(rows)} artifacts)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
