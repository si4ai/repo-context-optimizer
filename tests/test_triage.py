"""rco triage and rco notice: hints only, clean vs structural brief."""
import json
import subprocess

from rco import cli, fixture, triage
from rco.output import Exit

BASE = {
    "pkg/__init__.py": '"""Pkg."""\n',
    "pkg/core.py": "def run(argv: list[str]) -> int:\n    return 0\n\n\ndef keep() -> int:\n    return 1\n",
    "pkg/use.py": "from pkg.core import run\n\n\ndef main() -> int:\n    return run([])\n",
    "pkg/other.py": "from pkg.core import keep\n\n\ndef two() -> int:\n    return keep()\n",
    "README.md": "# r\n",
}
NEW_RUN = ("def run(argv: list[str], *, verbose: bool = False) -> int:\n    return 0\n\n\n"
           "def keep() -> int:\n    return 1\n")


def _setup(tmp_path):
    """main moves run(); 'uses' edits a caller of run; 'clean' edits an unrelated file."""
    repo, base = fixture.make_repo(tmp_path / "r", BASE)
    fixture.git(repo, "switch", "-q", "-c", "uses")
    fixture.commit(repo, {"pkg/use.py": BASE["pkg/use.py"] + "\n\ndef again() -> int:\n    return run(['x'])\n"})
    fixture.git(repo, "switch", "-q", "-c", "clean", base)
    fixture.commit(repo, {"pkg/other.py": BASE["pkg/other.py"] + "\n\ndef three() -> int:\n    return 3\n"})
    fixture.git(repo, "switch", "-q", "-c", "same", base)
    fixture.commit(repo, {"pkg/core.py": BASE["pkg/core.py"] + "\n\ndef extra() -> int:\n    return 2\n"})
    fixture.git(repo, "switch", "-q", "main")
    fixture.commit(repo, {"pkg/core.py": NEW_RUN})
    return repo, base


def test_triage_clean_and_brief(tmp_path):
    repo, _ = _setup(tmp_path)
    rep = triage.triage(repo, ["clean", "uses", "same"], onto="main")
    by = {b.branch: b for b in rep.branches}
    assert by["clean"].clean and by["clean"].overlap == () and by["clean"].impacted == ()
    assert not by["uses"].clean
    assert [f for f, _ in by["uses"].impacted] == ["pkg/use.py"]
    assert by["uses"].impacted[0][1] == ("run",)
    assert by["same"].overlap == ("pkg/core.py",)
    assert [c.key for c in by["uses"].changes] == ["function:pkg/core.py:run"]
    assert rep.exit_code() is Exit.FINDINGS
    md = rep.to_markdown()
    assert "clean: no changed file, no changed contract used" in md
    assert "STRUCTURAL UPDATE REQUIRED" in md and "`pkg/use.py` (calls `run`)" in md


def test_triage_all_clean_is_exit_0(tmp_path):
    repo, _ = _setup(tmp_path)
    rep = triage.triage(repo, ["clean"], onto="main")
    assert rep.exit_code() is Exit.OK


def test_triage_never_moves_refs(tmp_path):
    repo, _ = _setup(tmp_path)
    before = fixture.git(repo, "for-each-ref", "--format=%(refname) %(objectname)")
    head = fixture.git(repo, "rev-parse", "HEAD")
    triage.triage(repo, ["clean", "uses", "same"], onto="main")
    triage.notice(repo, None, "main", ["clean", "uses", "same"])
    assert fixture.git(repo, "for-each-ref", "--format=%(refname) %(objectname)") == before
    assert fixture.git(repo, "rev-parse", "HEAD") == head


def test_notice_names_other_prs_using_a_changed_contract(tmp_path):
    repo, base = _setup(tmp_path)
    rep = triage.notice(repo, base, "main", ["clean", "uses", "same"])
    by = {b.branch: b for b in rep.branches}
    assert not by["uses"].clean and by["clean"].clean
    obj = rep.to_obj()
    assert obj["schema"] == "rco.notice/2"
    assert rep.exit_code() is Exit.OK  # a notice is a hint, never a failing check
    assert "uses" in rep.to_markdown()


def test_cli_triage_json_deterministic(tmp_path, capsysbinary):
    repo, _ = _setup(tmp_path)
    args = ["triage", "clean", "uses", "--onto", "main", "--repo", str(repo), "--format", "json"]
    assert cli.main(args) == 1
    first = capsysbinary.readouterr().out
    assert cli.main(args) == 1
    assert capsysbinary.readouterr().out == first
    obj = json.loads(first)
    assert obj["schema"] == "rco.triage/1"
    assert [b["clean"] for b in obj["branches"]] == [True, False]


def test_cli_triage_needs_a_branch(tmp_path):
    repo, _ = _setup(tmp_path)
    assert cli.main(["triage", "--repo", str(repo)]) == 2


POLY = {
    "web/package.json": '{"name": "web"}',
    "web/api.ts": "export function fetchUser(id: string) { return id; }\n",
    "web/page.ts": 'import { fetchUser } from "./api";\nexport function page() { return fetchUser("a"); }\n',
    "web/noise.ts": "export function fetchUser() { return 1; }\n",
    "go.mod": "module example.com/x\n",
    "store/s.go": "package store\n\nfunc Get() int { return 1 }\n",
    "svc/main.go": 'package main\n\nimport "example.com/x/store"\n\nfunc main() { store.Get() }\n',
    "ops/lib.sh": "deploy_app() {\n  echo hi\n}\n",
    "ops/run.sh": '. "$(dirname "$0")/lib.sh"\ndeploy_app\n',
    "ops/tools.ps1": "function Invoke-Deploy {\n  param($Name)\n}\n",
    "ops/setup.ps1": ". $PSScriptRoot/tools.ps1\ninvoke-deploy -Name x\n",
    "pkg/__init__.py": "",
    "pkg/m.py": "class Box:\n    size: int\n",
    "pkg/u.py": "from pkg.m import Box\n\n\ndef f(b: Box, cfg):\n    return b.size + cfg.size\n",
}


def test_triage_and_notice_see_callers_in_every_bundled_language(tmp_path):
    repo, base = fixture.make_repo(tmp_path / "p", POLY)
    fixture.git(repo, "switch", "-q", "-c", "users")
    fixture.commit(repo, {
        "web/page.ts": POLY["web/page.ts"] + "// touched\n",
        "web/noise.ts": POLY["web/noise.ts"] + "// touched, but defines its own fetchUser\n",
        "svc/main.go": POLY["svc/main.go"] + "// touched\n",
        "ops/run.sh": POLY["ops/run.sh"] + "# touched\n",
        "ops/setup.ps1": POLY["ops/setup.ps1"] + "# touched\n",
        "pkg/u.py": POLY["pkg/u.py"] + "# touched\n"})
    fixture.git(repo, "switch", "-q", "main")
    fixture.commit(repo, {
        "web/api.ts": "export function fetchUser(id: number) { return id; }\n",
        "store/s.go": "package store\n\nfunc Get(x int) int { return x }\n",
        "ops/lib.sh": "deploy_v2() {\n  echo hi\n}\n",  # deploy_app removed
        "ops/tools.ps1": "function Invoke-Deploy {\n  param($Name, $Force)\n}\n",
        "pkg/m.py": "class Box:\n    size: float\n"})
    rep = triage.triage(repo, ["users"], onto="main")
    got = dict(rep.branches[0].impacted)
    assert got == {"ops/run.sh": ("deploy_app",), "ops/setup.ps1": ("Invoke-Deploy",),
                   "pkg/u.py": ("size",),
                   "svc/main.go": ("Get",), "web/page.ts": ("fetchUser",)}
    note = triage.notice(repo, base, "main", ["users"])
    assert dict(note.branches[0].impacted) == got and note.exit_code() is Exit.OK

def test_notice_no_branch_names_notice_not_triage(tmp_path, capsys):
    repo, _ = _setup(tmp_path)
    assert cli.main(["notice", "--repo", str(repo)]) == 2
    err = capsys.readouterr().err
    assert "rco notice:" in err
    assert "the following arguments are required" in err
    assert "triage needs" not in err


def test_notice_lists_changes_once_with_neutral_wording(tmp_path):
    repo, base = _setup(tmp_path)
    rep = triage.notice(repo, base, "main", ["clean", "uses", "same"])
    md = rep.to_markdown()
    assert md.startswith("# rco notice")
    assert md.count("## Contract changes in this PR") == 1
    assert "STRUCTURAL UPDATE REQUIRED" not in md
    assert "contract changes (upstream)" not in md
    assert "impacted files in your branch" not in md
    assert "task: update the impacted files" not in md
    obj = rep.to_obj()
    assert obj["schema"] == "rco.notice/2" and obj["changes"]
    assert "changes" not in obj["branches"][0]
    assert rep.exit_code() is Exit.OK
    # triage still uses the structural brief
    tri = triage.triage(repo, ["uses"], onto="main")
    assert "STRUCTURAL UPDATE REQUIRED" in tri.to_markdown()

