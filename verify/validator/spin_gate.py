#!/usr/bin/env python3
"""spin_gate: run verify/spin and gate on its verdicts, independent of run.sh's exit code.

  python3 verify/validator/spin_gate.py [--ref HEAD] [--repo DIR] [--out DIR]
      # export verify/spin at REF into OUT, run run.sh, gate, self-test
  python3 verify/validator/spin_gate.py --summary FILE [--doc verify/spin/README.md]
      # gate an existing summary only
Expected verdicts come from the documented tables in verify/spin/README.md at the same ref,
plus two hard rules from the top README: `fixed` passes all seven properties; a, b and b-cas fail.
Exit 0 = gate green and self-test green, 1 = mismatch (red), 2 = blocked/error.
"""
from __future__ import annotations
import argparse, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # repo root; --repo overrides
OUT = Path(tempfile.gettempdir()) / "rco-validator"  # --out overrides; nothing is written in the repo
PROPS = [f"P{i}" for i in range(1, 8)]
COL = {"a": "a", "b": "b", "b-cas": "bcas", "fixed": "fixed"}
ABL = {"check every module": "bcas_touch", "derived staleness": "bcas_derived",
       "queue the rewrite after the pointer": "bcas_qafter"}
ROW = re.compile(r"^(\S+)\s+(P\d)\s+(PASS|FAIL)\s+(\S+)")


def expected_from_doc(doc: str) -> tuple[dict, dict]:
    exp, states = {}, {}
    for line in doc.splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        m = re.match(r"(P\d) ", cells[0]) if cells else None
        if m and len(cells) == 5:  # main table: property | a | b | b-cas | fixed
            for name, cell in zip(("a", "b", "b-cas", "fixed"), cells[1:]):
                v = COL[name]
                exp[(v, m.group(1))] = "FAIL" if "FAIL" in cell else "PASS"
                n = re.search(r"\(([\d,]+)\)", cell)
                if n:
                    states[(v, m.group(1))] = int(n.group(1).replace(",", ""))
        elif cells and cells[0].startswith("b-cas + ") and len(cells) == 8:
            v = ABL.get(cells[0][len("b-cas + "):])
            if v:
                for p, cell in zip(PROPS, cells[1:]):
                    exp[(v, p)] = cell
    return exp, states


def gate(summary: str, doc: str) -> tuple[list[str], list[str]]:
    exp, states = expected_from_doc(doc)
    bad, notes = [], []
    if len(exp) != 49:
        bad.append(f"could parse only {len(exp)}/49 documented verdicts from verify/spin/README.md")
    got, seen_st = {}, {}
    for line in summary.splitlines():
        if re.search(r"ERROR|spin failed", line):
            bad.append(f"tool error line: {line.strip()}")
        m = ROW.match(line)
        if m:
            k = (m.group(1), m.group(2))
            if k in got:
                bad.append(f"duplicate row {k}")
            got[k] = m.group(3); seen_st[k] = m.group(4)
    for k, v in sorted(exp.items()):
        if k not in got:
            bad.append(f"missing result {k[0]} {k[1]} (documented {v})")
        elif got[k] != v:
            bad.append(f"{k[0]} {k[1]}: got {got[k]}, README documents {v}")
    for k in sorted(set(got) - set(exp)):
        bad.append(f"undocumented result {k[0]} {k[1]} = {got[k]}")
    # hard rules from the top README (independent of the spin README tables)
    for p in PROPS:
        if got.get(("fixed", p)) != "PASS":
            bad.append(f"top README rule: fixed must PASS {p}, got {got.get(('fixed', p))}")
    for v in ("a", "b", "bcas"):
        if not any(got.get((v, p)) == "FAIL" for p in PROPS):
            bad.append(f"top README rule: variant {v} must fail at least one property")
    if any(got.get(("a", p)) != "FAIL" for p in PROPS):
        bad.append("top README: 'the design as first written failed all seven' (variant a) does not hold")
    for k, n in states.items():
        if k in seen_st and seen_st[k].isdigit() and int(seen_st[k]) != n:
            notes.append(f"state count {k[0]} {k[1]}: got {seen_st[k]}, README says {n}")
    if not re.search(r"^spin: Spin Version", summary, re.M):
        bad.append("no 'spin: Spin Version' footer: run did not finish")
    return bad, notes


def selftest(summary: str, doc: str) -> list[str]:
    """Each tampered summary must turn the gate red."""
    fails = []
    def flip(text, v, p, to):
        return re.sub(rf"^({re.escape(v)}\s+{p}\s+)(PASS|FAIL)", lambda m: m.group(1) + to.ljust(len(m.group(2))), text, count=1, flags=re.M)
    cases = {
        "fixed P1 -> FAIL": flip(summary, "fixed", "P1", "FAIL"),
        "a P1 -> PASS": flip(summary, "a", "P1", "PASS"),
        "bcas_touch P3 -> PASS": flip(summary, "bcas_touch", "P3", "PASS"),
        "drop fixed P7 row": re.sub(r"^fixed\s+P7 .*\n", "", summary, flags=re.M),
        "gcc ERROR row": summary + "fixed        P8   ERROR gcc (see x)\n",
        "truncated (no footer)": re.sub(r"^spin: .*\n?", "", summary, flags=re.M),
    }
    for name, text in cases.items():
        if text == summary:
            fails.append(f"self-test case '{name}' did not change the summary")
        elif not gate(text, doc)[0]:
            fails.append(f"self-test case '{name}' stayed GREEN")
    return fails


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", default="HEAD"); ap.add_argument("--summary", type=Path)
    ap.add_argument("--doc", type=Path)
    ap.add_argument("--repo", type=Path, default=REPO); ap.add_argument("--out", type=Path, default=OUT)
    a = ap.parse_args()
    repo = a.repo.resolve()
    p = subprocess.run(["git", "rev-parse", "--verify", "--short=8", f"{a.ref}^{{commit}}"], cwd=repo, capture_output=True, text=True)
    if p.returncode:
        print(f"spin_gate: ERROR: cannot resolve {a.ref}: {p.stderr.strip()}"); return 2
    sha = p.stdout.strip()
    if a.summary:
        summary = a.summary.read_text()
        doc = a.doc.read_text() if a.doc else subprocess.run(["git", "show", f"{a.ref}:verify/spin/README.md"], cwd=repo, capture_output=True, text=True).stdout
        run_rc = None
    else:
        if not shutil.which("spin") or not shutil.which("gcc"):
            print("spin_gate: BLOCKED: spin or gcc not on PATH"); return 2
        work = a.out.resolve() / f"spin-{sha}"
        shutil.rmtree(work, ignore_errors=True); work.mkdir(parents=True)
        arc = subprocess.run(["git", "archive", a.ref, "verify/spin"], cwd=repo, capture_output=True, check=True).stdout
        subprocess.run(["tar", "-x", "-C", str(work)], input=arc, check=True)
        d = work / "verify/spin"; t0 = time.time()
        p = subprocess.run(["bash", "run.sh"], cwd=d, capture_output=True, text=True)
        run_rc = p.returncode
        print(f"spin_gate: ran verify/spin/run.sh at {sha} in {time.time()-t0:.0f}s, run.sh exit {run_rc}")
        sf = d / "results/summary.txt"
        if not sf.is_file():
            print(f"spin_gate: ERROR: run.sh exit {run_rc} wrote no results/summary.txt"); print(p.stderr[-2000:]); return 2
        summary = sf.read_text()
        doc = (d / "README.md").read_text()
    bad, notes = gate(summary, doc)
    rows = sum(1 for l in summary.splitlines() if ROW.match(l))
    print(f"spin_gate {sha}: {rows} result rows; gate {'RED' if bad else 'GREEN'}")
    for b in bad: print("  MISMATCH:", b)
    for n in notes: print("  note:", n)
    st = selftest(summary, doc) if not bad else ["skipped: gate already red on the real summary"]
    print("  self-test (tampered summaries must go red):", "PASS (6/6 red)" if not st else "FAIL")
    for s in st: print("   ", s)
    if run_rc is not None and (run_rc == 0) == bool(bad):
        print(f"  note: run.sh exit {run_rc} disagrees with the gate ({'RED' if bad else 'GREEN'})")
    return 1 if bad or st else 0


if __name__ == "__main__":
    sys.exit(main())
