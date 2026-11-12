"""Redaction pass: README Secrets list, scope, idempotence, ModuleHash version.

Every secret here is assembled at runtime from fragments, so the repo's
hygiene report never sees a secret-shaped literal in this file.
"""
import pytest

from rco import build, canon, extract, fixture, redact
from rco.model import Kind, Module, Node, SourceFile

P = redact.PLACEHOLDER
TOK = "".join(("q7Rk", "Zp2W", "xL9m", "Tb4N", "vY8c", "Hs3J", "dF6g"))  # 28 chars, mixed
GH = "gh" + "p_" + "A1b2C3d4E5" * 4
PEM = "-" * 5 + "BEGIN RSA " + "PRIVATE KEY" + "-" * 5
PEM_END = "-" * 5 + "END RSA " + "PRIVATE KEY" + "-" * 5
HOME = "/" + "home" + "/" + "jdoe"
MAIL = "jdoe" + "@" + "corp" + ".io"


@pytest.mark.parametrize("raw, want", [
    (f"key {PEM}\\nMIIEow\\n{PEM_END} tail", f"key {P} tail"),
    (f"{PEM} no end", P),
    (f"Authorization: Bearer {TOK}", f"Authorization: Bearer {P}"),
    (f"use {GH} here", f"use {P} here"),
    ("pypi" + "-AgE" + "x" * 24, P),
    ("xox" + "b-" + "1234567890-abc", P),
    ("sk" + "-" + "z" * 24, P),
    ("AK" + "IA" + "ABCDEFGH12345678", P),
    ("postgres://app:" + "pw" + "123@db.example.com/x", f"postgres://{P}@db.example.com/x"),
    ('api_key = "abc"', f'api_key = "{P}"'),
    ("password: 'letmein'", f"password: '{P}'"),
    ("db_pass" + "word=letmein99", f"db_pass" + f"word={P}"),
    (f"token {TOK} end", f"token {P} end"),
    (f"see {HOME}/src", f"see /home/{P}/src"),
    ("C:\\Users\\" + "jdoe" + "\\x", f"C:\\Users\\{P}\\x"),
    (f"mail {MAIL}", f"mail {P}"),
])
def test_redacts_readme_list(raw, want):
    assert redact.redact_text(raw) == want


@pytest.mark.parametrize("keep", [
    "Run the engine.",
    "token: the bearer token to send",
    "Basic authentication over TLS",
    "password=None",
    "commit 0123456789abcdef0123456789abcdef01234567",
    "HttpServerV2HandlerFactory",
    "test_skeleton_registry_2026",
    "src/rco/extract/__init__.py",
    "repo-context-optimizer-phase-one",
    "/home/<user>/x and /home/$USER/y",
    "bot@example.com and x@users.noreply.github.com",
    "GET /api/v1/doing",
])
def test_keeps_ordinary_text(keep):
    assert redact.redact_text(keep) == keep


def test_idempotent_on_every_case():
    samples = [f"Bearer {TOK}", GH, f"{PEM} x", f"{HOME}/a {MAIL}", 'secret="s3cr3t!"',
               "https://u:" + "p@h/", "api_key=" + P, f"'{P}'", P]
    for s in samples:
        once = redact.redact_text(s)
        assert redact.redact_text(once) == once


def test_node_scope_never_touches_identity():
    n = Node(Kind.CONSTANT, "API_KEY", "src/a.py", sig="str", doc=f"see {MAIL}",
             attrs=(("value", f"'{TOK}'"),),
             children=(Node(Kind.FIELD, MAIL, sig=f"'{TOK}'"),))
    r = redact.redact_node(n)
    assert (r.kind, r.name, r.path) == (n.kind, n.name, n.path)
    assert r.doc == f"see {P}" and dict(r.attrs)["value"] == P
    assert r.children[0].name == MAIL          # names are never redacted
    assert r.children[0].sig == f"'{P}'"       # FIELD sig is


def test_ordinary_sigs_and_names_untouched():
    for sig in ("(token: str, other: str='x') -> None", "(password=None)",
                "(x: dict={'a': 1}, *, verbose: bool=False) -> int", "(self) -> str"):
        n = Node(Kind.FUNCTION, "login", "a.py", sig=sig)
        assert redact.redact_node(n) == n
    route = Node(Kind.ROUTE, "GET /api/v1/token/{id}", "r.py")
    flag = Node(Kind.FLAG, "--api-key")
    assert redact.redact_node(route) == route and redact.redact_node(flag) == flag


@pytest.mark.parametrize("value, redacted", [
    ("'hunter2'", True), ('"x"', True), ("os.environ['K']", True),
    ("None", False), ("''", False), ("3600", False), ("...", False), ("True", False),
])
def test_credential_named_value(value, redacted):
    for kind, key in ((Kind.CONSTANT, "value"), (Kind.FIELD, "value"), (Kind.FLAG, "default")):
        n = Node(kind, "DB_PASSWORD" if kind is not Kind.FLAG else "--password", attrs=((key, value),))
        got = dict(redact.redact_node(n).attrs)[key]
        assert (got == P) is redacted, (kind, value, got)


SHORT = "k9" + "Xq" + "2w"   # a short secret: kept in the sig by the 40-char default rule


@pytest.mark.parametrize("kind", [Kind.FUNCTION, Kind.METHOD])
def test_secret_default_in_sig_is_redacted(kind):
    n = Node(kind, "login", sig=f"(user: str, api_key: str='{SHORT}', n: int=3) -> None")
    assert redact.redact_node(n).sig == f"(user: str, api_key: str='{P}', n: int=3) -> None"
    tok = Node(kind, "send", sig=f"(self, auth='Bearer {TOK[:16]}') -> None")
    assert SHORT not in redact.redact_node(n).sig and TOK[:16] not in redact.redact_node(tok).sig


def test_other_attrs_untouched():
    n = Node(Kind.FUNCTION, "f", attrs=(("decorators", f"auth('{TOK}')"),))
    assert redact.redact_node(n) == n


def test_version_is_in_registry_and_module_hash():
    assert dict(extract.versions())["redact"] == redact.VERSION != "0"


def test_version_bump_moves_module_hash(monkeypatch):
    mod = Module("pkg", ("pkg/a.py",))
    files = [SourceFile("pkg/a.py", "1" * 40, b"X = 1\n")]
    before = build.build_tree(mod, files)
    monkeypatch.setattr(redact, "VERSION", redact.VERSION + ".test")
    after = build.build_tree(mod, files)
    assert before.module_hash != after.module_hash
    assert canon.tree_hash(before) != canon.tree_hash(after)   # extractors list is in the tree


def test_secret_default_never_reaches_map_bytes_or_stdout(tmp_path, capsysbinary):
    from rco import cli
    src = (f"def login(user: str, api_key: str = '{SHORT}') -> None:\n    pass\n\n\n"
           f"class Client:\n    def connect(self, password='{SHORT}x') -> None:\n        pass\n")
    repo, sha = fixture.make_repo(tmp_path / "r", {"pkg/__init__.py": "", "pkg/a.py": src})
    m = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    assert SHORT.encode() not in m and b"api_key: str=" in m
    for fmt in ("json", "markdown"):
        assert cli.main(["tree", sha, "--repo", str(repo), "--format", fmt]) == 0
        out = capsysbinary.readouterr().out
        assert SHORT.encode() not in out and P.encode() in out


def test_secret_never_reaches_map_bytes(tmp_path):
    src = (f'"""Module. Contact {MAIL}."""\n'
           f"API_TOKEN = '{TOK}'\n"
           f"URL = 'https://bot:{TOK[:8]}@example.com/x'\n"
           f"BASE = '{HOME}/data'\n\n"
           f"def run() -> None:\n    \"\"\"Uses Bearer {TOK}.\"\"\"\n")
    repo, sha = fixture.make_repo(tmp_path / "r", {"pkg/__init__.py": "", "pkg/a.py": src})
    a = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    b = canon.canonical_json(canon.map_to_obj(build.build_map(repo, sha)))
    assert a == b
    for leak in (TOK, TOK[:8], MAIL, "jdoe"):
        assert leak.encode() not in a, leak
    assert b"API_TOKEN" in a and P.encode() in a
