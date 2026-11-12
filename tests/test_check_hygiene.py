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
