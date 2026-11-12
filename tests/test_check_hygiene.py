"""check_hygiene reports identifiers, secrets, and junk in a git range.

The command is a fact report: it exits 0 even when it finds something.
--strict fails on a doctored range and passes on a clean range. A git
failure exits 2.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import check_hygiene as ch  # noqa: E402

_GIT_ENV = {
    "GIT_AUTHOR_NAME": "builder",
    "GIT_AUTHOR_EMAIL": "builder@users.noreply.github.com",
    "GIT_COMMITTER_NAME": "builder",
    "GIT_COMMITTER_EMAIL": "builder@users.noreply.github.com",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_CONFIG_SYSTEM": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}

# assembled so this file itself does not contain a forbidden literal.
_PLANTED_NAME = "Q" + "vexlorn"
_PLANTED_MAIL_HOST = "mail" + "host"
_PLANTED_HOME_USER = "some" + "one"
_PLANTED_IP = ".".join(("10", "0", "0", "1"))
_PLANTED_EMAIL = "desk@" + _PLANTED_MAIL_HOST + ".com"
_PLANTED_HOME = "/home/" + _PLANTED_HOME_USER + "/notes"
_PLANTED_PAT = "ghp_" + ("C" * 36)


def _git(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(_GIT_ENV)
    proc = subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, env=env, text=True
    )
    return proc.stdout


def _write(repo: Path, rel: str, text: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "commit", "--allow-empty", "-qm", message)
    return _git(repo, "rev-parse", "HEAD").strip()


def _base_repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, "tools/hygiene_words.txt", "hello\nnotes\nexample\nrole\nadd\nbase\nsee\nuser\nproj\ndesk\n")
    _write(repo, "docs/guide.md", "hello example user\n")
    return repo, _commit(repo, "base\n\nRole: builder\n")


def _scan(
    repo: Path,
    base: str,
    head: str = "HEAD",
    strict: bool = False,
    extra: list[str] | None = None,
) -> tuple[int, list[dict]]:
    argv = ["--root", str(repo), "--base", base, "--head", head, "--json"]
    if strict:
        argv.append("--strict")
    if extra:
        argv.extend(extra)
    code = ch.main(argv)
    # main already printed JSON on stdout; parse via a second scan for findings
    # is unnecessary if we capture? tests use capsys when needed. rescan below.
    cfg = ch.load_hygiene_config(repo, None)
    findings = ch.scan_range(
        repo,
        _git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").strip(),
        _git(repo, "rev-parse", "--verify", f"{head}^{{commit}}").strip(),
        cfg,
    )
    return code, [ch.asdict(item) if hasattr(ch, "asdict") else {"kind": item.kind, "where": item.where} for item in findings]


def test_planted_name_is_a_finding_and_default_exit_is_zero(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", f"hello {_PLANTED_NAME} here\n")
    _commit(repo, "add notes\n\nRole: builder\n")
    code, findings = _scan(repo, base)
    assert code == 0
    kinds = {item["kind"] for item in findings}
    assert "personal name token" in kinds
    assert any(item["where"].endswith("diff") for item in findings)
    assert ch.main(["--root", str(repo), "--base", base, "--head", "HEAD", "--strict"]) == 1


def test_clean_range_is_silent_under_strict(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(
        repo,
        "docs/note.md",
        "hello example user. see /home/<user>/proj and desk@example.com and 192.0.2.1.\n",
    )
    _commit(repo, "add notes\n\nRole: builder\n")
    code = ch.main(
        ["--root", str(repo), "--base", base, "--head", "HEAD", "--strict", "--json"]
    )
    assert code == 0


def test_crash_on_missing_git_repo(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    assert ch.main(["--root", str(empty), "--base", "HEAD"]) == 2


def test_email_home_ip_secret_junk_and_pr_body(tmp_path: Path, capsys) -> None:
    repo, base = _base_repo(tmp_path)
    body = "\n".join(
        [
            f"mail {_PLANTED_EMAIL}",
            f"path {_PLANTED_HOME}",
            f"addr {_PLANTED_IP}",
            f"tok {_PLANTED_PAT}",
        ]
    )
    _write(repo, "docs/note.md", body + "\n")
    _write(repo, "docs/note.bak", "old\n")
    _commit(repo, "add notes\n\nRole: builder\n")
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps(
            {
                "pull_request": {
                    "title": "add notes",
                    "body": f"see {_PLANTED_NAME}",
                    "number": 1,
                }
            }
        ),
        encoding="utf-8",
    )
    code = ch.main(
        [
            "--root",
            str(repo),
            "--base",
            base,
            "--head",
            "HEAD",
            "--event",
            str(event),
            "--json",
        ]
    )
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    kinds = {item["kind"] for item in payload["findings"]}
    wheres = {item["where"] for item in payload["findings"]}
    assert "email outside placeholder domains" in kinds
    assert "home path with a login name" in kinds
    assert "non-documentation address" in kinds
    assert "credential shape (github-pat)" in kinds
    assert "junk file (backup)" in kinds
    assert "personal name token" in kinds
    assert "docs/note.bak" in wheres
    assert "PR body" in wheres
    # The report must not echo the planted values.
    blob = captured.out + captured.err
    assert _PLANTED_EMAIL not in blob
    assert _PLANTED_HOME not in blob
    assert _PLANTED_IP not in blob
    assert _PLANTED_PAT not in blob
    assert _PLANTED_NAME not in blob


# WSL and key shapes ported from the md2xarticle reference scanner.
# These are assembled at run time so this file does not trip the scan itself.
_BS = "\\"
_WSL = "w" + "sl"
_LOGIN = "fixture" + "login"


def test_wsl_home_paths_are_findings() -> None:
    for text in (
        _BS * 2 + _WSL + "$" + _BS + "ubuntu" + _BS + "home" + _BS + _LOGIN,
        _BS * 2 + _WSL + ".localhost" + _BS + "ubuntu" + _BS + "home" + _BS + _LOGIN,
        _BS * 4 + _WSL + ".localhost" + _BS * 2 + "ubuntu",
        "//" + _WSL + "$/ubuntu/home/" + _LOGIN,
        "//" + _WSL + ".localhost/ubuntu/home/" + _LOGIN,
        "/mnt/c/" + "Users/" + _LOGIN + "/desktop",
        "cd /mnt/d/" + "users/" + _LOGIN,
    ):
        assert ch.text_has_personal_home_path(text), text


def test_wsl_placeholders_and_near_misses_pass() -> None:
    for text in (
        "open the folder through " + _BS * 2 + _WSL + "$",
        _BS * 2 + _WSL + ".localhost" + _BS + "<distro>" + _BS + "home",
        "//" + _WSL + "$/<distro>/home/<user>",
        "/mnt/c/" + "Users/<user>/out",
        "/mnt/c/" + "Users/%USERNAME%",
        "/mnt/c/program files/app",
        "/mnt/data/users/list.csv",
        "run " + _WSL + ".exe and " + _WSL + " --install",
    ):
        assert not ch.text_has_personal_home_path(text), text


def test_pypi_and_sk_key_shapes_are_findings() -> None:
    body = "PLANTED0FIXTURE0NOT0A0REAL0TOKEN0000"
    assert "pypi-token" in ch.credential_kinds("pypi-" + "AgEIcHlwaS5vcmc" + body)
    assert "sk-api-key" in ch.credential_kinds("sk" + "-" + body)
    assert "sk-api-key" in ch.credential_kinds("sk" + "-proj-" + body)
    for text in ("pypi-AgE", "sk" + "-abc123", "pypi" + "-" + "a" * 40):
        assert ch.credential_kinds(text) == [], text


_UTF8_BOM = b"\xef\xbb\xbf"
_UTF16LE_BOM = b"\xff\xfe"
_UTF16BE_BOM = b"\xfe\xff"


def test_planted_utf8_and_utf16_bom_are_findings(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    (repo / "docs" / "utf8.md").write_bytes(_UTF8_BOM + b"hello example user\n")
    (repo / "docs" / "utf16.md").write_bytes(_UTF16LE_BOM + b"h\x00i\x00\n\x00")
    (repo / "docs" / "utf16be.md").write_bytes(_UTF16BE_BOM + b"\x00h\x00i\x00\n")
    _commit(repo, "add notes\n\nRole: builder\n")
    _code, findings = _scan(repo, base)
    kinds_by_where = {(item["kind"], item["where"]) for item in findings}
    assert ("byte-order mark", "docs/utf8.md") in kinds_by_where
    assert ("byte-order mark", "docs/utf16.md") in kinds_by_where
    assert ("byte-order mark", "docs/utf16be.md") in kinds_by_where


def test_planted_typographic_mark_is_a_finding(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    _write(repo, "docs/note.md", "see this " + "\u2192" + " there\n")
    _commit(repo, "add notes\n\nRole: builder\n")
    _code, findings = _scan(repo, base)
    kinds = {item["kind"] for item in findings}
    assert "typographic mark U+2192" in kinds


def test_staged_bom_fails_under_strict(tmp_path: Path) -> None:
    repo, _base = _base_repo(tmp_path)
    planted = repo / "docs" / "planted.md"
    planted.write_bytes(_UTF8_BOM + b"hello example user\n")
    _git(repo, "add", "docs/planted.md")
    code = ch.main(
        ["--root", str(repo), "--staged", "--strict", "--json"]
    )
    assert code == 1
    planted.write_bytes(b"hello example user\n")
    _git(repo, "add", "docs/planted.md")
    assert ch.main(["--root", str(repo), "--staged", "--strict", "--json"]) == 0


def test_non_ascii_path_in_range_is_readable(tmp_path: Path) -> None:
    repo, base = _base_repo(tmp_path)
    planted = "caf" + "\u00e9" + ".txt"
    (repo / planted).write_bytes(_UTF8_BOM + b"hello example user\n")
    _commit(repo, "add cafe\n\nRole: builder\n")
    saved = {k: os.environ.get(k) for k in list(os.environ) if k.startswith("GIT_CONFIG_")}
    try:
        for k in saved:
            del os.environ[k]
        os.environ["GIT_CONFIG_COUNT"] = "1"
        os.environ["GIT_CONFIG_KEY_0"] = "core.quotePath"
        os.environ["GIT_CONFIG_VALUE_0"] = "true"
        code, findings = _scan(repo, base, strict=True)
    finally:
        for k in list(os.environ):
            if k.startswith("GIT_CONFIG_"):
                del os.environ[k]
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v
    assert code == 1
    kinds_by_where = {(item["kind"], item["where"]) for item in findings}
    assert ("byte-order mark", planted) in kinds_by_where


def test_gitlink_in_range_and_staged_does_not_crash(tmp_path: Path) -> None:
    gitlink_sha = "1234567890123456789012345678901234567890"
    repo, base = _base_repo(tmp_path)
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_sha},sub")
    _git(repo, "commit", "-qm", "add gitlink\n\nRole: builder\n")
    code, findings = _scan(repo, base, strict=True)
    assert code in (0, 1)
    assert all(item.get("where") != "sub" for item in findings)

    staged, _base = _base_repo(tmp_path / "staged")
    _git(staged, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_sha},sub")
    assert ch.main(["--root", str(staged), "--staged", "--strict", "--json"]) in (0, 1)


def test_junk_symlink_and_gitlink_are_findings(tmp_path: Path) -> None:
    gitlink_sha = "1234567890123456789012345678901234567890"
    repo, base = _base_repo(tmp_path)
    # symlink target starts with a UTF-8 BOM; that must not be read as a blob.
    os.symlink("\ufeff-target", repo / "x.orig")
    _git(repo, "add", "x.orig")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_sha},.DS_Store")
    _git(repo, "commit", "-qm", "add junk names\n\nRole: builder\n")
    code, findings = _scan(repo, base, strict=True)
    assert code == 1
    kinds_by_where = {(item["kind"], item["where"]) for item in findings}
    assert ("junk file (backup)", "x.orig") in kinds_by_where
    assert ("junk file (os-junk)", ".DS_Store") in kinds_by_where
    assert ("byte-order mark", "x.orig") not in kinds_by_where

    staged, _base = _base_repo(tmp_path / "staged")
    os.symlink("\ufeff-target", staged / "x.orig")
    _git(staged, "add", "x.orig")
    _git(staged, "update-index", "--add", "--cacheinfo", f"160000,{gitlink_sha},.DS_Store")
    assert ch.main(["--root", str(staged), "--staged", "--strict", "--json"]) == 1
    staged_findings = ch.scan_staged(staged, ch.load_hygiene_config(staged, None))
    staged_kinds = {(item.kind, item.where) for item in staged_findings}
    assert ("junk file (backup)", "x.orig") in staged_kinds
    assert ("junk file (os-junk)", ".DS_Store") in staged_kinds
    assert ("byte-order mark", "x.orig") not in staged_kinds


def test_non_utf8_path_blob_is_read_by_oid(tmp_path: Path, capsys) -> None:
    repo, base = _base_repo(tmp_path)
    rel = b"caf\xe9.txt"
    dest = os.fsencode(repo) + b"/" + rel
    with open(dest, "wb") as fh:
        fh.write(_UTF8_BOM + b"hello example user\n")
    env = os.environ.copy()
    env.update(_GIT_ENV)
    subprocess.run(
        [b"git", b"add", b"--", rel],
        cwd=os.fsencode(repo),
        check=True,
        capture_output=True,
        env=env,
    )
    _git(repo, "commit", "-qm", "add latin1 name\n\nRole: builder\n")
    code = ch.main(
        ["--root", str(repo), "--base", base, "--head", "HEAD", "--strict", "--json"]
    )
    captured = capsys.readouterr()
    assert code == 1, captured.err
    payload = json.loads(captured.out)
    assert any(item["kind"] == "byte-order mark" for item in payload["findings"])

    staged, _base = _base_repo(tmp_path / "staged")
    dest = os.fsencode(staged) + b"/" + rel
    with open(dest, "wb") as fh:
        fh.write(_UTF8_BOM + b"hello example user\n")
    subprocess.run(
        [b"git", b"add", b"--", rel],
        cwd=os.fsencode(staged),
        check=True,
        capture_output=True,
        env=env,
    )
    capsys.readouterr()
    staged_code = ch.main(["--root", str(staged), "--staged", "--strict", "--json"])
    staged_captured = capsys.readouterr()
    assert staged_code == 1, staged_captured.err
    staged_payload = json.loads(staged_captured.out)
    assert any(item["kind"] == "byte-order mark" for item in staged_payload["findings"])
