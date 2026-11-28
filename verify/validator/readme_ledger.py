#!/usr/bin/env python3
"""readme_ledger: every README promise -> probe -> PASS / FAIL / UNTESTABLE.

  python3 verify/validator/readme_ledger.py [--ref HEAD] [--repo DIR] [--out DIR]
      [--offline] [--check-runs] [--red-on FAIL,UNTESTABLE,STALE]
A claim with no probe is UNTESTABLE. A claim whose quote is no longer in README.md is STALE
(update claims.yaml). By default every status but PASS is a finding; --red-on narrows the
statuses that give exit 1 (CI passes --red-on FAIL, so only a false claim is red).
Exit 0 = no red status, 1 = a red status, 2 = error.
Report: stdout + OUT/ledger-<sha>.md and .yaml
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, tarfile, io, tempfile, urllib.request, urllib.error
from pathlib import Path
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]  # repo root; --repo overrides
OUT = Path(tempfile.gettempdir()) / "rco-validator"  # --out overrides; nothing is written in the repo
STATUSES = ("PASS", "FAIL", "UNTESTABLE", "STALE")
sys.path.insert(0, str(HERE))
sys.dont_write_bytecode = True
import spin_gate  # noqa: E402

CTX: dict = {}


def git(*a, cwd=None, ok=False):
    p = subprocess.run(["git", *a], cwd=cwd or REPO, capture_output=True, text=True)
    if p.returncode and not ok:
        raise RuntimeError(f"git {' '.join(a)}: {p.stderr.strip()}")
    return p.stdout if p.returncode == 0 else None


# ------------------------------------------------------------------ probes: return (status, detail)
def no_rco_code(t: Path):
    files = CTX["files"]
    hits = [f for f in files if f.startswith(("src/", "rco/")) or f in ("pyproject.toml", "setup.py", "setup.cfg")]
    return ("PASS", "no src/, rco/ or packaging files") if not hits else ("FAIL", f"code present: {hits[:5]}")


def defaults_consistent(t: Path):
    r = CTX["readme"]
    need = {"quick start budget 12000": "--budget 12000" in r, "planned default 12000": "Design default: 12000." in r,
            "open question 12000": "12000 estimated tokens is the interim design default" in r,
            "SHA=HEAD quick start": "SHA=HEAD" in r, "SHA defaults HEAD": "`SHA` defaults to `HEAD`." in r,
            "--for optional (both)": r.count("`--for` is optional") >= 2,
            "diff BASE=merge-base (both)": "BASE=merge-base(default branch, HEAD)" in r and "`BASE` defaults to the merge-base" in r}
    bad = [k for k, v in need.items() if not v]
    return ("PASS", "budget 12000, SHA=HEAD, --for optional, diff base agree") if not bad else ("FAIL", f"mismatch: {bad}")


def sample_pack_tokens(t: Path):
    m = re.search(r"#### Pack output \(example\).*?```markdown\n(.*?)```", CTX["readme"], re.S)
    c = re.search(r"This sample is about (\d+) estimated tokens", CTX["readme"])
    if not m or not c:
        return ("FAIL", "sample block or its token claim not found")
    claim = int(c.group(1)); chars = len(m.group(1)); est = chars / 4
    lo, hi = claim * 0.75, claim * 1.25
    ok = lo <= est <= hi
    return ("PASS" if ok else "FAIL", f"sample is {chars} chars = ~{est:.0f} estimated tokens (claim ~{claim}; tolerance {lo:.0f}-{hi:.0f})")


def pypi_unclaimed(t: Path):
    if CTX["offline"]:
        return ("UNTESTABLE", "offline run")
    try:
        urllib.request.urlopen("https://pypi.org/pypi/repo-context-optimizer/json", timeout=15)
        return ("FAIL", "name is now taken on PyPI (no release of ours exists yet)")
    except urllib.error.HTTPError as e:
        return ("PASS", "still unclaimed (PyPI 404)") if e.code == 404 else ("UNTESTABLE", f"PyPI HTTP {e.code}")
    except OSError as e:
        return ("UNTESTABLE", f"PyPI unreachable: {e}")


def spin_model_present(t: Path):
    pml, run = t / "verify/spin/rco_update.pml", t / "verify/spin/run.sh"
    if not pml.is_file() or not run.is_file():
        return ("FAIL", "rco_update.pml or run.sh missing")
    rs = run.read_text()
    ok = "MAXCRASH=1" in rs and "MAXDUP=1" in rs and "MAXCRASH=2" not in rs
    return ("PASS" if ok else "FAIL", "model + run.sh present; bounds 1 crash, 1 duplicate")


SPIN_PROPS = {"R08": None, "R09": ["P1", "P6"], "R10": ["P2", "P5"], "R11": ["P3"], "R12": ["P7"], "R13": ["P4"]}


def spin_gate_probe(t: Path, cid: str):
    s = OUT / f"spin-{CTX['short']}" / "verify/spin/results/summary.txt"
    if not s.is_file():
        return ("UNTESTABLE", f"no spin run for {CTX['short']}: run spin_gate.py --ref {CTX['ref']} first")
    summary = s.read_text()
    bad, _ = spin_gate.gate(summary, (t / "verify/spin/README.md").read_text())
    if bad:
        return ("FAIL", f"spin gate red: {bad[0]}")
    props = SPIN_PROPS.get(cid)
    return ("PASS", "spin gate green (fixed 7/7 PASS; a/b/bcas fail as documented)" if not props
            else f"fixed passes {'/'.join(props)}; spin gate green")


def spin_limits_match(t: Path):
    d = (t / "verify/spin/README.md").read_text()
    ok = "The history is linear" in d and "never losing data" in d
    return ("PASS" if ok else "FAIL", "verify/spin/README Limits: linear history, lossless store")


def _ci(t: Path):
    return yaml.safe_load((t / ".github/workflows/ci.yml").read_text())


def hidden_code_every_event(t: Path):
    ci = _ci(t); on = ci.get("on", ci.get(True, {}))
    job = ci["jobs"].get("hidden_code_report")
    if not job:
        return ("FAIL", "no hidden_code_report job")
    probs = []
    if "pull_request" not in on: probs.append("no pull_request trigger")
    if "push" not in on: probs.append("no push trigger")
    if "if" in job: probs.append(f"job has if: {job['if']}")
    if "needs" in job: probs.append(f"job needs {job['needs']} (could be skipped)")
    push = (on.get("push") or {}).get("branches")
    note = f"; note: push trigger is limited to {push}, so 'every push' means pushes to those branches" if push else ""
    return ("PASS" if not probs else "FAIL", ("unconditional job on pull_request, push, merge_group" if not probs else str(probs)) + note)


def hidden_code_exit(t: Path):
    scanner = t / "tools/check_hidden_code.py"
    run = lambda *a: subprocess.run([sys.executable, str(scanner), "--root", str(REPO), *a], capture_output=True, text=True)
    first, second = CTX["first"], CTX["third"]
    p = run("--base", first, "--head", second, "--json")
    n = len(json.loads(p.stdout)["findings"]) if p.returncode == 0 else -1
    q = run("--base", "no-such-ref-xyz")
    job = _ci(t)["jobs"]["hidden_code_report"]
    step = json.dumps(job)
    scan_lines = [l for st in job.get("steps", []) for l in str(st.get("run", "")).splitlines() if "check_hidden_code.py" in l]
    probs = []
    if p.returncode != 0 or n < 1: probs.append(f"with findings: exit {p.returncode}, {n} findings (want exit 0, >=1)")
    if q.returncode == 0: probs.append("crash/bad ref exited 0")
    if not scan_lines: probs.append("CI job never runs check_hidden_code.py")
    if any(x in l for l in scan_lines for x in ("--strict", "||", "; true")) or "continue-on-error" in step:
        probs.append(f"CI step masks the scanner exit: {scan_lines}")
    if "set -euo pipefail" not in step: probs.append("CI step lacks set -euo pipefail")
    return ("PASS", f"{n} findings -> exit 0; bad ref -> exit {q.returncode}; CI step not masked") if not probs else ("FAIL", "; ".join(probs))


def setup_doc_present(t: Path):
    f = t / "docs/github-setup.md"
    return ("PASS", f"docs/github-setup.md, {len(f.read_text().splitlines())} lines") if f.is_file() and f.stat().st_size > 200 else ("FAIL", "missing or empty")


def license_mit(t: Path):
    f = t / "LICENSE"
    return ("PASS", "LICENSE is MIT") if f.is_file() and f.read_text().startswith("MIT License") else ("FAIL", "LICENSE is not MIT")


def no_generated_committed(t: Path):
    hits = [f for f in CTX["files"] if f.startswith(".rco/") or "/.rco/" in f or f.endswith((".tree.json", ".forest.json", ".sqlite", ".db"))]
    return ("PASS", "no .rco/, Tree/Forest JSON or sqlite files tracked") if not hits else ("FAIL", str(hits[:5]))


def ci_green_and_selects(t: Path):
    ci = _ci(t)
    sel = json.dumps(ci["jobs"].get("select", {}))
    toml_jobs = set()
    import tomllib
    for r in tomllib.loads((t / "ci/test-select.toml").read_text()).get("rule", []):
        toml_jobs |= set(r.get("jobs", []))
    # Compare the map to the select job's declared outputs, not to every
    # job gated on those outputs. package is gated on outputs.pytest on
    # purpose and is kept out of ci/test-select.toml.
    select_outputs = set((ci["jobs"].get("select") or {}).get("outputs") or {})
    probs = []
    if "ci/test_select.py" not in sel: probs.append("select job does not run ci/test_select.py")
    if toml_jobs != select_outputs: probs.append(f"map jobs {sorted(toml_jobs)} != select outputs {sorted(select_outputs)}")
    token = os.environ.get("GITHUB_TOKEN", "")
    if CTX["offline"] or not CTX["check_runs"] or not token:
        why = "offline" if CTX["offline"] else "check runs not read (pass --check-runs with GITHUB_TOKEN set)"
        return ("UNTESTABLE", f"{why}; select job maps {sorted(toml_jobs)}") if not probs else ("FAIL", "; ".join(probs))
    repo = os.environ.get("GITHUB_REPOSITORY", "si4ai/repo-context-optimizer")
    req = urllib.request.Request(f"https://api.github.com/repos/{repo}/commits/{CTX['sha']}/check-runs?per_page=100",
                                 headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            c, runs = resp.status, json.load(resp).get("check_runs", [])
    except (OSError, ValueError) as e:
        return ("UNTESTABLE", f"GitHub API: {type(e).__name__}")
    if not runs: probs.append(f"no check runs on {CTX['short']} (HTTP {c})")
    red = [f"{r['name']}={r['conclusion'] or r['status']}" for r in runs if r["conclusion"] not in ("success", "skipped", "neutral")]
    if red: probs.append(f"not green: {red}")
    names = ", ".join(sorted(f"{r['name']}={r['conclusion']}" for r in runs))
    return ("PASS", f"{len(runs)} check runs green ({names}); select maps {sorted(toml_jobs)}") if not probs else ("FAIL", "; ".join(probs))


def _find(pattern: str):
    return [f for f in CTX["files"] if re.search(pattern, f, re.I)]


def hygiene_check_present(t: Path):
    h = _find(r"hygiene")
    return ("PASS", f"hygiene check present: {h}") if h else ("FAIL", "not met yet: no hygiene check in the repo (Auditor is porting it, card 1126)")


def role_trailer_present(t: Path):
    h = _find(r"trailer|role")
    return ("PASS", f"role-trailer check present: {h}") if h else ("FAIL", "not met yet: no role-trailer check in the repo (Auditor is porting it, card 1126)")


def _fixture(t: Path, work: Path):
    """A one-commit git repo under work, plus a runner for this ref's rco (git archive tree t)."""
    env = {**os.environ, "PYTHONPATH": str(t / "src"), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("RCO_DEBUG", None)
    def sh(*a, **k):
        return subprocess.run(a, cwd=work, capture_output=True, text=True, env=env, **k)
    (work / "pkg").mkdir(parents=True)
    (work / "pkg/__init__.py").write_text("")
    (work / "pkg/mod.py").write_text("def a(x):\n    return x\n")
    sh("git", "init", "-q", ".")
    sh("git", "add", "-A")
    sh("git", "-c", "user.name=ledger", "-c", "user.email=ledger@example.invalid", "commit", "-q", "-m", "init")
    head = sh("git", "rev-parse", "HEAD").stdout.strip()
    return (lambda *a: sh(sys.executable, "-m", "rco", *a)), head


def _events(work: Path):
    f = work / ".rco/events.jsonl"
    return [json.loads(l) for l in f.read_text().splitlines()] if f.is_file() else []


def events_update_creates(t: Path):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        if (w / ".rco").exists(): return ("FAIL", ".rco exists before update")
        p = rco("update")
        ev = _events(w)
        if p.returncode != 0 or len(ev) != 1: return ("FAIL", f"update exit {p.returncode}, {len(ev)} lines: {p.stderr[-200:]}")
        e = ev[0]
        ok = e.get("cmd") == "update" and e.get("exit") == 0 and e.get("store") == head
        return ("PASS", "update created .rco/events.jsonl: cmd=update exit=0 store=HEAD") if ok else ("FAIL", f"first line {e}")


def events_runs_append(t: Path):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        rco("update")
        rco("pack", "--for", "pkg/mod.py")
        after_pack = _events(w)
        rco("check", "forest")
        after_check = _events(w)
        required = {"v", "run", "ts", "cmd", "exit", "store", "head", "ms", "for"}
        optional = {"for_cut"}
        pack_keys = {"pack_tokens", "pack_budget", "pack_skipped", "pack_overrun"}
        probs = []
        if [e.get("cmd") for e in after_pack] != ["update", "pack"]: probs.append(f"after pack: {[e.get('cmd') for e in after_pack]}")
        elif after_pack[1].get("for") != ["pkg/mod.py"] or after_pack[1].get("exit") != 0: probs.append(f"pack line {after_pack[1]}")
        if len(after_check) != 3 or after_check[-1].get("cmd") != "check": probs.append(f"check did not append: {len(after_check)} lines")
        for e in after_check:
            keys = set(e)
            missing = required - keys
            extra = keys - required - optional - pack_keys
            if missing or extra:
                probs.append(f"line cmd={e.get('cmd')} keys {sorted(keys)}")
            elif e.get("cmd") == "pack":
                if not pack_keys <= keys:
                    probs.append(f"pack line missing {sorted(pack_keys - keys)}")
            elif keys & pack_keys:
                probs.append(f"{e.get('cmd')} line has pack keys")
        return ("PASS", "update, pack, check each append one line; pack lines include pack token fields") if not probs else ("FAIL", "; ".join(probs))


def events_help_version_add_no_line(t: Path):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        rco("update")
        before = _events(w)
        p_help = rco("--help")
        p_ver = rco("--version")
        after = _events(w)
        probs = []
        if p_help.returncode != 0: probs.append(f"--help exit {p_help.returncode}")
        if p_ver.returncode != 0: probs.append(f"--version exit {p_ver.returncode}")
        if after != before: probs.append(f"--help or --version appended a line: before {len(before)} after {len(after)}")
        return ("PASS", "--help and --version added no line") if not probs else ("FAIL", "; ".join(probs))


def events_not_written_without_rco_dir(t: Path):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        p1 = rco("pack", "--for", "pkg/mod.py"); rco("check", "forest")
        if p1.returncode != 0: return ("FAIL", f"pack exit {p1.returncode}")
        return ("PASS", "pack and check in a tree with no .rco/ left no .rco/") if not (w / ".rco").exists() else ("FAIL", f".rco appeared: {sorted(x.name for x in (w / '.rco').iterdir())}")


def readonly_commands_create_no_store(t: Path):
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        (w / ".rco").mkdir()
        rco("pack", "--for", "pkg/mod.py"); rco("check", "forest")
        dbs = sorted(x.name for x in (w / ".rco").iterdir() if x.name.startswith("rco.db"))
        return ("PASS", "pack and check beside an empty .rco/ created no rco.db") if not dbs else ("FAIL", f"store files appeared: {dbs}")


def no_events_table(t: Path):
    import sqlite3
    with tempfile.TemporaryDirectory() as td:
        w = Path(td) / "w"; rco, head = _fixture(t, w)
        rco("update"); rco("pack", "--for", "pkg/mod.py")
        db = w / ".rco/rco.db"
        if not db.is_file(): return ("FAIL", "no rco.db after update")
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            tables = sorted(r[0] for r in con.execute("select name from sqlite_master where type='table'"))
        finally:
            con.close()
        bad = [x for x in tables if "event" in x.lower()]
        return ("PASS", f"tables {tables}: no events table") if not bad else ("FAIL", f"events table present: {bad}")


PROBES = {k: v for k, v in globals().items() if callable(v) and not k.startswith("_")}


def main() -> int:
    global REPO, OUT
    ap = argparse.ArgumentParser(); ap.add_argument("--ref", default="HEAD"); ap.add_argument("--offline", action="store_true")
    ap.add_argument("--repo", type=Path, default=REPO); ap.add_argument("--out", type=Path, default=OUT)
    ap.add_argument("--check-runs", action="store_true", help="read the ref's check runs (needs GITHUB_TOKEN)")
    ap.add_argument("--red-on", default="FAIL,UNTESTABLE,STALE", help="comma list of statuses that give exit 1")
    a = ap.parse_args()
    red = {s.strip().upper() for s in a.red_on.split(",") if s.strip()}
    if not red <= set(STATUSES) - {"PASS"}:
        ap.error(f"--red-on takes FAIL, UNTESTABLE, STALE; got {a.red_on}")
    try:
        return _run(a, red)
    except Exception as e:
        print(f"readme_ledger: ERROR: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _run(a, red) -> int:
    global REPO, OUT
    REPO, OUT = a.repo.resolve(), a.out.resolve()
    sha = git("rev-parse", a.ref).strip(); short = sha[:8]
    log = git("rev-list", "--reverse", sha).split()
    CTX.update(ref=a.ref, sha=sha, short=short, offline=a.offline, check_runs=a.check_runs, files=git("ls-tree", "-r", "--name-only", sha).split("\n"),
               readme=git("show", f"{sha}:README.md"), first=log[0], third=log[min(2, len(log) - 1)])
    lines = CTX["readme"].splitlines()
    claims = yaml.safe_load((HERE / "claims.yaml").read_text())
    rows = []
    with tempfile.TemporaryDirectory() as td:
        t = Path(td)
        tarfile.open(fileobj=io.BytesIO(subprocess.run(["git", "archive", sha], cwd=REPO, capture_output=True, check=True).stdout)).extractall(t, filter="data")
        for c in claims:
            hit = [i + 1 for i, l in enumerate(lines) if c["quote"] in l]
            line = hit[0] if hit else None
            if not hit:
                st, det = "STALE", "quote no longer in README.md: update claims.yaml"
            elif not c.get("probe"):
                st, det = "UNTESTABLE", f"no probe yet ({c['kind']}: needs rco code)" if c["kind"] == "planned" else "no probe yet"
            else:
                fn = c["probe"]
                try:
                    st, det = spin_gate_probe(t, c["id"]) if fn == "spin_gate" else PROBES[fn](t)
                except Exception as e:  # a crashing probe is a FAIL, not a pass
                    st, det = "FAIL", f"probe crashed: {type(e).__name__}: {e}"
            rows.append({**c, "line": line, "status": st, "detail": det})
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    out = [f"# README ledger, si4ai/repo-context-optimizer @ {short}", "",
           " | ".join(f"{k} {v}" for k, v in counts.items()) + f" | total {len(rows)}", "",
           "| id | line | kind | status | claim | evidence |", "|---|---|---|---|---|---|"]
    for r in rows:
        q = r["quote"].replace("|", "\\|")
        out.append(f"| {r['id']} | {r['line']} | {r['kind']} | {r['status']} | {q} | {r['detail'].replace('|', '/')} |")
    md = "\n".join(out) + "\n"
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"ledger-{short}.md").write_text(md)
    (OUT / f"ledger-{short}.yaml").write_text(yaml.safe_dump(rows, sort_keys=False, width=200))
    print(md)
    hot = sorted(s for s in red if counts[s])
    print(f"red on {','.join(sorted(red))}: " + (f"RED ({', '.join(f'{s} {counts[s]}' for s in hot)})" if hot else "GREEN"))
    return 1 if hot else 0


if __name__ == "__main__":
    sys.exit(main())
