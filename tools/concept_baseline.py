#!/usr/bin/env python3
"""Concept labels and the no-ML baseline for card 1338 (rco-concept-model).

Step 1 of the card is a labelled set: module directories and a sample of
files in public repositories, each tagged with concept ids from
docs/baseline/concepts/taxonomy.json. Step 2 is this baseline: a
deterministic scorer from folder and file names, an import lexicon, and
the repo's own import graph. No model, no LLM, no network. Standard
library only; git is the only outside program.

  python3 tools/concept_baseline.py check  [--dir DIR]
  python3 tools/concept_baseline.py fetch  --clones CLONES [--dir DIR] [--repo ID]
  python3 tools/concept_baseline.py items  --clones CLONES [--dir DIR] [--repo ID]
  python3 tools/concept_baseline.py auto   --clones CLONES [--dir DIR] [--repo ID]
  python3 tools/concept_baseline.py score  --clones CLONES [--dir DIR]

DIR defaults to docs/baseline/concepts. CLONES holds one checkout per
corpus id (CLONES/<id>), each at the corpus sha; `score` and `items`
refuse a checkout whose HEAD is not that sha.

check  Validates taxonomy, corpus and labels: unique ids, known concept
       ids, provenance auto or judged, every repo in exactly one split.
fetch  Shallow-fetches each corpus repo at its pinned sha into
       CLONES/<id> (the only subcommand that uses the network; skips a
       checkout already at the pin).
items  Lists the items to label for a repo (modules, then sampled files)
       with evidence: names, docstring first line, top-level names,
       imports. Prints JSON.
auto   Prints the auto labels for a repo: concepts whose `names` hint
       equals the item's own directory name or file stem.
score  Runs the baseline in four configurations (names; names plus the
       import lexicon; plus import-graph neighbours; or plus import
       clusters from label propagation on the pruned graph),
       picks one threshold per configuration on the train split, and
       reports precision, recall and F1 per concept plus micro and macro
       on each held-out set, and how import clusters line up with
       folders per repo (the forest). Prints JSON.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path, PurePosixPath

DEFAULT_DIR = Path(__file__).resolve().parent.parent / "docs" / "baseline" / "concepts"
CODE_EXT = {".py", ".js", ".mjs", ".cjs", ".ts", ".go"}
SKIP_PARTS = {
    "docs", "doc", "docs_src", "examples", "example", "benchmarks", "bench",
    "_vendor", "vendor", "third_party", "node_modules", "fixtures", "testdata",
    "data", "dist", "build", "changelog.d", "news", ".github", "sandbox",
    "typings", "stubs",
}
MAX_MODULES = 15
MAX_TEST_MODULES = 3  # of those, at most this many under a test directory
# Files: at most a quarter of the sample is test files, unless the repo has too
# few other files.
FILES_STRUCTURED = 12
FILES_FLAT = 20
MIN_FILE_BYTES = 200
PROVENANCE = ("auto", "judged")
SPLITS = ("train", "heldout")
LAYOUTS = ("structured", "flat")
# Signal weights. Fixed by hand before any score was read; only the
# threshold is fitted, on the train split.
W_STEM = 1.0      # the item's own name (file stem, or directory name)
W_PARENT = 0.5    # nearest parent directory of a file
W_ANCESTOR = 0.25  # any higher directory
W_MEMBER = 0.5    # module: mean of its files' name scores
W_IMPORT = 0.5    # an import listed in a concept's `imports`
W_GRAPH = 0.5     # mean name score of import neighbours, or of the import cluster
CONFIGS = ("names", "names+imports", "names+imports+graph", "names+imports+clusters")
THRESHOLDS = [round(0.25 + 0.05 * i, 2) for i in range(36)]  # 0.25 .. 2.0


# ---------------------------------------------------------------- data

def load(dirpath: Path) -> tuple[dict, dict, dict[str, dict]]:
    tax = json.loads((dirpath / "taxonomy.json").read_text(encoding="utf-8"))
    corpus = json.loads((dirpath / "corpus.json").read_text(encoding="utf-8"))
    labels = {}
    ldir = dirpath / "labels"
    if ldir.is_dir():
        for p in sorted(ldir.glob("*.json")):
            labels[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return tax, corpus, labels


def check(tax: dict, corpus: dict, labels: dict[str, dict]) -> list[str]:
    errs: list[str] = []
    ids = [c["id"] for c in tax["concepts"]]
    if len(ids) != len(set(ids)):
        errs.append("taxonomy: duplicate concept id")
    known = set(ids)
    for c in tax["concepts"]:
        for k in ("id", "group", "definition", "source"):
            if not c.get(k):
                errs.append(f"taxonomy: {c.get('id')} lacks {k}")
    if not 30 <= len(ids) <= 60:
        errs.append(f"taxonomy: {len(ids)} concepts, expected 30-60")
    if corpus.get("taxonomy_version") != tax.get("version"):
        errs.append("corpus: taxonomy_version does not match taxonomy version")
    rids = [r["id"] for r in corpus["repos"]]
    if len(rids) != len(set(rids)):
        errs.append("corpus: duplicate repo id")
    for r in corpus["repos"]:
        if r.get("split") not in SPLITS:
            errs.append(f"corpus: {r['id']} split {r.get('split')!r}")
        if r.get("layout") not in LAYOUTS:
            errs.append(f"corpus: {r['id']} layout {r.get('layout')!r}")
        if not re.fullmatch(r"[0-9a-f]{40}", r.get("sha", "")):
            errs.append(f"corpus: {r['id']} sha is not a full commit id")
        if not r.get("licence") or r["licence"] in ("NOASSERTION", "None"):
            errs.append(f"corpus: {r['id']} licence missing")
        if r.get("layout") == "flat" and r.get("split") != "heldout":
            errs.append(f"corpus: {r['id']} is flat but not held out")
        if r["id"] not in labels:
            errs.append(f"labels: {r['id']} has no label file")
    for rid, lab in labels.items():
        if rid not in rids:
            errs.append(f"labels: {rid} is not in the corpus")
            continue
        sha = next(r["sha"] for r in corpus["repos"] if r["id"] == rid)
        if lab.get("sha") != sha:
            errs.append(f"labels: {rid} sha differs from corpus")
        seen = set()
        for it in lab.get("items", []):
            key = (it.get("kind"), it.get("path"))
            if key in seen:
                errs.append(f"labels: {rid} duplicate item {key}")
            seen.add(key)
            if it.get("kind") not in ("module", "file"):
                errs.append(f"labels: {rid} {it.get('path')} kind")
            if not it.get("concepts"):
                errs.append(f"labels: {rid} {it.get('path')} has no concept")
            for c in it.get("concepts", []):
                if c.get("id") not in known:
                    errs.append(f"labels: {rid} {it.get('path')} unknown concept {c.get('id')}")
                if c.get("prov") not in PROVENANCE:
                    errs.append(f"labels: {rid} {it.get('path')} provenance {c.get('prov')!r}")
            if not it.get("evidence"):
                errs.append(f"labels: {rid} {it.get('path')} has no evidence note")
    return errs


# ---------------------------------------------------------------- names

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def tokens(name: str) -> list[str]:
    """Split one path part into lowercase tokens; the whole part is kept too."""
    stem = name
    for ext in CODE_EXT:
        if stem.endswith(ext):
            stem = stem[: -len(ext)]
            break
    if stem.endswith(".test") or stem.endswith(".spec"):
        stem = stem.rsplit(".", 1)[0] + "_test"
    out = [stem.lower()]
    for part in re.split(r"[._\-]+", _CAMEL.sub("_", stem)):
        p = part.lower()
        if p and p not in out:
            out.append(p)
    if stem.startswith("test_") or stem.endswith("_test"):
        out.append("test")
    return out


def name_index(tax: dict) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for c in tax["concepts"]:
        for n in c["names"]:
            idx[n].append(c["id"])
    return dict(idx)


def import_index(tax: dict) -> dict[str, list[str]]:
    idx: dict[str, list[str]] = defaultdict(list)
    for c in tax["concepts"]:
        for n in c["imports"]:
            idx[n].append(c["id"])
    return dict(idx)


def hits(part: str, nidx: dict[str, list[str]]) -> set[str]:
    found: set[str] = set()
    for t in tokens(part):
        found.update(nidx.get(t, ()))
    return found


def auto_concepts(item_path: str, kind: str, nidx: dict[str, list[str]]) -> list[str]:
    """Exact match of the item's own directory name or file stem."""
    p = PurePosixPath(item_path)
    stem = p.name if kind == "module" else tokens(p.name)[0]
    return sorted(set(nidx.get(stem.lower(), ())))


# ---------------------------------------------------------------- repo walk

def git(clone: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(clone), *args], check=True,
                          capture_output=True, text=True).stdout


def code_files(clone: Path) -> list[str]:
    out = []
    for p in git(clone, "ls-files", "-z").split("\0"):
        if not p:
            continue
        pp = PurePosixPath(p)
        if pp.suffix not in CODE_EXT or p.endswith(".d.ts"):
            continue
        if any(part in SKIP_PARTS or part.startswith(".") for part in pp.parts[:-1]):
            continue
        out.append(p)
    return sorted(out)


def sample_key(repo_id: str, path: str) -> str:
    return hashlib.sha256(f"{repo_id}\0{path}".encode()).hexdigest()


TEST_PARTS = ("test", "tests", "testing", "__tests__")


def is_test_path(path: str) -> bool:
    pp = PurePosixPath(path)
    return any(part in TEST_PARTS for part in pp.parts[:-1]) or "test" in tokens(pp.name)


def select_items(repo: dict, clone: Path) -> tuple[list[str], list[str], list[str]]:
    """Return (all code files, module dirs, sampled files), deterministically."""
    files = code_files(clone)
    per_dir: dict[str, int] = defaultdict(int)
    for f in files:
        per_dir[str(PurePosixPath(f).parent)] += 1
    ranked = sorted((d for d, n in per_dir.items() if n >= 2 and d != "."),
                    key=lambda d: (-per_dir[d], d))
    mods, n_test = [], 0
    for d in ranked:
        is_test = any(part in TEST_PARTS for part in PurePosixPath(d).parts)
        if is_test and n_test >= MAX_TEST_MODULES:
            continue
        n_test += is_test
        mods.append(d)
        if len(mods) == MAX_MODULES:
            break
    mods = sorted(mods)
    want = FILES_FLAT if repo["layout"] == "flat" else FILES_STRUCTURED
    cand = []
    for f in files:
        try:
            size = (clone / f).stat().st_size
        except OSError:
            continue
        if size >= MIN_FILE_BYTES:
            cand.append(f)
    ranked_files = sorted(cand, key=lambda f: sample_key(repo["id"], f))
    tests = [f for f in ranked_files if is_test_path(f)]
    other = [f for f in ranked_files if not is_test_path(f)]
    n_test = max(want // 4, want - len(other))
    sample = sorted(other[: want - min(n_test, len(tests))] + tests[:n_test])
    return files, mods, sample


# ---------------------------------------------------------------- imports

_JS_IMPORT = re.compile(r"""(?:require\(\s*|from\s+|import\s+)['"]([^'"]+)['"]""")
_GO_IMPORT_BLOCK = re.compile(r"import\s*\(([^)]*)\)", re.S)
_GO_IMPORT_ONE = re.compile(r'import\s+(?:\w+\s+)?"([^"]+)"')
_GO_PATH = re.compile(r'"([^"]+)"')


def read(clone: Path, path: str) -> str:
    try:
        return (clone / path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def py_imports(src: str, path: str) -> list[tuple[str, int]]:
    """(dotted name, relative level) for each import in a Python file."""
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((a.name, 0) for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            out.append((base, node.level))
            out.extend(((base + "." if base else "") + a.name, node.level)
                       for a in node.names if a.name != "*")
    return out


def py_module_map(files: list[str]) -> dict[str, str]:
    """Dotted module name -> file, for roots '', 'src', 'lib'."""
    m: dict[str, str] = {}
    for f in files:
        if not f.endswith(".py"):
            continue
        parts = list(PurePosixPath(f).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for root in ("src", "lib"):
            if parts and parts[0] == root and len(parts) > 1:
                m.setdefault(".".join(parts[1:]), f)
        if parts:
            m.setdefault(".".join(parts), f)
    return m


def resolve_py(name: str, level: int, path: str, mmap: dict[str, str]) -> str | None:
    if level:
        pkg = list(PurePosixPath(path).parent.parts)
        if level > 1:
            pkg = pkg[: len(pkg) - (level - 1)]
        for root in ("src", "lib"):
            if pkg and pkg[0] == root:
                pkg = pkg[1:]
                break
        name = ".".join([*pkg, name] if name else pkg)
    while name:
        if name in mmap:
            return mmap[name]
        name = name.rpartition(".")[0]
    return None


def graph_and_externals(clone: Path, files: list[str]) -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    """Undirected intra-repo import graph and each file's external import names."""
    fileset = set(files)
    mmap = py_module_map(files)
    gomod = ""
    if (clone / "go.mod").is_file():
        mm = re.search(r"^module\s+(\S+)", read(clone, "go.mod"), re.M)
        gomod = mm.group(1) if mm else ""
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_dir[str(PurePosixPath(f).parent)].append(f)
    edges: dict[str, set[str]] = {f: set() for f in files}
    ext: dict[str, set[str]] = {f: set() for f in files}

    def link(a: str, b: str | None) -> None:
        if b and b != a and b in fileset:
            edges[a].add(b)
            edges[b].add(a)

    for f in files:
        src = read(clone, f)
        suf = PurePosixPath(f).suffix
        if suf == ".py":
            for name, level in py_imports(src, f):
                tgt = resolve_py(name, level, f, mmap)
                if tgt:
                    link(f, tgt)
                elif level == 0 and name:
                    ext[f].add(name.split(".")[0])
                    if "." in name:
                        ext[f].add(".".join(name.split(".")[:2]))
        elif suf == ".go":
            specs = []
            for blk in _GO_IMPORT_BLOCK.findall(src):
                specs.extend(_GO_PATH.findall(blk))
            specs.extend(_GO_IMPORT_ONE.findall(src))
            for s in specs:
                if gomod and (s == gomod or s.startswith(gomod + "/")):
                    d = s[len(gomod):].lstrip("/") or "."
                    for t in by_dir.get(d, ()):
                        link(f, t)
                else:
                    ext[f].add(s)
                    ext[f].add(s.split("/")[0])
        else:
            for s in _JS_IMPORT.findall(src):
                if s.startswith("."):
                    base = PurePosixPath(f).parent / s
                    cands = [str(base)] + [f"{base}{e}" for e in (".js", ".ts", ".mjs", ".cjs")] \
                        + [f"{base}/index{e}" for e in (".js", ".ts")]
                    for c in cands:
                        c = str(PurePosixPath(*[p for p in PurePosixPath(c).parts]))
                        norm = _normpath(c)
                        if norm in fileset:
                            link(f, norm)
                            break
                else:
                    ext[f].add(s.split("/")[0])
    return edges, ext


def _normpath(p: str) -> str:
    out: list[str] = []
    for part in p.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if out:
                out.pop()
            continue
        out.append(part)
    return "/".join(out)


# ---------------------------------------------------------------- clusters

LP_ROUNDS = 20


def clusters(edges: dict[str, set[str]]) -> dict[str, str]:
    """Deterministic label propagation over the undirected import graph.

    Every file starts as its own label. In sorted file order, each file
    takes the label most common among its neighbours (ties: the smallest
    label), for at most LP_ROUNDS rounds or until nothing changes. A file
    with no intra-repo imports stays alone. Returns file -> cluster label.
    """
    label = {f: f for f in edges}
    for _ in range(LP_ROUNDS):
        changed = False
        for f in sorted(edges):
            nb = edges[f]
            if not nb:
                continue
            count: dict[str, int] = defaultdict(int)
            for n in nb:
                count[label[n]] += 1
            best = min(count, key=lambda k: (-count[k], k))
            if count[best] > count.get(label[f], 0) and best != label[f]:
                label[f] = best
                changed = True
        if not changed:
            break
    return label


FACADES = ("__init__.py", "index.js", "index.ts")
HUB_MIN = 8      # a file is a hub above max(HUB_MIN, HUB_X * median degree)
HUB_X = 3


def prune(edges: dict[str, set[str]]) -> dict[str, set[str]]:
    """Drop edges that join everything to everything before clustering:
    package facades (__init__.py, index.js) that re-export a package, then
    hubs whose degree is far above the repo's median."""
    fac = {f for f in edges if PurePosixPath(f).name in FACADES}
    e1 = {f: (set() if f in fac else {n for n in v if n not in fac}) for f, v in edges.items()}
    degs = sorted(len(v) for v in e1.values() if v)
    if not degs:
        return e1
    mid = degs[(len(degs) - 1) // 2]
    cap = max(HUB_MIN, HUB_X * mid)
    hub = {f for f, v in e1.items() if len(v) > cap}
    return {f: (set() if f in hub else {n for n in v if n not in hub}) for f, v in e1.items()}


def forest_stats(files: list[str], edges: dict[str, set[str]]) -> dict:
    """How import clusters line up with folders: purity is, over files in
    clusters of two or more, the share that sit in their cluster's most
    common directory."""
    lab = clusters(edges)
    groups: dict[str, list[str]] = defaultdict(list)
    for f, l in lab.items():
        groups[l].append(f)
    multi = [g for g in groups.values() if len(g) >= 2]
    in_multi = sum(len(g) for g in multi)
    agree = 0
    for g in multi:
        dirs: dict[str, int] = defaultdict(int)
        for f in g:
            dirs[str(PurePosixPath(f).parent)] += 1
        agree += max(dirs.values())
    return {"files": len(files), "clusters": len(multi),
            "clustered_share": round(in_multi / len(files), 3) if files else 0.0,
            "folder_purity": round(agree / in_multi, 3) if in_multi else 0.0,
            "folders": len({str(PurePosixPath(f).parent) for f in files})}


# ---------------------------------------------------------------- scorer

def file_name_scores(path: str, nidx: dict[str, list[str]]) -> dict[str, float]:
    p = PurePosixPath(path)
    s: dict[str, float] = defaultdict(float)
    for c in hits(p.name, nidx):
        s[c] = max(s[c], W_STEM)
    parents = list(p.parent.parts)
    for i, part in enumerate(reversed(parents)):
        w = W_PARENT if i == 0 else W_ANCESTOR
        for c in hits(part, nidx):
            s[c] = max(s[c], w)
    return dict(s)


def score_repo(repo: dict, clone: Path, tax: dict, items: list[dict], config: str) -> dict[tuple[str, str], dict[str, float]]:
    """Concept scores for each labelled (kind, path) under one configuration."""
    nidx, iidx = name_index(tax), import_index(tax)
    files = code_files(clone)
    edges, ext = graph_and_externals(clone, files) if config != "names" else ({}, {})
    fname = {f: file_name_scores(f, nidx) for f in files}
    members_of: dict[str, list[str]] = defaultdict(list)
    lab: dict[str, str] = {}
    if config == "names+imports+clusters":
        lab = clusters(prune(edges))
        for f, l in lab.items():
            members_of[l].append(f)

    def file_score(f: str) -> dict[str, float]:
        s = defaultdict(float, fname.get(f) or file_name_scores(f, nidx))
        if config != "names":
            for e in ext.get(f, ()):
                for c in iidx.get(e, ()):
                    s[c] += W_IMPORT / 2 if s[c] else W_IMPORT
        nb: list[str] = []
        if config == "names+imports+graph":
            nb = sorted(edges.get(f, ()))
        elif config == "names+imports+clusters" and f in lab:
            nb = [m for m in members_of[lab[f]] if m != f]
        if nb:
            agg: dict[str, float] = defaultdict(float)
            for n in nb:
                for c, v in fname[n].items():
                    agg[c] += v
            for c, v in agg.items():
                s[c] += W_GRAPH * v / len(nb)
        return dict(s)

    out: dict[tuple[str, str], dict[str, float]] = {}
    for it in items:
        if it["kind"] == "file":
            out[("file", it["path"])] = file_score(it["path"])
        else:
            d = it["path"]
            s: dict[str, float] = defaultdict(float)
            for c in hits(PurePosixPath(d).name, nidx):
                s[c] = max(s[c], W_STEM)
            for part in PurePosixPath(d).parent.parts:
                for c in hits(part, nidx):
                    s[c] = max(s[c], W_ANCESTOR)
            members = [f for f in files if str(PurePosixPath(f).parent) == d]
            if members:
                agg: dict[str, float] = defaultdict(float)
                for f in members:
                    fs = file_score(f)
                    for c in fs:
                        agg[c] += fs[c]
                for c, v in agg.items():
                    s[c] += W_MEMBER * v / len(members)
            out[("module", d)] = dict(s)
    return out


def prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else 0.0
    r = tp / (tp + fn) if tp + fn else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 3), "recall": round(r, 3), "f1": round(f, 3),
            "tp": tp, "fp": fp, "fn": fn}


def evaluate(gold: dict, scores: dict, threshold: float, concept_ids: list[str]) -> dict:
    """gold, scores: {(repo, kind, path): set | dict}. Micro over all decisions,
    macro over concepts with gold support in this set."""
    tp: dict[str, int] = defaultdict(int)
    fp: dict[str, int] = defaultdict(int)
    fn: dict[str, int] = defaultdict(int)
    for key, g in gold.items():
        pred = {c for c, v in scores.get(key, {}).items() if v >= threshold}
        for c in pred & g:
            tp[c] += 1
        for c in pred - g:
            fp[c] += 1
        for c in g - pred:
            fn[c] += 1
    per = {c: prf(tp[c], fp[c], fn[c]) for c in concept_ids if tp[c] or fp[c] or fn[c]}
    supported = [c for c in concept_ids if tp[c] + fn[c] > 0]
    micro = prf(sum(tp.values()), sum(fp.values()), sum(fn.values()))
    macro = {
        k: round(sum(per[c][k] for c in supported) / len(supported), 3) if supported else 0.0
        for k in ("precision", "recall", "f1")
    }
    return {"items": len(gold), "micro": micro, "macro": macro,
            "macro_over": len(supported), "per_concept": per}


def gold_of(labels: dict[str, dict], repo_ids: list[str], only_judged_items: bool = False) -> dict:
    g = {}
    for rid in repo_ids:
        for it in labels[rid]["items"]:
            cs = {c["id"] for c in it["concepts"]}
            if only_judged_items and not any(c["prov"] == "judged" for c in it["concepts"]):
                continue
            g[(rid, it["kind"], it["path"])] = cs
    return g


def run_score(dirpath: Path, clones: Path) -> dict:
    tax, corpus, labels = load(dirpath)
    errs = check(tax, corpus, labels)
    if errs:
        raise SystemExit("check failed:\n" + "\n".join(errs))
    cids = [c["id"] for c in tax["concepts"]]
    repos = {r["id"]: r for r in corpus["repos"]}
    for rid, r in repos.items():
        if not (clones / rid / ".git").is_dir():
            raise SystemExit(f"{rid}: no checkout at {clones / rid}; run the fetch subcommand first")
        head = git(clones / rid, "rev-parse", "HEAD").strip()
        if head != r["sha"]:
            raise SystemExit(f"{rid}: checkout is at {head}, corpus pins {r['sha']}")
    sets = {
        "train": [r for r in repos if repos[r]["split"] == "train"],
        "heldout_structured": [r for r in repos if repos[r]["split"] == "heldout" and repos[r]["layout"] == "structured"],
        "heldout_flat": [r for r in repos if repos[r]["split"] == "heldout" and repos[r]["layout"] == "flat"],
    }
    result: dict = {"taxonomy_version": tax["version"], "corpus_version": corpus["version"],
                    "weights": {"stem": W_STEM, "parent": W_PARENT, "ancestor": W_ANCESTOR,
                                "member": W_MEMBER, "import": W_IMPORT, "graph": W_GRAPH},
                    "sets": {k: v for k, v in sets.items()}, "configs": {}}
    for config in CONFIGS:
        scores = {}
        for rid, r in repos.items():
            for (kind, path), s in score_repo(r, clones / rid, tax, labels[rid]["items"], config).items():
                scores[(rid, kind, path)] = s
        train_gold = gold_of(labels, sets["train"])
        best = max(THRESHOLDS, key=lambda t: (evaluate(train_gold, scores, t, cids)["micro"]["f1"], -t))
        res = {"threshold": best}
        for name, rids in sets.items():
            res[name] = evaluate(gold_of(labels, rids), scores, best, cids)
            res[name + "_judged_items"] = evaluate(gold_of(labels, rids, True), scores, best, cids)
        result["configs"][config] = res
    result["forest"] = {}
    for rid, r in sorted(repos.items()):
        files = code_files(clones / rid)
        edges, _ = graph_and_externals(clones / rid, files)
        result["forest"][rid] = {"split": r["split"], "layout": r["layout"],
                                 "raw": forest_stats(files, edges),
                                 "pruned": forest_stats(files, prune(edges))}
    return result


# ---------------------------------------------------------------- evidence

def evidence(clone: Path, path: str, kind: str, files: list[str]) -> dict:
    if kind == "module":
        members = [f for f in files if str(PurePosixPath(f).parent) == path]
        init = next((f for f in members if PurePosixPath(f).name in ("__init__.py", "index.js", "index.ts", "doc.go")), None)
        return {"files": [PurePosixPath(f).name for f in members][:25], "n": len(members),
                "doc": _doc(read(clone, init), init) if init else ""}
    src = read(clone, path)
    return {"doc": _doc(src, path), "defs": _defs(src, path)[:10], "lines": src.count("\n")}


def _doc(src: str, path: str) -> str:
    if path.endswith(".py"):
        try:
            d = ast.get_docstring(ast.parse(src)) or ""
        except (SyntaxError, ValueError):
            d = ""
        return d.strip().splitlines()[0][:160] if d.strip() else ""
    for line in src.splitlines()[:30]:
        s = line.strip().lstrip("/*# ").strip()
        if len(s) > 12 and not s.startswith(("package", "import", "'use", '"use')):
            return s[:160]
    return ""


def _defs(src: str, path: str) -> list[str]:
    if path.endswith(".py"):
        try:
            tree = ast.parse(src)
        except (SyntaxError, ValueError):
            return []
        return [n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    return re.findall(r"^(?:export\s+)?(?:async\s+)?(?:function|class|func|type)\s+(?:\([^)]*\)\s*)?(\w+)", src, re.M)


def run_items(dirpath: Path, clones: Path, only: str | None, with_auto: bool) -> dict:
    tax, corpus, _ = load(dirpath)
    nidx = name_index(tax)
    out = {}
    for r in corpus["repos"]:
        if only and r["id"] != only:
            continue
        clone = clones / r["id"]
        head = git(clone, "rev-parse", "HEAD").strip()
        if head != r["sha"]:
            raise SystemExit(f"{r['id']}: checkout is at {head}, corpus pins {r['sha']}")
        files, mods, sample = select_items(r, clone)
        edges, ext = graph_and_externals(clone, files)
        items = []
        for kind, paths in (("module", mods), ("file", sample)):
            for p in paths:
                it = {"kind": kind, "path": p}
                if with_auto:
                    it["auto"] = auto_concepts(p, kind, nidx)
                else:
                    it["evidence"] = evidence(clone, p, kind, files)
                    if kind == "file":
                        it["evidence"]["imports"] = sorted(ext.get(p, ()))[:12]
                        it["evidence"]["links"] = len(edges.get(p, ()))
                items.append(it)
        out[r["id"]] = {"sha": r["sha"], "files": len(files), "items": items}
    return out


def run_fetch(dirpath: Path, clones: Path, only: str | None) -> dict:
    _, corpus, _ = load(dirpath)
    done = {}
    for r in corpus["repos"]:
        if only and r["id"] != only:
            continue
        dest = clones / r["id"]
        if (dest / ".git").is_dir() and git(dest, "rev-parse", "HEAD").strip() == r["sha"]:
            done[r["id"]] = "present"
            continue
        dest.mkdir(parents=True, exist_ok=True)
        git(dest, "init", "-q")
        git(dest, "fetch", "-q", "--depth", "1", r["url"] + ".git", r["sha"])
        git(dest, "-c", "advice.detachedHead=false", "checkout", "-q", "FETCH_HEAD")
        done[r["id"]] = "fetched"
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("cmd", choices=["check", "fetch", "items", "auto", "score"])
    ap.add_argument("--dir", type=Path, default=DEFAULT_DIR)
    ap.add_argument("--clones", type=Path)
    ap.add_argument("--repo")
    a = ap.parse_args(argv)
    if a.cmd == "check":
        errs = check(*load(a.dir))
        print(json.dumps({"ok": not errs, "errors": errs}, indent=1))
        return 1 if errs else 0
    if a.clones is None:
        ap.error(f"{a.cmd} needs --clones")
    if a.cmd == "fetch":
        print(json.dumps(run_fetch(a.dir, a.clones, a.repo), indent=1))
    elif a.cmd == "score":
        print(json.dumps(run_score(a.dir, a.clones), indent=1, sort_keys=True))
    else:
        print(json.dumps(run_items(a.dir, a.clones, a.repo, a.cmd == "auto"), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
