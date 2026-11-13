"""Select other open PR heads for the cross-PR contract notice CI job.

Names each PR as pr-N. Never uses a login or a desk. Reads the GitHub
pulls JSON list (one array, or several arrays written back to back).
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class PrHead:
    number: int
    label: str
    sha: str
    ref: str

    def line(self) -> str:
        return f"{self.label} {self.sha}"


def load_pulls(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    decoder = json.JSONDecoder()
    items: list[dict[str, Any]] = []
    i = 0
    n = len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n:
            break
        obj, end = decoder.raw_decode(text, i)
        if isinstance(obj, list):
            items.extend(obj)
        elif isinstance(obj, dict):
            items.append(obj)
        else:
            raise ValueError("pulls JSON must be an object or an array")
        i = end
    return items


def other_heads(prs: Sequence[dict[str, Any]], exclude: int | None = None) -> tuple[PrHead, ...]:
    out: list[PrHead] = []
    seen: set[int] = set()
    for p in prs:
        if str(p.get("state") or "open") != "open":
            continue
        number = int(p["number"])
        if exclude is not None and number == exclude:
            continue
        if number in seen:
            continue
        seen.add(number)
        head = p.get("head") if isinstance(p.get("head"), dict) else {}
        sha = str((head or {}).get("sha") or p.get("sha") or "")
        ref = str((head or {}).get("ref") or p.get("ref") or "")
        if not sha:
            raise ValueError(f"pull {number} has no head sha")
        # Label is pr-N so the notice names PRs, never a login or a desk.
        out.append(PrHead(number, f"pr-{number}", sha, ref))
    out.sort(key=lambda h: h.number)
    return tuple(out)


def fetch_spec(head: PrHead) -> str:
    """git fetch src:dst that stores the head as the local ref pr-N."""
    return f"pull/{head.number}/head:refs/heads/{head.label}"


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="list other open PR heads as pr-N SHA lines")
    ap.add_argument("pulls", type=Path, help="GitHub pulls JSON (array, or paged arrays)")
    ap.add_argument("--exclude", type=int, default=None, help="this PR's number, omitted on push")
    args = ap.parse_args(argv)
    heads = other_heads(load_pulls(args.pulls.read_text(encoding="utf-8")), args.exclude)
    for h in heads:
        print(h.line())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
