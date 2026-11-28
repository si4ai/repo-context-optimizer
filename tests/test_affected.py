"""rco affected (test selection from a diff) and the staged / working-tree snapshots."""
import json

from rco import cli, fixture, gitread
from rco.affected import affected, is_doc, is_infra, is_test, readers_by_path, select, selection
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


def test_select_unnamed_data_means_run_all_unless_ignored_and_conftest_scopes():
    paths = ["data.csv", "tests/test_a.py", "tests/unit/test_b.py", "tests/unit/conftest.py"]
    _, tests, run_all, reasons = select(["data.csv"], paths, [{}])
    assert run_all and tests == ()
    assert reasons == ("data.csv: not code and no code file names it, so its readers are unknown",)
    _, _, run_all, _ = select(["data.csv"], paths, [{}], ignore=["*.csv"])
    assert not run_all
    _, tests, run_all, _ = select(["tests/unit/conftest.py"], paths, [{}])
    assert tests == ("tests/unit/test_b.py",) and not run_all


def test_is_infra_and_is_doc():
    for p in ("pyproject.toml", "setup.cfg", "setup.py", "tox.ini", "pytest.ini", "conftest.py",
              "requirements.txt", "requirements-dev.in", "constraints.txt", "web/package.json",
              "go.mod", "Cargo.lock", "Makefile", "rco.toml", ".github/workflows/ci.yml",
              "web/jest.config.js"):
        assert is_infra(p), p
    for p in ("tests/conftest.py", "tests/fixtures/app/pyproject.toml", "src/setup_utils.py",
              "requirements.md", "docs/index.md"):
        assert not is_infra(p), p
    for p in ("README.md", "docs/guide/a.rst", "CHANGELOG.md", "LICENSE", "NOTICE.txt",
              "docs/img/flow.png", "pkg/notes.adoc"):
        assert is_doc(p), p
    for p in ("tests/fixtures/README.md", "tests/golden/a.md", "data.json", "docs/conf.py",
              "requirements.txt"):
        assert not is_doc(p), p


def test_docs_only_select_nothing():
    paths = ["README.md", "docs/a.md", "src/a.py", "tests/test_a.py"]
    s = selection(["README.md", "docs/a.md"], paths, [{"tests/test_a.py": ("src/a.py",)}])
    assert s.tests == () and not s.run_all and s.reasons == ()
    assert s.docs == ("README.md", "docs/a.md")


def test_infra_is_run_all_even_when_it_is_code():
    paths = ["setup.py", "pyproject.toml", "conftest.py", "tests/test_a.py", "src/a.py"]
    for p in ("setup.py", "pyproject.toml", "conftest.py", ".github/workflows/ci.yml"):
        s = selection([p], paths, [{}])
        assert s.run_all and s.reasons == (f"{p}: test config or infra, so every test may see it",)
    s = selection(["src/a.py", "pyproject.toml"], paths, [{"tests/test_a.py": ("src/a.py",)}])
    assert s.run_all and s.tests == ("tests/test_a.py",)


def test_files_run_or_read_by_path_map_to_the_tests_that_name_them():
    paths = ["ci/report.sh", "data/cases.json", "docs/usage.md", "README.md",
             "tests/helper.py", "tests/test_ci.py", "tests/test_cases.py", "tests/test_docs.py",
             "tests/test_other.py"]
    sources = {
        "tests/test_ci.py": b'subprocess.run(["bash", ROOT / "ci" / "report.sh"])\n',
        "tests/helper.py": b'CASES = json.loads((ROOT / "data/cases.json").read_text())\n',
        "tests/test_cases.py": b"from helper import CASES\n",
        "tests/test_docs.py": b'assert "rco" in (ROOT / "docs" / "usage.md").read_text()\n',
        "tests/test_other.py": b"# data.sh, report.sh.bak and old-report.sh are other files\n",
    }
    graph = {"tests/test_cases.py": ("tests/helper.py",)}
    s = selection(["ci/report.sh"], paths, [graph], sources=[sources])
    assert s.tests == ("tests/test_ci.py",) and not s.run_all
    assert s.by_path == (("ci/report.sh", ("tests/test_ci.py",)),)
    s = selection(["data/cases.json"], paths, [graph], sources=[sources])
    assert s.tests == ("tests/test_cases.py",) and not s.run_all  # through the helper
    s = selection(["docs/usage.md", "README.md"], paths, [graph], sources=[sources])
    assert s.tests == ("tests/test_docs.py",) and s.docs == ("README.md",) and not s.run_all
    assert readers_by_path(["x/a.sh"], [{"t.py": b"data.sh a.sh.bak my-a.sh a.sh."}]) == {
        "x/a.sh": ("t.py",)}
    assert readers_by_path(["pkg/__init__.py"], [{"t.py": b"__init__.py"}]) == {}


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
    s4 = fixture.commit(repo, {"data.csv": "y\n"})
    r = affected(repo, s3, s4)
    assert r.run_all and r.tests == () and r.select == "all"
    assert r.all_tests == ("tests/test_cli.py", "tests/test_other.py", "web/a.test.ts")
    s5 = fixture.commit(repo, {"README.md": "y\n"})
    r = affected(repo, s4, s5)
    assert not r.run_all and r.tests == () and r.select == "none" and r.docs == ("README.md",)


def test_affected_maps_a_script_a_test_runs_by_path(tmp_path, capsys):
    """The planted-bug miss: a shell script a Python test runs by path, not by import."""
    files = {**REPO, "ci/notice.sh": "echo hi\n",
             "tests/test_notice.py": 'import subprocess\nsubprocess.run(["sh", "ci/notice.sh"])\n'}
    repo, s1 = fixture.make_repo(tmp_path / "r", files)
    s2 = fixture.commit(repo, {"ci/notice.sh": "echo bye\n"})
    r = affected(repo, s1, s2)
    assert r.tests == ("tests/test_notice.py",) and r.select == "tests"
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--format", "json"]) == Exit.OK
    obj = json.loads(capsys.readouterr().out)
    assert obj["select"] == "tests" and obj["by_path"] == {"ci/notice.sh": ["tests/test_notice.py"]}
    assert cli.main(["affected", "--repo", str(repo), s1, s2]) == Exit.OK
    assert "## Read by path (1)" in capsys.readouterr().out


def test_affected_cli_list_and_json(tmp_path, capsys):
    repo, s1 = fixture.make_repo(tmp_path / "r", REPO)
    s2 = fixture.commit(repo, {"data.csv": "y\n"})
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--list"]) == Exit.OK
    assert capsys.readouterr().out.split() == ["tests/test_cli.py", "tests/test_other.py",
                                               "web/a.test.ts"]
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--ignore", "*.csv", "--list",
                     ]) == Exit.OK
    assert capsys.readouterr().out == "\n"
    assert cli.main(["affected", "--repo", str(repo), s1, s2, "--format", "json"]) == Exit.OK
    obj = json.loads(capsys.readouterr().out)
    assert obj["schema"] == "rco.affected/1" and obj["run_all"] is True and obj["select"] == "all"
    s3 = fixture.commit(repo, {"README.md": "z\n"})
    assert cli.main(["affected", "--repo", str(repo), s2, s3, "--format", "json"]) == Exit.OK
    obj = json.loads(capsys.readouterr().out)
    assert obj["select"] == "none" and obj["tests"] == [] and obj["docs"] == ["README.md"]
    assert cli.main(["affected", "--repo", str(repo), s2, s3]) == Exit.OK
    assert "**No tests needed.**" in capsys.readouterr().out


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


def test_every_language_has_build_files_that_select_every_test():
    """A new language must name its build files here, or say it has none."""
    from rco.affected import BUILD_FILES, NO_BUILD_FILES
    from rco import edges
    from rco.extract.langs import LANG_OF

    langs = set(LANG_OF.values()) | {"python"}
    assert langs <= set(BUILD_FILES) | NO_BUILD_FILES, langs - set(BUILD_FILES) - NO_BUILD_FILES
    assert not NO_BUILD_FILES & set(BUILD_FILES)
    for lang, names in BUILD_FILES.items():
        assert names, lang
        for n in names:
            sample = n.replace("*", "x")
            for path in (sample, f"app/{sample}", f"a/b/c/{sample}"):
                assert is_infra(path), (lang, path)
            assert not is_infra(f"tests/fixtures/{sample}"), (lang, sample)  # a fixture is test data
            _, tests, run_all, _ = select([f"app/{sample}"], ["app/Fix.test.js"], [{}],
                                         sources=[{"src/Reader.kt": f'"{sample}"'.encode()}])
            assert run_all, (lang, sample)  # a file that only names it does not read it
    suffixes = getattr(edges, "MANIFEST_SUFFIXES", ())
    for n in edges.MANIFEST_NAMES + tuple("x" + s for s in suffixes):  # the manifests edges read
        assert is_infra(n) and is_infra(f"a/{n}"), n


def test_a_build_file_a_source_file_only_mentions_still_runs_everything():
    srcs = [{"build-logic/src/main/kotlin/Bnd.kt": b'val f = "build.gradle.kts"'}]
    for p in ("okhttp/build.gradle.kts", "Lib/Lib.csproj", "Directory.Build.props", "pom.xml",
              "gradle/libs.versions.toml"):
        sel = selection([p], ["a/T.java"], [{}], sources=srcs if p.endswith(".kts") else ())
        assert sel.run_all and not sel.tests, p
    assert not is_infra("notes/build-gradle.txt") and not is_infra("tests/data/pom.xml")
