"""Card 1344: replay counts each changed file once, sends nothing for a skipped
pack, shows no replay-only warning line, explains each commit, and has --since."""
import json

from rco import cli, fixture, metrics
from rco.output import Exit
from rco.replay import replay

BIG = "".join(f"def f{i}(x: int) -> int:\n    y = x * {i} + 1\n    z = y - x // 3\n"
              f"    total = sum(range(z % 7))\n    extra = [k * total for k in range(y % 5)]\n"
              f"    if extra and total > 3:\n        total += max(extra) - min(extra)\n"
              f"    return total + y + z + {i}\n\n" for i in range(12))
USER_A = "from lib.big import f0\n\n\ndef use() -> int:\n    return f0(1)\n"
USER_B = "from lib.big import f0, f1\n\n\ndef use() -> int:\n    return f0(1) + f1(2)\n"
SOLO_A = "import json\n\n\ndef solo() -> str:\n    return json.dumps(1)\n"
SOLO_B = "import json\n\n\ndef solo() -> str:\n    return json.dumps(2)\n"
MARCH = "1772323200 +0000"  # 2026-03-01
APRIL = "1775001600 +0000"  # 2026-04-01


def _history(tmp_path):
    """root (january), user.py edit (march), solo.py edit (april), then user.py again at HEAD."""
    repo, root = fixture.make_repo(tmp_path / "r", {
        "app/__init__.py": "", "app/user.py": USER_A, "app/solo.py": SOLO_A,
        "lib/__init__.py": "", "lib/big.py": BIG})
    u = fixture.commit(repo, {"app/user.py": USER_B}, "user", date=MARCH)
    s = fixture.commit(repo, {"app/solo.py": SOLO_B}, "solo", date=APRIL)
    h = fixture.commit(repo, {"app/user.py": USER_A}, "user back", date=APRIL)
    return repo, root, u, s, h


def _rows(repo, **kw):
    return {r["sha"]: r for r in replay(repo, kw.pop("last", 10), 12000, 50,
                                        metrics.estimate, **kw).to_obj()["commits"]}


def test_with_side_counts_each_changed_file_once(tmp_path):
    repo, root, u, s, h = _history(tmp_path)
    r = _rows(repo)[u]
    est = metrics.estimate
    assert r["with"]["changed_tokens"] == est(USER_B)
    assert r["without"]["imported_files"] == 1 and r["without"]["imported_tokens"] == est(BIG)
    assert r["without"]["tokens"] == est(USER_B) + est(BIG)
    # the pack outlines the imported file, never the changed one, so the
    # changed file is counted once: pack plus its own text
    assert r["coverage"] == {"changed_in_pack": 0, "imported_in_pack": 1, "imported": 1}
    assert r["with"]["tokens"] == r["with"]["pack_tokens"] + est(USER_B)
    assert r["with"]["pack_tokens"] > 0 and r["with"]["files"] == 2


def test_skipped_pack_sends_nothing(tmp_path):
    repo, root, u, s, h = _history(tmp_path)
    r = _rows(repo)[s]  # solo.py imports nothing in the repo
    assert r["with"]["pack_tokens"] == 0 and r["with"]["pack_skipped"] is True
    assert r["with"]["tokens"] == r["without"]["tokens"] == metrics.estimate(SOLO_B)
    assert r["with"]["files"] == r["without"]["files"] == 1
    assert r["coverage"]["imported"] == 0


def test_no_replay_only_freshness_warning(tmp_path):
    # u's user.py differs in the working tree (HEAD changed it back), which
    # used to add "N files differ" and a "# warning: changed since" line
    from rco.replay import pack_text_at
    repo, root, u, s, h = _history(tmp_path)
    text = pack_text_at(repo, u, ["app/user.py"], 12000)
    assert "# warning:" not in text
    assert f"# built from {u[:7]} (git); HEAD {u[:7]}; current; working tree matches" in text
    assert _rows(repo)[u]["with"]["pack_tokens"] == metrics.estimate(text)


def test_since_selects_commits_from_midnight(tmp_path):
    repo, root, u, s, h = _history(tmp_path)
    assert set(_rows(repo, last=None, since="2026-03-15")) == {s, h}
    assert set(_rows(repo, last=None, since="2026-02-01")) == {u, s, h}
    assert set(_rows(repo, last=1, since="2026-02-01")) == {h}


def test_cli_since_and_json_fields(tmp_path, capsys):
    repo, root, u, s, h = _history(tmp_path)
    assert cli.main(["replay", "--repo", str(repo), "--since", "2026-02-01",
                     "--format", "json"]) == Exit.OK
    o = json.loads(capsys.readouterr().out)
    assert [c["sha"] for c in o["commits"]] == [h, s, u]
    for c in o["commits"]:
        assert {"changed_tokens", "pack_tokens", "pack_skipped"} <= set(c["with"])
        assert {"imported_files", "imported_tokens"} <= set(c["without"])
        assert set(c["coverage"]) == {"changed_in_pack", "imported_in_pack", "imported"}


def test_memo_gives_the_same_rows(tmp_path):
    # the per-run cache of per-file work must not change any number
    from rco import memo
    repo, root, u, s, h = _history(tmp_path)
    with memo.scope():
        cached = replay(repo, 10, 12000, 50, metrics.estimate).to_obj()
    assert memo.active() is False
    assert replay(repo, 10, 12000, 50, metrics.estimate, use_memo=False).to_obj() == cached


def test_since_bare_date_means_midnight_not_now(tmp_path, monkeypatch):
    # git reads --since=2026-03-15 at the current time of day; replay pins it
    # to midnight, so a commit 30 seconds after midnight is in
    monkeypatch.setenv("TZ", "UTC")
    repo, root, u, s, h = _history(tmp_path)
    early = fixture.commit(repo, {"app/user.py": USER_B}, "early", date="1773532830 +0000")
    assert early in _rows(repo, last=None, since="2026-03-15")
