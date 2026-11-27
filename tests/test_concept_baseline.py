"""tools/concept_baseline.py: the committed labelled set and the scorer's pure parts."""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import concept_baseline as cb  # noqa: E402

DATA = Path(__file__).resolve().parent.parent / "docs" / "baseline" / "concepts"


def test_committed_labelled_set_passes_check():
    tax, corpus, labels = cb.load(DATA)
    assert cb.check(tax, corpus, labels) == []


def test_corpus_size_and_split_by_repo():
    _, corpus, labels = cb.load(DATA)
    repos = corpus["repos"]
    assert 20 <= len(repos) <= 30
    flat = [r for r in repos if r["layout"] == "flat"]
    assert 3 <= len(flat) <= 5
    assert all(r["split"] == "heldout" for r in flat)
    # one split per repo: a path never appears under two repos' splits
    train = {r["id"] for r in repos if r["split"] == "train"}
    held = {r["id"] for r in repos if r["split"] == "heldout"}
    assert train and held and not train & held
    for rid, lab in labels.items():
        assert lab["taxonomy_version"] == corpus["taxonomy_version"]
        assert lab["items"], rid


def test_check_catches_bad_labels():
    tax, corpus, labels = cb.load(DATA)
    rid = next(iter(labels))
    bad = json.loads(json.dumps(labels))
    bad[rid]["items"][0]["concepts"].append({"id": "no.such", "prov": "auto"})
    bad[rid]["items"][1]["concepts"][0]["prov"] = "guess"
    errs = cb.check(tax, corpus, bad)
    assert any("unknown concept no.such" in e for e in errs)
    assert any("provenance 'guess'" in e for e in errs)


def test_tokens_split_camel_snake_and_tests():
    assert cb.tokens("base_coder.py") == ["base_coder", "base", "coder"]
    assert cb.tokens("CancelToken.js") == ["canceltoken", "cancel", "token"]
    assert "test" in cb.tokens("test_cli.py")
    assert "test" in cb.tokens("adapter.browser.test.js")


def test_auto_concepts_exact_own_name_only():
    tax, _, _ = cb.load(DATA)
    nidx = cb.name_index(tax)
    assert "layer.cli" in cb.auto_concepts("src/pkg/cli.py", "file", nidx)
    assert "layer.persistence" in cb.auto_concepts("pkg/storage", "module", nidx)
    # the parent directory does not make an auto label
    assert cb.auto_concepts("pkg/cli/zz_unmatched.py", "file", nidx) == []


def test_prf_and_evaluate_micro_macro():
    assert cb.prf(1, 1, 0)["precision"] == 0.5
    assert cb.prf(0, 0, 0)["f1"] == 0.0
    gold = {("r", "file", "a"): {"x"}, ("r", "file", "b"): {"y"}}
    scores = {("r", "file", "a"): {"x": 1.0, "y": 0.9}, ("r", "file", "b"): {"y": 0.1}}
    res = cb.evaluate(gold, scores, 0.5, ["x", "y"])
    assert res["micro"]["tp"] == 1 and res["micro"]["fp"] == 1 and res["micro"]["fn"] == 1
    assert res["per_concept"]["x"]["f1"] == 1.0
    assert res["per_concept"]["y"]["f1"] == 0.0
    assert res["macro"]["f1"] == 0.5 and res["macro_over"] == 2


def test_clusters_deterministic_and_prune_facades():
    edges = {
        "p/__init__.py": {"p/a.py", "p/b.py", "q/c.py", "q/d.py"},
        "p/a.py": {"p/__init__.py", "p/b.py"},
        "p/b.py": {"p/__init__.py", "p/a.py"},
        "q/c.py": {"p/__init__.py", "q/d.py"},
        "q/d.py": {"p/__init__.py", "q/c.py"},
        "z.py": set(),
    }
    pruned = cb.prune(edges)
    assert pruned["p/__init__.py"] == set()
    assert "p/__init__.py" not in pruned["p/a.py"]
    lab = cb.clusters(pruned)
    assert lab == cb.clusters(pruned)
    assert lab["p/a.py"] == lab["p/b.py"] != lab["q/c.py"] == lab["q/d.py"]
    assert lab["z.py"] == "z.py"
    st = cb.forest_stats(sorted(edges), pruned)
    assert st["clusters"] == 2 and st["folder_purity"] == 1.0


def test_resolve_python_imports_absolute_and_relative():
    files = ["src/pkg/__init__.py", "src/pkg/core.py", "src/pkg/sub/util.py"]
    mmap = cb.py_module_map(files)
    assert cb.resolve_py("pkg.core", 0, "src/pkg/sub/util.py", mmap) == "src/pkg/core.py"
    assert cb.resolve_py("core", 2, "src/pkg/sub/util.py", mmap) == "src/pkg/core.py"
    assert cb.resolve_py("pkg.missing", 0, "x.py", mmap) == "src/pkg/__init__.py"
    assert cb.resolve_py("os", 0, "x.py", mmap) is None


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


def test_score_end_to_end_on_tiny_repos(tmp_path):
    """Two throwaway git repos, one train and one held out: score runs,
    picks a threshold on train, and gives the same bytes twice."""
    tax = json.loads((DATA / "taxonomy.json").read_text(encoding="utf-8"))
    clones = tmp_path / "clones"
    repos = []
    for rid, split in (("one", "train"), ("two", "heldout")):
        d = clones / rid
        (d / "pkg").mkdir(parents=True)
        (d / "pkg" / "cli.py").write_text("import argparse\nfrom pkg import db\n" + "#" * 300 + "\n")
        (d / "pkg" / "db.py").write_text("import sqlite3\n" + "#" * 300 + "\n")
        (d / "pkg" / "notes.py").write_text("from pkg import db\n" + "#" * 300 + "\n")
        _git(d, "init", "-q")
        _git(d, "add", "-A")
        _git(d, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "x")
        sha = subprocess.run(["git", "-C", str(d), "rev-parse", "HEAD"], check=True,
                             capture_output=True, text=True).stdout.strip()
        repos.append({"id": rid, "url": f"https://example.com/{rid}", "sha": sha, "licence": "MIT",
                      "language": "python", "layout": "structured", "split": split})
    ddir = tmp_path / "data"
    (ddir / "labels").mkdir(parents=True)
    (ddir / "taxonomy.json").write_text(json.dumps(tax))
    (ddir / "corpus.json").write_text(json.dumps({"version": "t", "taxonomy_version": tax["version"], "repos": repos}))
    for r in repos:
        items = [
            {"kind": "module", "path": "pkg", "concepts": [{"id": "layer.cli", "prov": "judged"}], "evidence": "e"},
            {"kind": "file", "path": "pkg/cli.py", "concepts": [{"id": "layer.cli", "prov": "auto"}], "evidence": "e"},
            {"kind": "file", "path": "pkg/db.py", "concepts": [{"id": "layer.persistence", "prov": "auto"}], "evidence": "e"},
        ]
        (ddir / "labels" / f"{r['id']}.json").write_text(json.dumps(
            {"repo": r["id"], "sha": r["sha"], "taxonomy_version": tax["version"], "items": items}))
    a = cb.run_score(ddir, clones)
    b = cb.run_score(ddir, clones)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert set(a["configs"]) == set(cb.CONFIGS)
    names = a["configs"]["names"]["heldout_structured"]
    assert names["items"] == 3
    assert names["per_concept"]["layer.persistence"]["tp"] == 1
    assert a["forest"]["two"]["raw"]["files"] == 3


def test_committed_run_matches_committed_labels():
    """The run file was produced from these labels and this taxonomy."""
    run = json.loads((DATA / "baseline-run.json").read_text(encoding="utf-8"))
    tax, corpus, labels = cb.load(DATA)
    assert run["taxonomy_version"] == tax["version"]
    assert run["corpus_version"] == corpus["version"]
    held = [r["id"] for r in corpus["repos"] if r["split"] == "heldout" and r["layout"] == "flat"]
    n = sum(len(labels[r]["items"]) for r in held)
    assert run["configs"]["names"]["heldout_flat"]["items"] == n
    assert set(run["configs"]) == set(cb.CONFIGS)
