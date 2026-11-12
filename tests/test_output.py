"""Output contract: stdout vs --out bytes, atomic write, formats, exit codes 0-3."""
import json
import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from rco import build, canon, cli, fixture, output
from rco.output import Exit, UsageError


@dataclass
class R:
    code: Exit = Exit.OK
    md: str = "# r\n"

    def to_obj(self):
        return {"b": [1, "\u00e9"], "a": 1}

    def to_markdown(self):
        return self.md

    def exit_code(self):
        return self.code


def test_json_is_canonical_plus_newline(capsysbinary):
    assert output.emit(R(), "json", None) is Exit.OK
    cap = capsysbinary.readouterr()
    assert cap.out == canon.canonical_json(R().to_obj()) + b"\n"
    assert cap.out == '{"a":1,"b":[1,"\u00e9"]}\n'.encode()
    assert cap.err == b""


def test_markdown_always_ends_with_one_newline(capsysbinary):
    output.emit(R(md="# r"), "markdown", None)
    assert capsysbinary.readouterr().out == b"# r\n"


def test_unknown_format_is_usage():
    with pytest.raises(UsageError):
        output.render(R(), "yaml")


@pytest.mark.parametrize("code", list(Exit))
def test_emit_returns_report_exit(code, capsysbinary):
    assert output.emit(R(code=code), "json", None) is code


def test_out_same_bytes_as_stdout_and_report_on_stderr_only(tmp_path, capsysbinary):
    out = tmp_path / "r.json"
    output.emit(R(), "json", None)
    stdout = capsysbinary.readouterr().out
    assert output.emit(R(), "json", out) is Exit.OK
    cap = capsysbinary.readouterr()
    assert out.read_bytes() == stdout
    assert cap.out == b"" and b"wrote" in cap.err
    assert sorted(p.name for p in tmp_path.iterdir()) == ["r.json"]


def test_out_replaces_existing_file(tmp_path):
    out = tmp_path / "r.md"
    out.write_text("old")
    output.emit(R(md="# new\n"), "markdown", out)
    assert out.read_text() == "# new\n"


def test_failed_replace_keeps_old_file_and_no_tmp(tmp_path, monkeypatch):
    out = tmp_path / "r.json"
    out.write_text("old")

    def boom(a, b):
        raise OSError("disk full")
    monkeypatch.setattr(output.os, "replace", boom)
    with pytest.raises(OSError):
        output.emit(R(), "json", out)
    assert out.read_text() == "old"
    assert not (tmp_path / "r.json.tmp").exists()


def test_bad_out_is_usage(tmp_path):
    with pytest.raises(UsageError):
        output.emit(R(), "json", tmp_path / "missing" / "r.json")
    with pytest.raises(UsageError):
        output.emit(R(), "json", tmp_path)


def test_timed_goes_to_stderr(capsys):
    with output.timed("build"):
        pass
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err.startswith("rco: build ") and cap.err.endswith("s\n")


# ------------------------------------------------------------- CLI end to end

def _repo(tmp_path):
    return fixture.make_repo(tmp_path / "r", fixture.DRIFT_FILES)


def test_cli_exit_0_and_out_matches_stdout(tmp_path, capsysbinary):
    repo, sha = _repo(tmp_path)
    assert cli.main(["tree", sha, "--repo", str(repo), "--format", "json"]) == Exit.OK
    stdout = capsysbinary.readouterr().out
    out = tmp_path / "m.json"
    assert cli.main(["tree", sha, "--repo", str(repo), "--format", "json", "--out", str(out)]) == 0
    assert out.read_bytes() == stdout
    assert json.loads(stdout)["sha"] == sha
    assert cli.main(["tree", sha, "--repo", str(repo)]) == 0
    assert capsysbinary.readouterr().out.startswith(b"# rco tree")


def test_cli_exit_1_on_findings_stdout_report_only(tmp_path, capsysbinary):
    repo, sha = _repo(tmp_path)
    mp = tmp_path / "m.json"
    cli.main(["tree", sha, "--repo", str(repo), "--format", "json", "--out", str(mp)])
    obj = json.loads(mp.read_text())
    obj["modules"][0]["module_hash"] = "sha256:" + "0" * 64
    mp.write_text(json.dumps(obj))
    capsysbinary.readouterr()
    assert cli.main(["check", str(mp), "--repo", str(repo), "--format", "json"]) == Exit.FINDINGS
    cap = capsysbinary.readouterr()
    assert json.loads(cap.out)["schema"] == "rco.check/1"     # stdout is only the report
    assert b"findings in" in cap.err                          # the timing is on stderr


@pytest.mark.parametrize("argv", [
    ["nope"], ["tree", "--format", "yaml"], ["tree", "no-such-rev"], ["check", "/no/map.json"],
])
def test_cli_exit_2_usage(tmp_path, argv, capsys):
    repo, _ = _repo(tmp_path)
    assert cli.main([*argv, "--repo", str(repo)]) == Exit.USAGE
    cap = capsys.readouterr()
    assert cap.out == "" and cap.err.startswith("rco: ")


def test_cli_exit_2_unwritable_out(tmp_path, capsys):
    repo, sha = _repo(tmp_path)
    bad = tmp_path / "nodir" / "m.json"
    assert cli.main(["tree", sha, "--repo", str(repo), "--out", str(bad)]) == Exit.USAGE
    assert not bad.parent.exists()


def test_cli_exit_3_internal_one_line(tmp_path, capsys, monkeypatch):
    repo, sha = _repo(tmp_path)
    monkeypatch.delenv("RCO_DEBUG", raising=False)

    def boom(*a, **k):
        raise RuntimeError("kaput")
    monkeypatch.setattr(build, "build_map", boom)
    assert cli.main(["tree", sha, "--repo", str(repo)]) == Exit.INTERNAL
    cap = capsys.readouterr()
    assert cap.out == ""
    assert cap.err == "rco: internal error: RuntimeError: kaput\n"
    monkeypatch.setenv("RCO_DEBUG", "1")
    with pytest.raises(RuntimeError):
        cli.main(["tree", sha, "--repo", str(repo)])
