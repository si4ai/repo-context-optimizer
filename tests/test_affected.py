"""rco affected (test selection from a diff) and the staged / working-tree snapshots."""
import json

from rco import cli, fixture, gitread
from rco.affected import affected, is_test, select
from rco.diff import diff
from rco.output import Exit


def test_is_test():
    assert is_test("tests/test_a.py") and is_test("pkg/a_test.py")
    assert not is_test("tests/conftest.py") and not is_test("tests/helpers.py")
    assert not is_test("tests/fixtures/app/cli.py")
    assert is_test("svc/store_test.go") and not is_test("svc/store.go")
    assert is_test("web/a.test.ts") and is_test("web/a.spec.js") and is_test("web/__tests__/a.js")
    assert not is_test("web/test.js")
    assert is_test("crates/core/tests/api.rs") and not is_test("crates/core/tests/data/x.rs")


def test_select_follows_imports_both_ways_in_time():
    head = {"tests/test_b.py": ("src/b.py",), "src/b.py": ("src/a.py",), "tests/test_c.py": ()}
    base = {"tests/test_old.py": ("src/gone.py",)}
    paths = ["src/a.py", "src/b.py", "tests/test_b.py", "tests/test_c.py", "tests/test_old.py"]
    files, tests, run_all, _ = select(["src/a.py"], paths, [base, head])
    assert files == ("src/a.py", "src/b.py", "tests/test_b.py")
    assert tests == ("tests/test_b.py",) and not run_all
    _, tests, _, _ = select(["src/gone.py"], paths, [base, head])
    assert tests == ("tests/test_old.py",)  # the importer of a deleted file, from BASE


def test_select_non_code_means_run_all_unless_ignored_and_conftest_scopes():
    paths = ["README.md", "tests/test_a.py", "tests/unit/test_b.py", "tests/unit/conftest.py"]
    _, tests, run_all, reasons = select(["README.md"], paths, [{}])
    assert run_all and tests == () and reasons == ("README.md: not code, so its readers are unknown",)
    _, _, run_all, _ = select(["README.md"], paths, [{}], ignore=["*.md"])
    assert not run_all
    _, tests, run_all, _ = select(["tests/unit/conftest.py"], paths, [{}])
    assert tests == ("tests/unit/test_b.py",) and not run_all


REPO = {
    "src/app/__init__.py": "",
    "src/app/core.py": "def f():\n    return 1\n",
    "src/app/cli.py": "from app.core import f\n",
    "tests/helper.py": "import numpy\nfrom app import cli\n",
    "tests/test_cli.py": "from helper import x\n",
    "tests/test_other.py": "import os\n",
    "web/a.ts": "export function a() {}\n",
    "web/a.test.ts": 'import { a } from "./a";\n',
    "README.md": "x\n",
}


def test_affected_on_a_repo_with_sibling_imports_and_js(tmp_path):
    repo, s1 = fixture.make_repo(tmp_path / "r", REPO)
    s2 = fixture.commit(repo, {"src/app/core.py": "def f():\n    return 2\n"})
    r = affected(repo, s1, s2)
    assert r.changed == ("src/app/core.py",)
    assert r.tests == ("tests/test_cli.py",) and not r.run_all
    s3 = fixture.commit(repo, {"web/a.ts": "export function a(x) {}\n"})
    assert affected(repo, s2, s3).tests == ("web/a.test.ts",)
    s4 = fixture.commit(repo, {"README.md": "y\n"})
    r = affected(repo, s3, s4)
    assert r.run_all and r.tests == ()
    assert r.all_tests == ("tests/test_cli.py", "tests/test_other.py", "web/a.test.ts")


def test_affected_cli_list_and_json(tmp_path, capsys):
    repo, s1 = fixture.make_repo(tmp_path / "r", REPO)
    s2 = fixture.commit(repo, {"README.md": "y\n"})
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--list"]) == Exit.OK
    assert capsys.readouterr().out.split() == ["tests/test_cli.py", "tests/test_other.py",
                                               "web/a.test.ts"]
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--ignore", "*.md", "--list",
                     ]) == Exit.OK
    assert capsys.readouterr().out == "\n"
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--format", "json"]) == Exit.OK
    obj = json.loads(capsys.readouterr().out)
    assert obj["schema"] == "rco.affected/1" and obj["run_all"] is True


def test_snapshots_leave_the_index_alone_and_are_stable(tmp_path, capsys):
    repo, s1 = fixture.make_repo(tmp_path / "r", REPO)
    (repo / "src/app/core.py").write_text("def f(x):\n    return 2\n")
    fixture.git(repo, "add", "src/app/core.py")
    (repo / "web/a.ts").write_text("export function a(y) {}\n")  # unstaged
    (repo / "web/b.ts").write_text("export function b() {}\n")  # untracked
    before = fixture.git(repo, "status", "--porcelain", "--untracked-files=all")
    staged = gitread.snapshot(repo, "staged")
    work = gitread.snapshot(repo, "worktree")
    assert staged == gitread.snapshot(repo, "staged") and work == gitread.snapshot(repo, "worktree")
    assert fixture.git(repo, "status", "--porcelain", "--untracked-files=all") == before
    assert fixture.git(repo, "rev-parse", "HEAD").strip() == s1
    assert affected(repo, None, None, "staged").changed == ("src/app/core.py",)
    assert affected(repo, None, None, "worktree").changed == (
        "src/app/core.py", "web/a.ts", "web/b.ts")
    d = diff(repo, None, None, "staged")
    assert d.base == s1 and [c.path for c in d.changes] == ["src/app/core.py"]
    assert cli.main(["diff", "--repo", str(repo), "--worktree", "--format", "json"]) == Exit.FINDINGS
    paths = {c["path"] for c in json.loads(capsys.readouterr().out)["changes"]}
    assert paths == {"src/app/core.py", "web/a.ts", "web/b.ts"}
    assert cli.main(["diff", "--repo", str(repo), "--staged", "HEAD", "HEAD"]) == Exit.USAGE
