#!/usr/bin/env python3
"""Role-trailer report for a git commit range.

Each commit in the range must carry a Role: trailer whose value is in
the configured role list (flags or ci/hygiene.toml). A missing trailer
or an unknown role is a finding. bot-authored commits may omit it.

Findings name the kind and the commit, never a personal name. This is
a visible report, not a merge gate.

  python3 tools/check_role_trailer.py --base REV [--head REV] [--root DIR]
      [--config PATH] [--role NAME] [--json] [--strict]

exits 0 by default even with findings; --strict exits 1 when any are found.
a git failure or bad config exits 2.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_hygiene as hygiene  # noqa: E402

HygieneConfig = hygiene.HygieneConfig
load_hygiene_config = hygiene.load_hygiene_config
_git = hygiene._git
_rev_list = hygiene._rev_list
_commit_meta = hygiene._commit_meta

ROLE_TRAILER_RE = re.compile(r"(?im)^Role:\s*(\S.*?)\s*$")
_BOT_EMAIL_RE = re.compile(r"(?i)(\[bot\]|dependabot|noreply@github\.com)")


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str


def is_bot_author(name: str, email: str) -> bool:
    folded = (name or "").strip().casefold()
    if folded.endswith("[bot]") or folded in {"dependabot", "dependabot[bot]"}:
        return True
    return bool(_BOT_EMAIL_RE.search((email or "").strip()))


def role_values(body: str) -> list[str]:
    return [m.group(1).strip() for m in ROLE_TRAILER_RE.finditer(body or "")]


def check_commit(rev: str, name: str, email: str, body: str, roles: set[str]) -> list[Finding]:
    where = f"git commit {rev[:12]}"
    if is_bot_author(name, email):
        return []
    values = role_values(body)
    findings: list[Finding] = []
    if not values:
        findings.append(Finding(kind="missing role trailer", where=where))
        return findings
    known = {item.casefold() for item in roles}
    if not any(item.casefold() in known for item in values):
        findings.append(Finding(kind="no known role", where=where))
    for item in values:
        if item.casefold() not in known:
            findings.append(Finding(kind="unknown role", where=where))
    return findings


def scan_range(root: Path, base: str, head: str, cfg: HygieneConfig) -> list[Finding]:
    roles = {item.casefold() for item in cfg.roles}
    findings: list[Finding] = []
    for rev in _rev_list(root, base, head):
        name, email, body = _commit_meta(root, rev)
        findings.extend(check_commit(rev, name, email, body, roles))
    return findings


def to_markdown(findings: list[Finding], base: str, head: str) -> str:
    lines = [f"role-trailer report: {base[:12]}..{head[:12]}", ""]
    if not findings:
        lines.append("no findings.")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"{len(findings)} finding(s):")
    for item in findings:
        lines.append(f"- {item.kind} in {item.where}")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="report missing or unknown Role: commit trailers")
    ap.add_argument("--base", required=True, help="diff base revision")
    ap.add_argument("--head", default="HEAD", help="diff head revision")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="git work tree to scan")
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="toml config (roles); default ci/hygiene.toml if present",
    )
    ap.add_argument(
        "--role",
        action="append",
        default=None,
        dest="roles",
        help="allowed role (repeatable); overrides config",
    )
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--strict", action="store_true", help="exit 1 when there are findings")
    args = ap.parse_args(argv)
    try:
        root = args.root
        cfg = load_hygiene_config(root, args.config, roles=args.roles)
        base = _git(root, "rev-parse", "--verify", f"{args.base}^{{commit}}").decode().strip()
        head = _git(root, "rev-parse", "--verify", f"{args.head}^{{commit}}").decode().strip()
        findings = scan_range(root, base, head, cfg)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check_role_trailer: {exc}", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "base": base,
            "head": head,
            "findings": [asdict(item) for item in findings],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(to_markdown(findings, base, head))
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
