"""rco live: the map against captured real outputs (schema dump, OpenAPI, --help)."""
import json

import pytest

from rco import build, cli, fixture
from rco.live import compare_help, compare_openapi, compare_schema, norm_route
from rco.output import Exit, UsageError

REPO = {
    "db/schema.sql": ("CREATE TABLE users (id INT PRIMARY KEY, email TEXT, legacy TEXT);\n"
                      "CREATE TABLE audit (id INT);\n"),
    "svc/app.py": (
        "from fastapi import FastAPI\napp = FastAPI()\n\n"
        "@app.get('/users/{user_id}')\ndef get_user(user_id: int):\n    pass\n\n"
        "@app.post('/users/')\ndef make_user():\n    pass\n\n"
        "@app.delete('/old')\ndef old():\n    pass\n"),
    "tool/cli.py": (
        "import argparse\n\n\ndef main():\n"
        "    p = argparse.ArgumentParser(prog='tool')\n"
        "    sub = p.add_subparsers(dest='cmd')\n"
        "    r = sub.add_parser('run')\n"
        "    r.add_argument('--fast', action='store_true')\n"
        "    r.add_argument('--old-flag')\n"
        "    return p\n"),
}

DUMP = """\
CREATE TABLE public.users (
    id integer NOT NULL,
    email text,
    created_at timestamp with time zone
);
CREATE TABLE public.sessions (id integer);
"""

OPENAPI = {"openapi": "3.0.0", "paths": {
    "/users/{id}": {"get": {}, "parameters": []},
    "/users": {"post": {}},
    "/health": {"get": {}},
}}

HELP = """usage: tool run [-h] [--fast] [--slow]

options:
  -h, --help  show this help message and exit
  --fast
  --slow      go slow
"""


@pytest.fixture
def repo_map(tmp_path):
    repo, sha = fixture.make_repo(tmp_path / "r", REPO)
    return repo, build.build_map(repo, sha)


def test_norm_route():
    assert norm_route("/u/<int:id>/x") == norm_route("/u/{uid}/x") == norm_route("/u/:id/x") == "/u/{}/x"
    assert norm_route("/a/") == "/a" and norm_route("/") == "/"


def test_schema(repo_map):
    _, m = repo_map
    got = {(f.status, f.key) for f in compare_schema(m, DUMP)}
    assert got == {("missing", "column:users.created_at"), ("extra", "column:users.legacy"),
                   ("missing", "table:public.sessions"), ("extra", "table:audit")}
    with pytest.raises(UsageError):
        compare_schema(m, "CREATE TABLE (((")


def test_openapi(repo_map):
    _, m = repo_map
    got = {(f.status, f.key) for f in compare_openapi(m, OPENAPI)}
    assert got == {("missing", "route:GET /health"), ("extra", "route:DELETE /old")}
    swagger = {"swagger": "2.0", "basePath": "/v1", "paths": {"/x": {"get": {}}}}
    assert {f.key for f in compare_openapi(m, swagger)} >= {"route:GET /v1/x"}
    with pytest.raises(UsageError):
        compare_openapi(m, {"info": {}})


def test_help(repo_map):
    _, m = repo_map
    got = {(f.status, f.key) for f in compare_help(m, "tool run", HELP)}
    assert got == {("missing", "flag:tool run --slow"), ("extra", "flag:tool run --old-flag")}
    with pytest.raises(UsageError):
        compare_help(m, "nope", HELP)


def test_cli_exit_codes_and_determinism(repo_map, tmp_path, capsys):
    repo, m = repo_map
    (tmp_path / "dump.sql").write_text(DUMP)
    (tmp_path / "api.json").write_text(json.dumps(OPENAPI))
    (tmp_path / "help.txt").write_text(HELP)
    argv = ["live", "--repo", str(repo), "--schema", str(tmp_path / "dump.sql"),
            "--openapi", str(tmp_path / "api.json"),
            "--help-text", f"tool run={tmp_path / 'help.txt'}", "--format", "json"]
    assert cli.main(argv) == Exit.FINDINGS
    a = capsys.readouterr().out
    assert cli.main(argv) == Exit.FINDINGS
    assert capsys.readouterr().out == a
    obj = json.loads(a)
    assert obj["schema"] == "rco.live/1" and len(obj["findings"]) == 8
    clean = tmp_path / "clean.txt"
    clean.write_text("--fast --old-flag\n")
    assert cli.main(["live", "--repo", str(repo), "--help-text", f"tool run={clean}"]) == Exit.OK
    assert cli.main(["live", "--repo", str(repo)]) == Exit.USAGE
    assert cli.main(["live", "--repo", str(repo), "--help-text", "nofile"]) == Exit.USAGE
