"""Card 1340: `pack --for` outlines the files the --for files import, ranked, not the owning module."""
from rco import fixture
from rco.forest_model import estimate_tokens
from rco.pack import MIN_BUDGET, pack, pack_markdown


def _fns(prefix: str, n: int) -> str:
    return "\n".join(f"def {prefix}{i}(x: int) -> int:\n    return x + {i}\n" for i in range(n))


def _repo(tmp_path):
    files = {
        "pkg/__init__.py": "",
        "pkg/a/__init__.py": "",
        "pkg/a/f.py": ("from other.x import x0\nfrom far.deep.er.y import y0\n\n\n"
                       "def f_main() -> int:\n    return x0(1) + y0(2)\n"),
        "pkg/a/g.py": _fns("g", 6),  # same module as F, not imported by F
        "other/__init__.py": "",
        "other/x.py": _fns("x", 6),
        "far/__init__.py": "",
        "far/deep/__init__.py": "",
        "far/deep/er/__init__.py": "",
        "far/deep/er/y.py": _fns("y", 6),
    }
    return fixture.make_repo(tmp_path / "scope", files)


def _paths(p):
    return {n.path for t in p.trees for n in t.nodes}


def test_for_outlines_imported_files_only(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha, ["pkg/a/f.py"], 10 ** 6, fit_to_imports=False)
    assert _paths(p) == {"other/x.py", "far/deep/er/y.py"}
    names = {c.name for t in p.trees for n in t.nodes for c in n.children}
    assert {"x0", "x5", "y0", "y5"} <= names
    assert "pkg/a/g.py" not in _paths(p)  # no other file of F's module
    assert "pkg/a/f.py" not in _paths(p)  # no Tree for F itself
    text = pack_markdown(p, "pkg/a/f.py")
    assert "def g0" not in text and "def f_main" not in text


def test_farther_file_cut_first(tmp_path):
    repo, sha = _repo(tmp_path)
    full = pack(repo, sha, ["pkg/a/f.py"], 10 ** 6, fit_to_imports=False)
    n_x = sum(1 for t in full.trees for n in t.nodes if n.path == "other/x.py")
    y_cut_x_whole = 0
    for budget in range(MIN_BUDGET, full.used + 1, 2):
        p = pack(repo, sha, ["pkg/a/f.py"], budget, fit_to_imports=False)
        assert estimate_tokens(pack_markdown(p, "pkg/a/f.py")) <= budget
        cut = [t for t in p.trimmed if t.startswith("node:")]
        x_cut = any(":other/x.py:" in t for t in cut)
        y_kept = any(n.path == "far/deep/er/y.py" for t in p.trees for n in t.nodes)
        assert not (x_cut and y_kept), (budget, p.trimmed)  # the nearer file outlives the farther one
        x_kept = sum(1 for t in p.trees for n in t.nodes if n.path == "other/x.py")
        if any(":far/deep/er/y.py:" in t for t in cut) and x_kept == n_x:
            y_cut_x_whole += 1
    assert y_cut_x_whole, "some budget must cut the farther file and keep the nearer one whole"


def test_whole_repo_pack_unchanged_without_for(tmp_path):
    repo, sha = _repo(tmp_path)
    p = pack(repo, sha)
    assert p.trees == () and p.hop == ()
