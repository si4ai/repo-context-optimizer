"""Redaction pass (docs/design/tree-contract.md, section 7; README Secrets).

Applied centrally by build.py after extraction and before hashing, so a
secret never reaches a Tree, a hash input, a Map file or stdout. VERSION
feeds ModuleHash through extract.versions(): changing any rule below
changes output bytes, so it must bump VERSION.

What is replaced with PLACEHOLDER:

- private keys and PEM blocks (to the END line, or to the end of the text)
- bearer and basic auth tokens, and known token shapes (GitHub, PyPI,
  Slack, sk- API keys, AWS access key ids)
- the user:password part of URLs that embed credentials
- credential-like assignments in text (``password = "..."``, ``api_key=...``)
- the value or default of a CONSTANT, FIELD or FLAG whose name is
  credential-like (``API_KEY = "..."``), unless the value is empty, None,
  a bool, a number or ``...``
- high-entropy secret-shaped strings
- email addresses outside placeholder domains
- the login segment of home paths (``/home/<user>``, ``/Users/<user>``,
  ``C:\\Users\\<user>``)

Redaction never touches kind, name, path, route paths, flag names, or
table and column names. Pure (no I/O, no clock) and idempotent:
redact_text(redact_text(s)) == redact_text(s).
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import replace

from rco.model import Kind, Node

VERSION = "1"
PLACEHOLDER = "<redacted>"

_SIG_KINDS = frozenset({Kind.CONSTANT, Kind.FIELD})
_ATTR_KEYS = frozenset({"value", "default"})
# Kinds whose credential-like name redacts the whole value or default.
_NAMED_VALUE_KINDS = frozenset({Kind.CONSTANT, Kind.FIELD, Kind.FLAG})

_P = re.escape(PLACEHOLDER)
_DASH5 = "-" * 5

# PEM private key: BEGIN ... END, or BEGIN to the end of the text.
_PEM_RE = re.compile(
    _DASH5 + r"BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY" + _DASH5
    + r"(?:.*?" + _DASH5 + r"END [A-Z0-9 ]{0,40}PRIVATE KEY" + _DASH5 + r"|.*\Z)",
    re.DOTALL,
)
# scheme://user:password@host -> scheme://<redacted>@host
_URL_CRED_RE = re.compile(r"(?i)\b([a-z][a-z0-9+.-]{1,20}://)(?!" + _P + r"@)[^\s/@:'\"]+:[^\s/@'\"]+@")
# Authorization: Bearer <token>; the token holds a digit or a token symbol.
_AUTH_RE = re.compile(
    r"(?i)\b(bearer|basic)(\s+)(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])[A-Za-z0-9._~+/-]{12,}=*"
)
_EDGE_L = r"(?<![A-Za-z0-9_-])"
_EDGE_R = r"(?![A-Za-z0-9_-])"
_TOKEN_RES = tuple(re.compile(_EDGE_L + body + _EDGE_R) for body in (
    r"(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})",
    r"pypi-AgE[A-Za-z0-9_-]{20,}",
    r"xox[abprs]-[A-Za-z0-9-]{10,}",
    r"sk-(?:proj-|ant-[a-z0-9]{2,8}-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}",
    r"(?:AKIA|ASIA)[A-Z0-9]{16}",
))
_CRED_WORD = (r"(?:passw(?:or)?d|passwd|pwd|secret|token|api[_-]?key|apikey|access[_-]?key"
              r"|private[_-]?key|credential|auth[_-]?key)s?")
_CRED_NAME_RE = re.compile(r"(?i)" + _CRED_WORD)
# name = "value" / name: 'value'  (quoted, = or :)
_ASSIGN_Q_RE = re.compile(
    r"(?i)(\b[A-Za-z0-9_-]*" + _CRED_WORD + r"[\"']?\s*[=:]\s*)([\"'])(?!" + _P + r"\2)[^\"'\n]+\2"
)
# name=value  (unquoted, = only, 6 or more characters)
_ASSIGN_U_RE = re.compile(
    r"(?i)(\b[A-Za-z0-9_-]*" + _CRED_WORD + r"\s*=\s*)(?!" + _P + r")(?![\"'])[^\s\"',;)\]}]{6,}"
)
_SAFE_VALUES = frozenset({"", "None", "...", "True", "False", "''", '""', "[]", "()", "{}",
                          PLACEHOLDER, f"'{PLACEHOLDER}'"})
_NUMBER_RE = re.compile(r"[-+]?(?:\d[\d_]*)(?:\.\d+)?(?:[eE][-+]?\d+)?")
_CANDIDATE_RE = re.compile(r"(?<![A-Za-z0-9+/=_-])[A-Za-z0-9+/=_-]{20,}(?![A-Za-z0-9+/=_-])")
_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_KEBAB_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)+")
_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})(?![A-Za-z0-9-])")
_PLACEHOLDER_DOMAINS = frozenset({
    "example.com", "example.org", "example.net",
    "users.noreply.github.com", "users.noreply.gitlab.com", "noreply.gitlab.com",
})
_PLACEHOLDER_TLDS = frozenset({"example", "test", "invalid", "localhost"})
_HOME_RE = re.compile(
    r"((?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)+[Uu][Ss][Ee][Rr][Ss](?:\\\\|\\|/)+"
    r"|(?<![\w.~-])/(?:home|Users)/)"
    r"([^\\/\s\"'`<>()\[\]{}:;,|]+)"
)
_HOME_PLACEHOLDERS = frozenset({"example", "user", "username", "you", "name", "..."})


def _entropy(s: str) -> float:
    n = len(s)
    return -sum(c / n * math.log2(c / n) for c in Counter(s).values())


def _cls(ch: str) -> int:
    if ch.islower():
        return 0
    if ch.isupper():
        return 1
    if ch.isdigit():
        return 2
    return 3


def is_high_entropy(s: str) -> bool:
    """A secret-shaped token: long, mixed classes, high entropy, many class switches.

    Pure hex (commit SHAs, digests) and kebab-case words are kept; an
    identifier like ``CamelCaseName2`` has too few class switches.
    """
    if len(s) < 20 or _HEX_RE.fullmatch(s) or _KEBAB_RE.fullmatch(s):
        return False
    classes = {_cls(c) for c in s}
    if len(classes) < 3 or not ({0, 1} & classes) or 2 not in classes:
        return False
    switches = sum(1 for a, b in zip(s, s[1:]) if _cls(a) != _cls(b))
    return _entropy(s) >= 3.5 and switches / (len(s) - 1) >= 0.4


def _email_ok(domain: str) -> bool:
    d = domain.casefold()
    if d in _PLACEHOLDER_DOMAINS or any(d.endswith("." + p) for p in _PLACEHOLDER_DOMAINS):
        return True
    return d.rsplit(".", 1)[-1] in _PLACEHOLDER_TLDS


def _home(m: re.Match) -> str:
    seg = m.group(2)
    if seg.casefold() in _HOME_PLACEHOLDERS or seg[:1] in "$%{*.<":
        return m.group(0)
    return m.group(1) + PLACEHOLDER


def redact_text(s: str) -> str:
    """Replace secret-shaped and personal substrings with PLACEHOLDER."""
    if not s:
        return s
    s = _PEM_RE.sub(PLACEHOLDER, s)
    s = _URL_CRED_RE.sub(lambda m: m.group(1) + PLACEHOLDER + "@", s)
    s = _AUTH_RE.sub(lambda m: m.group(1) + m.group(2) + PLACEHOLDER, s)
    for rx in _TOKEN_RES:
        s = rx.sub(PLACEHOLDER, s)
    s = _ASSIGN_Q_RE.sub(lambda m: m.group(1) + m.group(2) + PLACEHOLDER + m.group(2), s)
    s = _ASSIGN_U_RE.sub(lambda m: m.group(1) + PLACEHOLDER, s)
    s = _CANDIDATE_RE.sub(lambda m: PLACEHOLDER if is_high_entropy(m.group(0)) else m.group(0), s)
    s = _EMAIL_RE.sub(lambda m: m.group(0) if _email_ok(m.group(1)) else PLACEHOLDER, s)
    s = _HOME_RE.sub(_home, s)
    return s


def credential_name(name: str) -> bool:
    """True for names like API_KEY, db_password, --token (README Secrets)."""
    return bool(_CRED_NAME_RE.search(name))


def _secret_value(v: str) -> bool:
    return v not in _SAFE_VALUES and not _NUMBER_RE.fullmatch(v)


def _attr(n: Node, k: str, v: str) -> str:
    if k not in _ATTR_KEYS:
        return v
    if n.kind in _NAMED_VALUE_KINDS and credential_name(n.name) and _secret_value(v):
        return PLACEHOLDER
    return redact_text(v)


def redact_node(n: Node) -> Node:
    """Redact doc, the sig of CONSTANT/FIELD, and attrs value/default; recursive."""
    return replace(
        n,
        doc=redact_text(n.doc),
        sig=redact_text(n.sig) if n.kind in _SIG_KINDS else n.sig,
        attrs=tuple((k, _attr(n, k, v)) for k, v in n.attrs),
        children=tuple(redact_node(c) for c in n.children),
    )
