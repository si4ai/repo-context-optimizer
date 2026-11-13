"""Qualified callers (rco.refs): a field named `base` on one class never picks up
every other `.base` in the repo."""
from rco import build, diff, fixture

NOISY_BASE = {
    "pkg/__init__.py": "",
    "pkg/report.py": (
        "from dataclasses import dataclass\n\n\n"
        "@dataclass\nclass Report:\n"
        "    base: str\n"
        "    head: str\n\n"
        "    def title(self) -> str:\n"
        "        return self.base + self.head\n\n\n"
        "@dataclass\nclass Holder:\n"
        "    report: Report\n\n\n"
        "def make() -> Report:\n"
        "    return Report(base='a', head='b')\n"
    ),
    "pkg/use.py": (
        "from pkg import report as rep\n"
        "from pkg.report import Holder, Report, make\n\n\n"
        "def show(r: Report) -> str:\n"
        "    return r.base\n\n\n"
        "def built() -> str:\n"
        "    x = make()\n"
        "    return x.base\n\n\n"
        "def held(h: Holder) -> str:\n"
        "    y = h.report\n"
        "    return y.base + h.report.base\n\n\n"
        "def qualified() -> str:\n"
        "    return rep.Report(base='z', head='y').head\n"
    ),
    # noise: unrelated objects with a .base attribute
    "pkg/other.py": (
        "import os\n\n\n"
        "class Path:\n"
        "    base = '/'\n\n\n"
        "def noise(cfg, args, p: Path) -> str:\n"
        "    a = cfg.base\n"
        "    b = args.base\n"
        "    c = os.path.base\n"
        "    return a + b + c + p.base\n"
    ),
}


def _diff(tmp_path, files, change):
    repo, a = fixture.make_repo(tmp_path / "r", files)
    b = fixture.commit(repo, change)
    return {c.key: c.callers for c in diff.diff(repo, a, b).changes}


def test_field_callers_are_qualified_not_bare(tmp_path):
    changed = NOISY_BASE["pkg/report.py"].replace("    base: str\n", "    base: bytes\n")
    by = _diff(tmp_path, NOISY_BASE, {"pkg/report.py": changed})
    assert by["field:pkg/report.py:Report.base"] == (
        "pkg/report.py:10",  # self.base
        "pkg/report.py:19",  # Report(base=...)
        "pkg/use.py:6",      # r: Report
        "pkg/use.py:11",     # x = make() -> Report
        "pkg/use.py:16",     # y = h.report, h.report.base
        "pkg/use.py:20",     # rep.Report(base=...)
    )
    assert not any(loc.startswith("pkg/other.py") for loc in by["field:pkg/report.py:Report.base"])


def test_removed_function_still_finds_dangling_callers(tmp_path):
    files = {"pkg/__init__.py": "", "pkg/a.py": "def gone():\n    pass\n\n\ndef kept():\n    pass\n",
             "pkg/b.py": "from pkg.a import gone as g\nimport pkg.a as m\n\n\ndef f():\n"
                         "    g()\n    m.gone()\n    gone = 1\n    return gone\n"}
    by = _diff(tmp_path, files, {"pkg/a.py": "def kept():\n    pass\n"})
    assert by["function:pkg/a.py:gone"] == ("pkg/b.py:6", "pkg/b.py:7")


POLY = {
    "web/package.json": '{"name": "web"}',
    "web/store.ts": ("export class Store {\n  limit = 1;\n  get(key: string) { return key; }\n}\n"
                     "export function open(): Store { return new Store(); }\n"),
    "web/app.ts": ('import { Store, open } from "./store";\n'
                   "// open() in a comment is not a call\n"
                   "const s = open();\nconsole.log(s.get('k'), \"open()\");\n"),
    "web/unrelated.ts": "export function open() { return 1; }\nconst x = { get: 1 }; x.get;\n",
    "go.mod": "module example.com/x\n",
    "store/s.go": "package store\n\nfunc Get() int { return 1 }\n",
    "store/t.go": "package store\n\nfunc Twice() int { return Get() * 2 }\n",
    "svc/main.go": 'package main\n\nimport "example.com/x/store"\n\nfunc main() { store.Get() }\n',
    "other/main.go": "package other\n\nfunc Get() int { return 2 }\nfunc f() int { return Get() }\n",
    "ops/lib.sh": "deploy_app() {\n  echo hi\n}\n",
    "ops/run.sh": '. "$(dirname "$0")/lib.sh"\ndeploy_app\n# deploy_app\n',
    "ops/solo.sh": "deploy_app\n",
}


def test_non_python_callers_follow_imports(tmp_path):
    change = {"web/store.ts": POLY["web/store.ts"].replace("get(key: string)", "get(key: number)")
              .replace("open(): Store", "open(x: number): Store"),
              "store/s.go": "package store\n\nfunc Get(x int) int { return x }\n",
              "ops/lib.sh": "deploy_app() {\n  echo bye\n}\nnew_fn() {\n  :\n}\n"}
    by = _diff(tmp_path, POLY, change)
    assert by["function:web/store.ts:open"] == ("web/app.ts:1", "web/app.ts:3")
    assert by["method:web/store.ts:Store.get"] == ("web/app.ts:4",)
    assert by["function:store/s.go:Get"] == ("store/t.go:3", "svc/main.go:5")
    sh = [k for k in by if k.startswith("function:ops/lib.sh")]
    assert sh == ["function:ops/lib.sh:new_fn"]  # body-only change: same contract


def test_diff_callers_are_deterministic(tmp_path):
    repo, a = fixture.make_repo(tmp_path / "r", NOISY_BASE)
    b = fixture.commit(repo, {"pkg/report.py": NOISY_BASE["pkg/report.py"] + "\nX = 1\n"})
    one = diff.diff(repo, a, b).to_obj()
    assert one == diff.diff(repo, a, b).to_obj()

def test_union_none_annotation_resolves_the_class(tmp_path):
    """A parameter typed `Cls | None` still types that name as Cls (M9)."""
    files = {
        "pkg/__init__.py": "",
        "pkg/box.py": "class Box:\n    size: int\n",
        "pkg/use.py": (
            "from pkg.box import Box\n\n\n"
            "def via_union(v: Box | None):\n"
            "    return v.size\n"
        ),
    }
    by = _diff(tmp_path, files, {"pkg/box.py": "class Box:\n    size: float\n"})
    assert "pkg/use.py:5" in by["field:pkg/box.py:Box.size"]


def test_constructor_assignment_types_the_variable(tmp_path):
    """`x = Cls(...)` types x as Cls, so `x.attr` is a caller (M10)."""
    files = {
        "pkg/__init__.py": "",
        "pkg/box.py": "class Box:\n    size: int\n",
        "pkg/use.py": (
            "from pkg.box import Box\n\n\n"
            "def via_ctor():\n"
            "    x = Box()\n"
            "    return x.size\n"
        ),
    }
    by = _diff(tmp_path, files, {"pkg/box.py": "class Box:\n    size: float\n"})
    assert "pkg/use.py:6" in by["field:pkg/box.py:Box.size"]

