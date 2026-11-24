"""rco metrics: one-PR measure, check, and a roll-up over many files."""
import json
from pathlib import Path

from rco import cli, fixture, metrics
from rco.output import Exit


def _doc(pr, ratio, without_bytes=1000, with_bytes=200, without_tok=None, with_tok=None,
         head="a" * 40, extra=None):
    if with_tok is None and without_tok is None and ratio:
        without_tok = 10000
        with_tok = int(round(ratio * without_tok))
    elif with_tok is None:
        with_tok = 0
        without_tok = 0
    doc = {
        "schema": 1,
        "pr": pr,
        "base_sha": "b" * 40,
        "head_sha": head,
        "rco_version": "0.1.6",
        "tokenizer": "estimate chars/4",
        "changed_files": 2,
        "without_rco": {"files": 3, "bytes": without_bytes, "tokens": without_tok},
        "with_rco": {"files": 2, "bytes": with_bytes, "tokens": with_tok},
        "ratio_tokens": ratio,
    }
    if extra:
        doc.update(extra)
    return doc


def _write(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def test_measure_counts_changed_files_plus_one_import_hop():
    # a.py is unchanged but imported by the changed file; without-rco includes it.
    changed = ["pkg/b.py"]
    graph = {"pkg/b.py": ("pkg/a.py",), "pkg/a.py": ()}
    blobs = {"pkg/a.py": b"A" * 40, "pkg/b.py": b"B" * 20, "pkg/c.py": b"C" * 8}
    pack = "pack-text"
    got = metrics.measure(changed, graph, blobs, pack, metrics.estimate)
    assert got["changed_files"] == 1
    assert got["without_rco"]["files"] == 2
    assert got["without_rco"]["bytes"] == 60
    assert got["without_rco"]["tokens"] == metrics.estimate("A" * 40 + "B" * 20)
    assert got["with_rco"]["files"] == 1
    assert got["with_rco"]["bytes"] == len(pack.encode("utf-8"))
    assert got["with_rco"]["tokens"] == metrics.estimate(pack)
    assert got["ratio_tokens"] == round(
        got["with_rco"]["tokens"] / got["without_rco"]["tokens"], 4)


def test_measure_skips_deleted_paths_and_null_ratio_when_without_is_zero():
    got = metrics.measure(["gone.py"], {}, {}, "", metrics.estimate)
    assert got == {"changed_files": 0,
                   "without_rco": {"files": 0, "bytes": 0, "tokens": 0},
                   "with_rco": {"files": 0, "bytes": 0, "tokens": 0},
                   "ratio_tokens": None}


def test_summary_mean_median_totals_and_best_worst(tmp_path, capsys):
    # ratios 0.10, 0.20, 0.40 -> mean 0.2333, median 0.20
    files = [
        _write(tmp_path / "a" / "rco-metrics.json",
               _doc(498, 0.10, without_bytes=1000, with_bytes=100, head="h1" + "0" * 38)),
        _write(tmp_path / "b" / "rco-metrics.json",
               _doc(500, 0.20, without_bytes=500, with_bytes=200, head="h2" + "0" * 38)),
        _write(tmp_path / "c" / "rco-metrics.json",
               _doc(501, 0.40, without_bytes=400, with_bytes=300, head="h3" + "0" * 38)),
    ]
    assert cli.main(["metrics", "summary", str(tmp_path)]) == Exit.OK
    out = capsys.readouterr().out
    assert "- count: 3\n" in out
    assert "- mean ratio: 0.2333\n" in out
    assert "- median ratio: 0.2000\n" in out
    assert "- bytes saved: 1300\n" in out  # 900+300+100
    # tokens: each doc uses without=10000, with = ratio * 10000
    assert "- tokens saved: 23000\n" in out  # 9000+8000+6000
    assert "- best: PR 498 (ratio 0.1000)\n" in out
    assert "- worst: PR 501 (ratio 0.4000)\n" in out

    assert cli.main(["metrics", "summary", str(tmp_path), "--json"]) == Exit.OK
    js = json.loads(capsys.readouterr().out)
    assert cli.main(["metrics", "summary", str(tmp_path), "--format", "json"]) == Exit.OK
    js2 = json.loads(capsys.readouterr().out)
    assert js == js2
    assert js["schema"] == "rco.metrics.summary/1"
    assert js["count"] == 3
    assert js["mean_ratio"] == 0.2333
    assert js["median_ratio"] == 0.2 or js["median_ratio"] == 0.2000
    assert js["bytes_saved"] == 1300
    assert js["tokens_saved"] == 23000
    assert js["best"]["pr"] == 498
    assert js["worst"]["pr"] == 501
    assert js["skipped"] == 0

    # explicit files work the same as a directory
    assert cli.main(["metrics", "summary", *[str(f) for f in files], "--json"]) == Exit.OK
    assert json.loads(capsys.readouterr().out)["count"] == 3


def test_summary_even_count_median_is_the_mid_average(tmp_path, capsys):
    _write(tmp_path / "a" / "rco-metrics.json", _doc(1, 0.10, head="1" * 40))
    _write(tmp_path / "b" / "rco-metrics.json", _doc(2, 0.20, head="2" * 40))
    assert cli.main(["metrics", "summary", str(tmp_path), "--json"]) == Exit.OK
    js = json.loads(capsys.readouterr().out)
    assert js["median_ratio"] == 0.15
    assert js["mean_ratio"] == 0.15


def test_summary_skips_bad_dir_files_dedupes_and_excludes_null_ratios(tmp_path, capsys):
    _write(tmp_path / "ok" / "rco-metrics.json", _doc(10, 0.25, head="x" * 40))
    (tmp_path / "bad").mkdir()
    (tmp_path / "bad" / "rco-metrics.json").write_text("{}\n")
    # duplicate of the same pr+head
    _write(tmp_path / "dup" / "rco-metrics.json", _doc(10, 0.25, head="x" * 40))
    # null ratio still counts toward bytes saved, not mean
    _write(tmp_path / "empty" / "rco-metrics.json",
           _doc(11, None, without_bytes=50, with_bytes=10, without_tok=0, with_tok=0,
                head="y" * 40))
    assert cli.main(["metrics", "summary", str(tmp_path), "--json"]) == Exit.OK
    err = capsys.readouterr()
    js = json.loads(err.out)
    assert js["count"] == 2
    assert js["mean_ratio"] == 0.25
    assert js["median_ratio"] == 0.25
    assert js["bytes_saved"] == (1000 - 200) + (50 - 10)
    assert js["best"]["pr"] == 10
    assert js["worst"]["pr"] == 10
    assert js["skipped"] >= 2
    assert "unreadable metrics" in err.err


def test_summary_explicit_bad_file_is_usage(tmp_path, capsys):
    p = tmp_path / "rco-metrics.json"
    p.write_text("{}\n")
    assert cli.main(["metrics", "summary", str(p)]) == Exit.USAGE
    assert "unreadable metrics" in capsys.readouterr().err


def test_summary_empty_dir_is_usage(tmp_path, capsys):
    assert cli.main(["metrics", "summary", str(tmp_path)]) == Exit.USAGE
    assert "no usable rco-metrics.json" in capsys.readouterr().err


def test_check_and_schema_1_round_trip(tmp_path, capsys):
    p = _write(tmp_path / "rco-metrics.json", _doc(498, 0.1486))
    assert metrics.check(json.loads(p.read_text())) == []
    assert cli.main(["metrics", "check", str(p)]) == Exit.OK
    assert cli.main(["metrics", "check", str(tmp_path / "missing.json")]) == Exit.FINDINGS
    p.write_text('{"schema": 2}\n')
    assert cli.main(["metrics", "check", str(p)]) == Exit.FINDINGS
    err = capsys.readouterr().err
    assert "missing" in err or "schema" in err


def test_measure_cli_writes_schema_1(tmp_path, capsys):
    repo, base = fixture.make_repo(tmp_path / "r", {
        "pkg/__init__.py": "",
        "pkg/a.py": "X = 1\n" + ("# pad\n" * 20),
        "pkg/b.py": "from pkg.a import X\n\n\ndef run() -> int:\n    return X\n",
    })
    fixture.commit(repo, {
        "pkg/b.py": "from pkg.a import X\n\n\ndef run() -> int:\n    return X + 1\n",
    })
    head = fixture.git(repo, "rev-parse", "HEAD").strip()
    pack = tmp_path / "pack.md"
    assert cli.main(["pack", head, "--repo", str(repo), "--for", "pkg/b.py",
                     "--out", str(pack)]) == Exit.OK
    capsys.readouterr()
    out = tmp_path / "rco-metrics.json"
    assert cli.main([
        "metrics", "measure", "--repo", str(repo), "--base", base, "--head", head,
        "--pr", "498", "--pack", str(pack), "--out", str(out), "--format", "json",
    ]) == Exit.OK
    capsys.readouterr()
    doc = json.loads(out.read_text())
    assert metrics.check(doc) == []
    assert doc["schema"] == 1
    assert doc["pr"] == 498
    assert doc["base_sha"] == base
    assert doc["head_sha"] == head
    assert doc["changed_files"] >= 1
    # b.py imports a.py, so without-rco includes the hop
    assert doc["without_rco"]["files"] >= 2
    assert doc["with_rco"]["bytes"] == pack.read_bytes().__len__()
    assert doc["ratio_tokens"] is not None
