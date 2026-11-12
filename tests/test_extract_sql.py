"""SQL extractor: shapes per the contract table, dialects, purity, parse errors."""
import os
import random
from pathlib import Path

import pytest

from rco import build, canon, check, fixture
from rco.extract.sql import SqlExtractor
from rco.model import Kind, Node, SourceFile, walk

EX = SqlExtractor()
HERE = Path(__file__).parent
CASE = "sql_basic"


def _nodes(src: str, path: str = "db/s.sql") -> list[Node]:
    return EX.extract(SourceFile(path, "0" * 40, src.encode()))


def _one(src: str) -> Node:
    (n,) = _nodes(src)
    return n


def _child(n: Node, name: str, kind: Kind = Kind.COLUMN) -> Node:
    (c,) = [c for c in n.children if c.name == name and c.kind is kind]
    return c


def _fixture(name: str) -> list[Node]:
    p = HERE / "fixtures" / CASE / "db" / name
    return EX.extract(SourceFile(f"db/{name}", "0" * 40, p.read_bytes()))


def _top(nodes: list[Node], name: str) -> Node:
    (n,) = [n for n in nodes if n.name == name]
    return n


def test_claims_sql_only():
    assert EX.claims("db/schema.sql") and EX.claims("db/OLD.SQL")
    assert not EX.claims("db/schema.py") and not EX.claims("sql/readme.md")
    assert EX.version != "0"


def test_table_columns_sig_default_doc():
    users = _top(_fixture("schema.sql"), "users")
    assert users.kind is Kind.TABLE and users.path == "db/schema.sql"
    assert users.doc == "accounts that can sign in"
    created = _child(users, "created")
    assert created.kind is Kind.COLUMN and created.path == ""
    assert created.sig == "DATETIME(6) NOT NULL ON UPDATE CURRENT_TIMESTAMP(6)"
    assert created.attrs == (("default", "CURRENT_TIMESTAMP(6)"),)
    assert _child(users, "email").sig == "VARCHAR(255) NULL"
    assert _child(users, "email").doc == "contact address"
    assert _child(users, "handle").doc == "login handle, unique per account."
    assert _child(users, "score").sig == "DECIMAL(10,2) NOT NULL"
    assert dict(_child(users, "score").attrs) == {"default": "-1"}
    assert _child(users, "score").doc == "running score"
    assert dict(_child(users, "kind").attrs) == {"default": "'member'"}


def test_names_lower_case_unless_quoted():
    t = _one('CREATE TABLE Accounts (ID int, "Display Name" text, `Raw` int);')
    assert t.name == "accounts"
    assert [c.name for c in t.children] == ["id", "Display Name", "Raw"]
    assert _one('CREATE TABLE app."Events" (id int);').name == "app.Events"


def test_indexes_from_table_constraints():
    users = _top(_fixture("schema.sql"), "users")
    idx = {c.name: c.sig for c in users.children if c.kind is Kind.INDEX}
    assert idx == {
        "PRIMARY": "PRIMARY (id)",
        "idx_handle": "UNIQUE (handle)",
        "email": "UNIQUE (email)",
        "idx_kind_created": "(kind,created DESC)",
    }
    # foreign keys and table CHECKs are not nodes in Phase 1
    assert {c.kind for c in users.children} == {Kind.COLUMN, Kind.INDEX}


def test_create_index_attaches_to_table_in_same_file():
    nodes = _fixture("pg.sql")
    ev = _top(nodes, "app.Events")
    idx = {c.name: c.sig for c in ev.children if c.kind is Kind.INDEX}
    assert idx["events_user_at"] == "UNIQUE (user_id,at)"
    assert "events_tag" in idx
    # an index on a table this file does not create is not a node here
    assert all(n.name != "other_table" for n in nodes)
    assert all(c.name != "elsewhere_idx" for n in nodes for c in n.children)


def test_temporary_tables_skipped():
    names = [n.name for n in _fixture("schema.sql")]
    assert "scratch" not in names and "tmp_ids" not in names


def test_procedure_and_function_sigs():
    nodes = _fixture("schema.sql")
    fn = _top(nodes, "user_count")
    assert fn.kind is Kind.PROCEDURE and fn.path == "db/schema.sql"
    assert fn.sig == "(p_kind VARCHAR(16)) RETURNS INT UNSIGNED"
    assert fn.doc == "how many users of a kind."
    proc = _top(nodes, "rename_user")
    assert proc.sig == "(IN p_id BIGINT UNSIGNED, IN p_handle VARCHAR(64), OUT p_ok TINYINT)"
    assert proc.doc == "rename one user; p_ok is 1 on success"
    pg = _top(_fixture("pg.sql"), "bump")
    assert pg.sig == "(p_id BIGINT, VARIADIC p_tags TEXT[]) RETURNS TABLE(ID BIGINT,N INT)"


def test_dollar_body_is_not_parsed():
    nodes = _fixture("pg.sql")
    assert sorted(n.name for n in nodes) == ["app.Events", "bump", "latest_events", "named_cols"]


def test_view_columns():
    nodes = _fixture("pg.sql")
    v = _top(nodes, "latest_events")
    assert v.kind is Kind.VIEW and v.doc == "latest event per user."
    assert [c.name for c in v.children] == ["user_id", "last_at", "n", "payload"]
    assert [c.name for c in _top(nodes, "named_cols").children] == ["a", "b"]


def test_delimiter_directive_and_a_column_named_delimiter():
    t = _one("CREATE TABLE t (\n  delimiter VARCHAR(4),\n  x INT\n);\n")
    assert [c.name for c in t.children] == ["delimiter", "x"]
    p = _one("DELIMITER $$\nCREATE PROCEDURE p(IN a INT)\nBEGIN\n  SELECT a;\nEND $$\nDELIMITER ;\n")
    assert p.sig == "(IN a INT)"


def test_body_change_keeps_node_signature_change_moves_it():
    a = _one("CREATE PROCEDURE p(IN a INT)\nBEGIN\n  SELECT a;\nEND;\n")
    b = _one("CREATE PROCEDURE p(IN a INT)\nBEGIN\n  SELECT a + 1;\n  SELECT 2;\nEND;\n")
    c = _one("CREATE PROCEDURE p(IN a INT, IN b INT)\nBEGIN\n  SELECT a;\nEND;\n")
    assert a == b and a != c


def test_whitespace_and_keyword_case_do_not_change_shape():
    a = _one("CREATE TABLE t (a varchar(10) not null default 'x', b decimal(4, 2));")
    b = _one("create table T (\n  a   VARCHAR( 10 )  NOT   NULL DEFAULT 'x',\n  b DECIMAL(4,2)\n)\n;")
    assert a == b


def test_no_line_numbers_in_nodes():
    src = "CREATE TABLE t (a INT);\nCREATE PROCEDURE p() BEGIN END;\n"
    assert _nodes(src) == _nodes("\n\n\n" + src)


def test_parse_error_is_a_file_node_not_an_exception():
    (n,) = _fixture("broken.sql")
    assert n == Node(Kind.FILE, "broken.sql", "db/broken.sql", attrs=(("error", "parse"),))
    assert _nodes("CREATE TABLE t (a INT, b INT;\n") == [
        Node(Kind.FILE, "s.sql", "db/s.sql", attrs=(("error", "parse"),))]


def test_never_raises_on_truncated_or_random_input():
    data = (HERE / "fixtures" / CASE / "db" / "schema.sql").read_bytes()
    rnd = random.Random(1167)
    inputs = [data[:k] for k in range(0, len(data), 7)]
    inputs += [bytes(rnd.randrange(256) for _ in range(200)) for _ in range(50)]
    for b in inputs:
        out = EX.extract(SourceFile("db/x.sql", "0" * 40, b))
        assert isinstance(out, list)


def test_data_only_file_has_no_nodes():
    assert _fixture("seed.sql") == []


def test_pure_same_input_same_output():
    src = SourceFile("db/s.sql", "0" * 40, (HERE / "fixtures" / CASE / "db" / "pg.sql").read_bytes())
    assert EX.extract(src) == EX.extract(src)


def test_selftest_plants_column_drift_on_a_real_column():
    with fixture.drift_repo() as (repo, sha):
        m = build.build_map(repo, sha)
    cols = [n for t in m.trees for _, n in walk(t) if n.kind is Kind.COLUMN]
    assert any(n.name == "email" for n in cols), "drift fixture needs a real SQL column"
    rep = check.selftest_on(m)
    assert all(r[3] for r in rep.rows)


def _files(root: Path) -> dict[str, bytes]:
    return {p.relative_to(root).as_posix(): p.read_bytes()
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_golden_sql_basic(tmp_path):
    """Regenerate with RCO_REGEN=1 and say why in the PR."""
    repo, sha = fixture.make_repo(tmp_path / CASE, _files(HERE / "fixtures" / CASE))
    got = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    assert got == canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha))) + b"\n"
    golden = HERE / "golden" / f"{CASE}.json"
    if os.environ.get("RCO_REGEN") == "1":
        golden.write_bytes(got)
    assert got == golden.read_bytes()
