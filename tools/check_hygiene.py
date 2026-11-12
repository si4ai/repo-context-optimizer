#!/usr/bin/env python3
"""Hygiene report for a git commit range, head message, and PR text.

flags personal identifiers, secrets, home paths, non-documentation
addresses, emails outside placeholder domains, title-case name tokens
that are not ordinary words, typographic marks, byte-order marks, and
junk files. It scans added diff lines, added paths (including a
byte-order-mark prefix check on those blobs), each commit message in
the range, and, when an event file is given, the pull-request title
and body. --staged scans index blobs the same way.

Findings name the kind and the path or commit, never the matched text.
This is a visible report, not a merge gate.

Options come from flags or from ci/hygiene.toml (optional). There are
no product-specific defaults.

git only, no network. standard library only.

  python3 tools/check_hygiene.py --base REV [--head REV] [--root DIR]
      [--event PATH] [--config PATH] [--words-file PATH] [--json] [--strict]
  python3 tools/check_hygiene.py --staged [--root DIR] [--strict]

exits 0 by default even with findings; --strict exits 1 when any are found.
a git failure or bad config exits 2.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_CONFIG_REL = "ci/hygiene.toml"
DEFAULT_WORDS_REL = "tools/hygiene_words.txt"
DEFAULT_ROLES = ("auditor", "builder", "validator", "desk")

EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+-])([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})"
)
_DOC_V4 = (
    ipaddress.ip_network("192.0.2.0/24"),
    ipaddress.ip_network("198.51.100.0/24"),
    ipaddress.ip_network("203.0.113.0/24"),
)
_DOC_V6 = ipaddress.ip_network("2001:db8::/32")
_IPV4_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{1,3}){3})(?!\d)")
_IPV6_RE = re.compile(
    r"(?<![0-9A-Za-z])((?:[0-9A-Fa-f]{0,4}:){2,7}(?:\d{1,3}(?:\.\d{1,3}){3}|[0-9A-Fa-f]{1,4}))(?![0-9A-Za-z:])"
)
_TEXT_MARKS = frozenset(
    {
        0x00A0,
        0x00B1,
        0x00B7,
        0x0394,
        0x200B,
        0x200C,
        0x200D,
        0x2013,
        0x2014,
        0x2018,
        0x2019,
        0x201C,
        0x201D,
        0x2026,
        0x2190,
        0x2191,
        0x2192,
        0x2193,
        0x2264,
        0x2713,
        0xFEFF,
    }
)
_BOM_PREFIXES = (b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")
_HOME_PLACEHOLDERS = frozenset(
    {"", "*", "...", "\u2026", "example", "user", "username", "you", "name"}
)
# WSL forms: a wsl$ or wsl.localhost UNC share (backslash or forward-slash
# form; the segment checked is the distro), and Windows user folders seen
# from WSL as /mnt/<drive>/Users/<login>.
_HOME_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z0-9])[A-Za-z]:(?:\\\\|\\|/)+[Uu][Ss][Ee][Rr][Ss](?:\\\\|\\|/)+"
    r"|(?<![\w.~-])/(?:home|Users)/"
    r"|(?<![\w.~-])/mnt/[A-Za-z]/[Uu][Ss][Ee][Rr][Ss]/"
    r"|(?<![\w$.~-])(?:\\\\|\\|/){2,}[Ww][Ss][Ll](?:\$|\.[Ll][Oo][Cc][Aa][Ll][Hh][Oo][Ss][Tt])(?:\\\\|\\|/)+)"
    r"([^\\/\s\"'`<>()\[\]{}:;,|]*)"
)
_PLACEHOLDER_EMAIL_DOMAINS = frozenset(
    {
        "example.com",
        "example.org",
        "example.net",
        "users.noreply.gitlab.com",
        "noreply.gitlab.com",
        "users.noreply.github.com",
    }
)
_PLACEHOLDER_EMAIL_TLDS = frozenset({"example", "test", "invalid", "localhost"})
_ASSET_TLDS = frozenset(
    {
        "png",
        "jpg",
        "jpeg",
        "gif",
        "svg",
        "webp",
        "ico",
        "js",
        "mjs",
        "css",
        "py",
        "md",
        "txt",
        "json",
        "whl",
        "gz",
    }
)
_NAME_TOKEN_RE = re.compile(r"\b[A-Z][a-z]{2,20}\b")
_LINE_START_RE = re.compile(
    r"^[ \t]*(?:(?:[-*+>]|\d+[.)]|#\d+:?)[ \t]+)*([A-Z][a-z]{2,20})\b",
    re.MULTILINE,
)
_SENTENCE_START_RE = re.compile(
    r"(?<=[.!?])[\"')\]]*[ \t]+([A-Z][a-z]{2,20})\b"
)
_LOWER_WORD_RE = re.compile(r"\b[a-z]{3,21}\b")
_PEM_BEGIN_RE = re.compile(r"-----BEGIN [A-Z0-9 ]{0,40}PRIVATE KEY-----")
_AWS_ID_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Za-z0-9_-])"
)
_AWS_SECRET_RE = re.compile(
    r"(?<![A-Za-z0-9/+=])[A-Za-z0-9/+=]{40}(?![A-Za-z0-9/+=])"
)
_GITHUB_PAT_RE = re.compile(
    r"(?<![A-Za-z0-9_-])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})(?![A-Za-z0-9_-])"
)
_PYPI_RE = re.compile(
    r"(?<![A-Za-z0-9_-])pypi-AgE[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_SLACK_RE = re.compile(
    r"(?<![A-Za-z0-9_-])xox[abprs]-[A-Za-z0-9-]{10,}(?![A-Za-z0-9_-])"
)
_SK_RE = re.compile(
    r"(?<![A-Za-z0-9_-])sk-(?:proj-|ant-[a-z0-9]{2,8}-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"
)
_ASSIGN_RE = re.compile(
    r"(?i)(?:(?:const|let|var|export)\s+)?"
    r"[\"']?"
    r"(?:[A-Za-z_][A-Za-z0-9_]*)?"
    r"(?:KEY|TOKEN|SECRET)"
    r"[\"']?"
    r"\s*[=:]\s*"
    r"[\"']?"
    r"([A-Za-z0-9/_+=\-]{20,})"
    r"[\"']?"
)
_KEBAB_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)+$")
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")


@dataclass(frozen=True)
class Finding:
    kind: str
    where: str


@dataclass(frozen=True)
class HygieneConfig:
    roles: tuple[str, ...] = DEFAULT_ROLES
    words_file: str = DEFAULT_WORDS_REL
    extra_words: tuple[str, ...] = ()


def _ipv4_allowed(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return True
    if not isinstance(ip, ipaddress.IPv4Address):
        return True
    if ip.is_loopback or ip.is_unspecified:
        return True
    return any(ip in net for net in _DOC_V4)


def _ipv6_allowed(token: str) -> bool:
    if "::" not in token and not any(c in "abcdefABCDEF" for c in token):
        return True
    try:
        ip = ipaddress.ip_address(token)
    except ValueError:
        return True
    if not isinstance(ip, ipaddress.IPv6Address):
        return True
    if ip.is_loopback or ip.is_unspecified:
        return True
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None and (mapped.is_loopback or mapped.is_unspecified):
        return True
    return bool(ip in _DOC_V6)


def text_has_forbidden_address(text: str) -> bool:
    if not text:
        return False
    for match in _IPV4_RE.finditer(text):
        if not _ipv4_allowed(match.group(1)):
            return True
    for match in _IPV6_RE.finditer(text):
        if not _ipv6_allowed(match.group(1)):
            return True
    return False


def home_segment_is_placeholder(segment: str) -> bool:
    seg = segment.strip().casefold()
    if seg in _HOME_PLACEHOLDERS:
        return True
    return seg[:1] in ("$", "%", "<", "{", "*", ".")


def text_has_personal_home_path(text: str) -> bool:
    if not text:
        return False
    for match in _HOME_PATH_RE.finditer(text):
        if not home_segment_is_placeholder(match.group(1)):
            return True
    return False


def email_is_placeholder(addr: str) -> bool:
    local, _, domain = addr.rpartition("@")
    domain = domain.casefold().rstrip(".")
    if local == "git":
        return True
    if domain in _PLACEHOLDER_EMAIL_DOMAINS:
        return True
    if any(domain.endswith("." + d) for d in _PLACEHOLDER_EMAIL_DOMAINS):
        return True
    tld = domain.rsplit(".", 1)[-1]
    return tld in _PLACEHOLDER_EMAIL_TLDS or tld in _ASSET_TLDS


def text_has_personal_email(text: str) -> bool:
    if not text:
        return False
    return any(not email_is_placeholder(em) for em in EMAIL_RE.findall(text))


def text_mark(text: str) -> int | None:
    if not text:
        return None
    for ch in text:
        point = ord(ch)
        if point in _TEXT_MARKS:
            return point
    return None


def blob_has_bom(data: bytes) -> bool:
    return bool(data) and data.startswith(_BOM_PREFIXES)


def _is_bak(name: str) -> bool:
    base = name.rsplit("/", 1)[-1]
    if base.endswith(".bak") or base == ".bak":
        return True
    if ".bak-" in base or base.startswith(".bak-"):
        return True
    return False


def junk_kind(path: str) -> str | None:
    parts = path.replace("\\", "/").split("/")
    for part in parts:
        if part == "__pycache__" or part.endswith(".pyc") or part == ".pyc":
            return "bytecode/cache"
        if part in {".DS_Store"} or part.casefold() == "thumbs.db":
            return "os-junk"
        if part.endswith("~") or part.endswith(".swp") or part.endswith(".orig"):
            return "backup"
        if _is_bak(part):
            return "backup"
    return None


def _high_entropy(val: str) -> bool:
    if len(val) < 20:
        return False
    if _KEBAB_RE.fullmatch(val):
        return False
    if len(val) in (40, 64) and _HEX_RE.fullmatch(val):
        return True
    classes = sum(
        (
            any(c.islower() for c in val),
            any(c.isupper() for c in val),
            any(c.isdigit() for c in val),
            any(c in "/+_=" for c in val),
        )
    )
    return classes >= 3


def credential_kinds(text: str) -> list[str]:
    if not text:
        return []
    kinds: list[str] = []

    def add(kind: str) -> None:
        if kind not in kinds:
            kinds.append(kind)

    if _PEM_BEGIN_RE.search(text):
        add("pem-private-key")
    if _AWS_ID_RE.search(text):
        add("aws-access-key-id")
    if _GITHUB_PAT_RE.search(text):
        add("github-pat")
    if _PYPI_RE.search(text):
        add("pypi-token")
    if _SLACK_RE.search(text):
        add("slack-token")
    if _SK_RE.search(text):
        add("sk-api-key")
    for match in _ASSIGN_RE.finditer(text):
        if _high_entropy(match.group(1)):
            add("high-entropy-secret-assign")
            break
    for line in text.splitlines():
        if _AWS_ID_RE.search(line) or line.rstrip().endswith("EXAMPLEKEY"):
            for match in _AWS_SECRET_RE.finditer(line):
                if not _AWS_ID_RE.fullmatch(match.group(0)):
                    add("aws-secret-access-key")
                    break
    return kinds


def load_word_file(path: Path) -> set[str]:
    words: set[str] = set()
    try:
        for ln in path.read_text(encoding="utf-8").splitlines():
            ln = ln.split("#", 1)[0].strip()
            if ln:
                words.add(ln.casefold())
    except OSError:
        pass
    return words


def ordinary_words(
    root: Path, words_file: Path, extra: tuple[str, ...] = ()
) -> tuple[set[str], set[str]]:
    """Return (allow, vocab).

    allow is the words file plus config extras, used for mid-line tokens.
    vocab is allow plus lower-case words in the tree, used at line and
    sentence starts.
    """
    allow = load_word_file(words_file)
    allow.update(w.casefold() for w in extra if w)
    vocab = set(allow)
    try:
        raw = subprocess.check_output(
            ["git", "grep", "-h", "-o", "-w", "-I", "-E", "[a-z]{3,21}", "HEAD", "--", "."],
            cwd=root,
            stderr=subprocess.DEVNULL,
        )
        vocab.update(_LOWER_WORD_RE.findall(raw.decode("utf-8", "replace")))
    except (OSError, subprocess.CalledProcessError):
        pass
    return allow, vocab


def text_has_personal_name_token(text: str, allow: set[str], vocab: set[str]) -> bool:
    if not text:
        return False
    starts = {
        m.start(1)
        for rx in (_LINE_START_RE, _SENTENCE_START_RE)
        for m in rx.finditer(text)
        if m.group(1).casefold() in vocab
    }
    for match in _NAME_TOKEN_RE.finditer(text):
        if match.start() in starts:
            continue
        if match.group(0).casefold() not in allow:
            return True
    return False


def kinds_in_text(text: str, allow: set[str], vocab: set[str]) -> list[str]:
    out: list[str] = []
    if text_has_forbidden_address(text):
        out.append("non-documentation address")
    if text_has_personal_home_path(text):
        out.append("home path with a login name")
    if text_has_personal_email(text):
        out.append("email outside placeholder domains")
    for cred in credential_kinds(text):
        out.append(f"credential shape ({cred})")
    if text_has_personal_name_token(text, allow, vocab):
        out.append("personal name token")
    mark = text_mark(text)
    if mark is not None:
        out.append(f"typographic mark U+{mark:04X}")
    return out


def load_hygiene_config(
    root: Path,
    config_path: Path | None = None,
    *,
    roles: list[str] | None = None,
    words_file: str | None = None,
) -> HygieneConfig:
    cfg = HygieneConfig()
    path = config_path if config_path is not None else root / DEFAULT_CONFIG_REL
    if path.is_file():
        try:
            raw = path.read_text(encoding="utf-8")
            data = tomllib.loads(raw)
        except tomllib.TOMLDecodeError as exc:
            raise OSError(f"config is not valid toml: {exc}") from exc
        except OSError as exc:
            raise OSError(f"cannot read config {path}: {exc}") from exc
        file_roles = data.get("roles")
        if file_roles is not None:
            if not isinstance(file_roles, list) or not all(
                isinstance(item, str) and item.strip() for item in file_roles
            ):
                raise OSError("config roles must be a list of strings")
            cfg = HygieneConfig(
                roles=tuple(item.strip() for item in file_roles),
                words_file=cfg.words_file,
                extra_words=cfg.extra_words,
            )
        file_words = data.get("words_file")
        if file_words is not None:
            if not isinstance(file_words, str) or not file_words.strip():
                raise OSError("config words_file must be a string")
            cfg = HygieneConfig(
                roles=cfg.roles,
                words_file=file_words.strip(),
                extra_words=cfg.extra_words,
            )
        extra = data.get("words")
        if extra is not None:
            if not isinstance(extra, list) or not all(isinstance(item, str) for item in extra):
                raise OSError("config words must be a list of strings")
            cfg = HygieneConfig(
                roles=cfg.roles,
                words_file=cfg.words_file,
                extra_words=tuple(item.strip() for item in extra if item.strip()),
            )
    if roles:
        cfg = HygieneConfig(
            roles=tuple(item.strip() for item in roles if item.strip()),
            words_file=cfg.words_file,
            extra_words=cfg.extra_words,
        )
    if words_file:
        cfg = HygieneConfig(
            roles=cfg.roles,
            words_file=words_file,
            extra_words=cfg.extra_words,
        )
    return cfg


def _git(root: Path, *args: str) -> bytes:
    try:
        return subprocess.check_output(
            ["git", *args], cwd=root, stderr=subprocess.PIPE
        )
    except subprocess.CalledProcessError as exc:
        err = exc.stderr.decode("utf-8", "replace").strip() if exc.stderr else ""
        raise OSError(err or f"git {' '.join(args)} failed") from exc
    except OSError as exc:
        raise OSError(f"git failed: {exc}") from exc


def _rev_list(root: Path, base: str, head: str) -> list[str]:
    raw = _git(root, "rev-list", "--reverse", f"{base}..{head}")
    return [ln.strip() for ln in raw.decode().splitlines() if ln.strip()]


def _commit_meta(root: Path, rev: str) -> tuple[str, str, str]:
    raw = _git(root, "log", "-1", "--format=%an%x1f%ae%x1e%B", rev)
    head, _, body = raw.decode("utf-8", "replace").partition("\x1e")
    name, _, email = head.strip().partition("\x1f")
    return name, email, body


@dataclass(frozen=True)
class _RawEntry:
    path: str
    mode: str
    oid: str


def _entries_from_raw_z(raw: bytes) -> list[_RawEntry]:
    """New-side paths from `git diff-tree` / `git diff` `--raw -z` output."""
    entries: list[_RawEntry] = []
    parts = raw.split(b"\0")
    i = 0
    n = len(parts)
    while i < n:
        meta = parts[i]
        i += 1
        if not meta.startswith(b":"):
            continue
        fields = meta.decode("ascii", "replace").split()
        if len(fields) < 5:
            continue
        new_mode = fields[1]
        new_oid = fields[3]
        status = fields[4]
        if i >= n:
            break
        path = parts[i].decode("utf-8", "replace")
        i += 1
        if status[:1] in ("R", "C"):
            if i >= n:
                break
            path = parts[i].decode("utf-8", "replace")
            i += 1
        if path:
            entries.append(_RawEntry(path=path, mode=new_mode, oid=new_oid))
    return entries


def _commit_added(root: Path, rev: str) -> tuple[list[_RawEntry], str]:
    names = _git(
        root,
        "diff-tree",
        "--no-commit-id",
        "--no-renames",
        "-r",
        "--diff-filter=ACM",
        "--raw",
        "-z",
        rev,
    )
    paths = _entries_from_raw_z(names)
    raw = _git(root, "show", "--format=", "--unified=0", "--no-renames", rev)
    added_lines = [
        ln[1:]
        for ln in raw.decode("utf-8", "replace").splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    return paths, "\n".join(added_lines)


def _scan_text(where: str, text: str, allow: set[str], vocab: set[str]) -> list[Finding]:
    return [Finding(kind=kind, where=where) for kind in kinds_in_text(text, allow, vocab)]


def _word_sets(root: Path, cfg: HygieneConfig) -> tuple[set[str], set[str]]:
    words_path = Path(cfg.words_file)
    if not words_path.is_absolute():
        words_path = root / words_path
    return ordinary_words(root, words_path, cfg.extra_words)


def scan_range(root: Path, base: str, head: str, cfg: HygieneConfig) -> list[Finding]:
    allow, vocab = _word_sets(root, cfg)
    findings: list[Finding] = []
    for rev in _rev_list(root, base, head):
        short = rev[:12]
        _name, _email, body = _commit_meta(root, rev)
        findings.extend(_scan_text(f"git commit {short}", body, allow, vocab))
        paths, added = _commit_added(root, rev)
        for entry in paths:
            kind = junk_kind(entry.path)
            if kind:
                findings.append(Finding(kind=f"junk file ({kind})", where=entry.path))
            if not entry.mode.startswith("100"):
                continue
            try:
                blob = _git(root, "cat-file", "blob", entry.oid)
            except OSError as exc:
                raise OSError(f"cannot read blob {entry.oid}: {exc}") from exc
            if blob_has_bom(blob):
                findings.append(Finding(kind="byte-order mark", where=entry.path))
        findings.extend(_scan_text(f"git commit {short} diff", added, allow, vocab))
    return findings


def _staged_paths(root: Path) -> list[_RawEntry]:
    raw = _git(root, "diff", "--cached", "--diff-filter=ACM", "--raw", "-z")
    return _entries_from_raw_z(raw)


def _staged_added_text(root: Path) -> str:
    raw = _git(root, "diff", "--cached", "--unified=0", "--no-renames")
    added = [
        ln[1:]
        for ln in raw.decode("utf-8", "replace").splitlines()
        if ln.startswith("+") and not ln.startswith("+++")
    ]
    return "\n".join(added)


def scan_staged(root: Path, cfg: HygieneConfig) -> list[Finding]:
    """BOM and typographic marks on each staged blob; other rules on added lines.

    Name-token and identifier rules follow the range scan: they look at
    added diff lines, not unchanged prose already on the branch. A
    byte-order mark or typographic mark on an unchanged line of a
    staged file still fails, because those are read from the blob.
    """
    allow, vocab = _word_sets(root, cfg)
    findings: list[Finding] = []
    for entry in _staged_paths(root):
        kind = junk_kind(entry.path)
        if kind:
            findings.append(Finding(kind=f"junk file ({kind})", where=entry.path))
        if not entry.mode.startswith("100"):
            continue
        try:
            blob = _git(root, "cat-file", "blob", entry.oid)
        except OSError as exc:
            raise OSError(f"cannot read staged blob {entry.oid}: {exc}") from exc
        if blob_has_bom(blob):
            findings.append(Finding(kind="byte-order mark", where=entry.path))
        if b"\0" in blob[:8000]:
            continue
        mark = text_mark(blob.decode("utf-8", "replace"))
        if mark is not None:
            findings.append(Finding(kind=f"typographic mark U+{mark:04X}", where=entry.path))
    added = _staged_added_text(root)
    for kind in kinds_in_text(added, allow, vocab):
        if kind.startswith("typographic mark"):
            continue
        findings.append(Finding(kind=kind, where="staged diff"))
    return findings


def scan_event(event_path: Path, allow: set[str], vocab: set[str]) -> list[Finding]:
    try:
        event = json.loads(event_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise OSError(f"cannot read event file ({type(exc).__name__})") from exc
    pr = event.get("pull_request") or {}
    if not pr:
        return []
    findings: list[Finding] = []
    title = str(pr.get("title") or "")
    body = str(pr.get("body") or "").replace("\r\n", "\n")
    findings.extend(_scan_text("PR title", title, allow, vocab))
    findings.extend(_scan_text("PR body", body, allow, vocab))
    return findings


def to_markdown(findings: list[Finding], base: str, head: str) -> str:
    lines = [f"hygiene report: {base[:12]}..{head[:12]}", ""]
    if not findings:
        lines.append("no findings.")
        lines.append("")
        return "\n".join(lines)
    lines.append(f"{len(findings)} finding(s):")
    for item in findings:
        lines.append(f"- {item.kind} in {item.where}")
    lines.append("")
    lines.append("the report names the kind and the path or commit, never the matched text.")
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="report hygiene findings in a PR or push range")
    ap.add_argument(
        "--base",
        default=None,
        help="diff base revision (required unless --staged)",
    )
    ap.add_argument("--head", default="HEAD", help="diff head revision")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="git work tree to scan")
    ap.add_argument(
        "--event",
        type=Path,
        default=None,
        help="GitHub event json (PR title and body); default none",
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="toml config (roles, words_file, words); default ci/hygiene.toml if present",
    )
    ap.add_argument("--words-file", default=None, help="ordinary-words file, default tools/hygiene_words.txt")
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--strict", action="store_true", help="exit 1 when there are findings")
    ap.add_argument(
        "--staged",
        action="store_true",
        help="scan staged index blobs instead of a commit range",
    )
    args = ap.parse_args(argv)
    if not args.staged and not args.base:
        ap.error("--base is required unless --staged")
    try:
        root = args.root
        cfg = load_hygiene_config(root, args.config, words_file=args.words_file)
        if args.staged:
            base, head = "staged", "index"
            findings = scan_staged(root, cfg)
        else:
            base = _git(root, "rev-parse", "--verify", f"{args.base}^{{commit}}").decode().strip()
            head = _git(root, "rev-parse", "--verify", f"{args.head}^{{commit}}").decode().strip()
            findings = scan_range(root, base, head, cfg)
        if args.event is not None:
            allow, vocab = _word_sets(root, cfg)
            findings.extend(scan_event(args.event, allow, vocab))
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check_hygiene: {exc}", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "base": base,
            "head": head,
            "findings": [asdict(item) for item in findings],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(to_markdown(findings, base, head))
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
