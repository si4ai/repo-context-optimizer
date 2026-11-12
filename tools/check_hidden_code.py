#!/usr/bin/env python3
"""Hidden-code report for a git diff.

Lists code a mapped test job may never exercise or scan. It is a visible
report that runs on every change, whatever paths it touches; it is not a
merge gate. Five sections, each a list of path:line with a reason:

  1 unmapped-exec   a changed file no test-select rule matches that can
                    run code (.py, .pth, .sh, .js, shebang, exec bit,
                    conftest.py, sitecustomize, setup.py, Makefile, ...)
  2 hook            import-time or build-time hooks in any path (.pth
                    imports, pytest plugins, entry points, build backend,
                    MANIFEST.in additions)
  3 obfuscation     added lines with exec/eval/decode calls, long base64 or
                    hex literals, chr() chains, and new non-text files
  4 control-plane   edits under the configured control-plane globs
                    (permissions, secrets, unpinned uses:,
                    pull_request_target) and edits to the test-select map,
                    marked when a path pattern is removed
  5 dependency      lock changes with no manifest change, unless the author
                    is dependabot[bot]; new index URLs, git+ or URL deps

The control-plane globs and the test-select map path come from flags or
from ci/hidden-code.toml (optional). Defaults: control-plane is
.github/**, and the map is ci/test-select.toml. Lock-to-manifest pairs
default to common packaging files and can be set in the same config.

Git only, no network. Standard library only.

  python3 tools/check_hidden_code.py --base REV [--head REV] [--json]
      [--author LOGIN] [--strict] [--root DIR] [--map PATH]
      [--control-plane GLOB] [--config PATH]

Exit 0 by default even with findings; --strict exits 1 when any are found.
A git failure exits 2.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tomllib
from dataclasses import asdict, dataclass, replace
from pathlib import Path

DEFAULT_MAP = "ci/test-select.toml"
DEFAULT_CONFIG_REL = "ci/hidden-code.toml"
DEFAULT_CONTROL_PLANE = (".github/**",)
DEFAULT_HOOK_PATHS = ("build_backend/**",)

SECTIONS = {
    1: "unmapped-exec",
    2: "hook",
    3: "obfuscation",
    4: "control-plane",
    5: "dependency",
}

DEPENDABOT_LOGINS = frozenset({"dependabot[bot]", "dependabot", "app/dependabot"})


def _is_dependabot(author: str | None) -> bool:
    a = (author or "").strip().lower()
    if a in {s.lower() for s in DEPENDABOT_LOGINS}:
        return True
    return a.endswith("/dependabot") or a.endswith("dependabot[bot]")


@dataclass(frozen=True)
class Finding:
    section: int
    path: str
    line: int  # 0 = the whole file
    reason: str

    @property
    def where(self) -> str:
        return f"{self.path}:{self.line}" if self.line else self.path


@dataclass
class Change:
    path: str
    status: str  # A, M, D
    old_mode: str
    new_mode: str
    added: list[tuple[int, str]]
    removed: list[str]
    binary: bool


@dataclass(frozen=True)
class LockRule:
    glob: str
    manifests: tuple[str, ...]


DEFAULT_LOCKS = (
    LockRule("**/package-lock.json", ("package.json",)),
    LockRule("**/yarn.lock", ("package.json",)),
    LockRule("**/pnpm-lock.yaml", ("package.json",)),
    LockRule("**/poetry.lock", ("pyproject.toml",)),
    LockRule("**/uv.lock", ("pyproject.toml",)),
    LockRule("**/Pipfile.lock", ("Pipfile",)),
    LockRule("**/requirements*.txt", ("pyproject.toml",)),
)


@dataclass(frozen=True)
class ScanConfig:
    map_rel: str = DEFAULT_MAP
    control_plane: tuple[str, ...] = DEFAULT_CONTROL_PLANE
    hook_paths: tuple[str, ...] = DEFAULT_HOOK_PATHS
    locks: tuple[LockRule, ...] = DEFAULT_LOCKS


# ---------------------------------------------------------------- matcher


def match_glob(path: str, pattern: str) -> bool:
    """Match a repo-relative path. ** is zero or more directories."""
    return _match(path.split("/"), pattern.split("/"))


def _match(path: list[str], pattern: list[str]) -> bool:
    if not pattern:
        return not path
    if pattern[0] == "**":
        if _match(path, pattern[1:]):
            return True
        return bool(path) and _match(path[1:], pattern)
    if not path or not _segment(path[0], pattern[0]):
        return False
    return _match(path[1:], pattern[1:])


def _segment(text: str, pattern: str) -> bool:
    i = 0
    j = 0
    star = -1
    mark = -1
    while i < len(text):
        if j < len(pattern) and (pattern[j] == "?" or pattern[j] == text[i]):
            i += 1
            j += 1
        elif j < len(pattern) and pattern[j] == "*":
            star = j
            mark = i
            j += 1
        elif star >= 0:
            mark += 1
            i = mark
            j = star + 1
        else:
            return False
    while j < len(pattern) and pattern[j] == "*":
        j += 1
    return j == len(pattern)


def _globs_from_toml(text: str) -> list[str]:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return []
    out: list[str] = []
    for rule in data.get("rule", []) or []:
        if isinstance(rule, dict):
            out.extend(p for p in rule.get("paths", []) or [] if isinstance(p, str))
    return out


def _is_text_name(rel: str) -> bool:
    name = rel.rsplit("/", 1)[-1].casefold()
    if name in {"copying", "dockerfile", "license", "makefile", "readme"}:
        return True
    return Path(name).suffix in {
        ".cfg", ".css", ".html", ".ini", ".js", ".json", ".md", ".mjs",
        ".ps1", ".py", ".sh", ".sql", ".svg", ".toml", ".txt", ".yaml", ".yml",
    }


def _config_from_toml(path: Path) -> ScanConfig:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise OSError(f"cannot read config: {exc}") from exc
    try:
        data = tomllib.loads(raw)
    except tomllib.TOMLDecodeError as exc:
        raise OSError(f"config is not valid toml: {exc}") from exc
    if not isinstance(data, dict):
        raise OSError("config must be a table")
    cfg = ScanConfig()
    updates: dict = {}
    if "map" in data:
        if not isinstance(data["map"], str) or not data["map"]:
            raise OSError("config map must be a path string")
        updates["map_rel"] = data["map"]
    if "control_plane" in data:
        items = data["control_plane"]
        if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
            raise OSError("config control_plane must be a list of globs")
        updates["control_plane"] = tuple(items)
    if "hook_paths" in data:
        items = data["hook_paths"]
        if not isinstance(items, list) or not all(isinstance(i, str) and i for i in items):
            raise OSError("config hook_paths must be a list of globs")
        updates["hook_paths"] = tuple(items)
    if "lock" in data:
        rules_raw = data["lock"]
        if not isinstance(rules_raw, list):
            raise OSError("config lock must be a list of tables")
        locks: list[LockRule] = []
        for rule in rules_raw:
            if not isinstance(rule, dict):
                raise OSError("config lock entry must be a table")
            glob = rule.get("glob")
            manifests = rule.get("manifests")
            if not isinstance(glob, str) or not glob:
                raise OSError("config lock.glob must be a glob string")
            if not isinstance(manifests, list) or not all(isinstance(m, str) and m for m in manifests):
                raise OSError("config lock.manifests must be a list of strings")
            locks.append(LockRule(glob, tuple(manifests)))
        updates["locks"] = tuple(locks)
    return replace(cfg, **updates) if updates else cfg


def load_config(
    root: Path,
    config_path: Path | None = None,
    map_rel: str | None = None,
    control_plane: list[str] | None = None,
) -> ScanConfig:
    """Defaults, then optional ci/hidden-code.toml or --config, then flags."""
    cfg = ScanConfig()
    path = config_path
    if path is None:
        candidate = root / DEFAULT_CONFIG_REL
        if candidate.is_file():
            path = candidate
    if path is not None:
        cfg = _config_from_toml(path)
    if map_rel:
        cfg = replace(cfg, map_rel=map_rel)
    if control_plane:
        cfg = replace(cfg, control_plane=tuple(control_plane))
    return cfg


# ------------------------------------------------------------------- git


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, capture_output=True, check=False, timeout=120
    )
    if proc.returncode != 0:
        raise OSError(proc.stderr.decode("utf-8", "replace").strip() or f"git {args[0]} failed")
    return proc.stdout.decode("utf-8", "surrogateescape")


def _git_bytes(root: Path, *args: str) -> bytes | None:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, check=False, timeout=120)
    return proc.stdout if proc.returncode == 0 else None


_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def read_changes(root: Path, base: str, head: str) -> list[Change]:
    """Parse `git diff base..head` into per-file added/removed lines."""
    raw = _git(root, "diff", "--no-renames", "--no-color", "--no-ext-diff", "-U0", f"{base}..{head}", "--")
    changes: list[Change] = []
    cur: Change | None = None
    lineno = 0
    for line in raw.split("\n"):
        if line.startswith("diff --git "):
            m = re.match(r'diff --git "?a/(.*?)"? "?b/(.*)"?$', line)
            path = m.group(2) if m else line.split(" b/", 1)[-1]
            cur = Change(path=path, status="M", old_mode="", new_mode="", added=[], removed=[], binary=False)
            changes.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode "):
            cur.status, cur.new_mode = "A", line.split()[-1]
        elif line.startswith("deleted file mode "):
            cur.status, cur.old_mode = "D", line.split()[-1]
        elif line.startswith("old mode "):
            cur.old_mode = line.split()[-1]
        elif line.startswith("new mode "):
            cur.new_mode = line.split()[-1]
        elif line.startswith("index ") and len(line.split()) == 3:
            cur.new_mode = cur.new_mode or line.split()[-1]
            cur.old_mode = cur.old_mode or line.split()[-1]
        elif line.startswith("Binary files "):
            cur.binary = True
        elif line.startswith("@@"):
            m = _HUNK.match(line)
            lineno = int(m.group(1)) if m else 0
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            cur.added.append((lineno, line[1:]))
            lineno += 1
        elif line.startswith("-"):
            cur.removed.append(line[1:])
    return changes


# --------------------------------------------------------------- section 1

_EXEC_SUFFIXES = {".py", ".pth", ".sh", ".bash", ".js", ".mjs", ".cjs", ".ps1", ".pyw"}
_EXEC_NAMES = {
    "conftest.py", "sitecustomize.py", "usercustomize.py", "setup.py", "setup.cfg",
    "makefile", "gnumakefile", "dockerfile",
}


def _exec_reason(root: Path, head: str, ch: Change) -> str | None:
    name = ch.path.rsplit("/", 1)[-1]
    low = name.casefold()
    if low in _EXEC_NAMES or low.startswith("dockerfile"):
        return f"runs code ({name})"
    suffix = Path(low).suffix
    if suffix in _EXEC_SUFFIXES:
        return f"runs code ({suffix})"
    if ch.new_mode == "100755":
        return "exec bit set"
    blob = _git_bytes(root, "show", f"{head}:{ch.path}")
    if blob and blob.startswith(b"#!"):
        return "shebang"
    return None


def section_unmapped(root: Path, head: str, changes: list[Change], cfg: ScanConfig) -> list[Finding]:
    text = _git_bytes(root, "show", f"{head}:{cfg.map_rel}")
    globs = _globs_from_toml(text.decode("utf-8", "replace")) if text else []
    out: list[Finding] = []
    for ch in changes:
        if ch.status == "D":
            continue
        if any(match_glob(ch.path, g) for g in globs):
            continue
        reason = _exec_reason(root, head, ch)
        if reason:
            out.append(Finding(1, ch.path, 0, f"no test-select rule; {reason}"))
    return out


# --------------------------------------------------------------- section 2

_PYPROJECT_HOOK = re.compile(
    r"^\s*(build-backend|backend-path|requires)\s*=|^\s*\[(project\.)?(entry-points|scripts|gui-scripts)",
)
_ENTRY = re.compile(r"pytest11|entry_points|entry-points|console_scripts")
_PLUGIN = re.compile(r"^\s*pytest_plugins\s*=|^\s*-p\s+\S|addopts\s*=.*-p\s")


def section_hooks(changes: list[Change], cfg: ScanConfig) -> list[Finding]:
    out: list[Finding] = []
    for ch in changes:
        p = ch.path
        name = p.rsplit("/", 1)[-1]
        if p.endswith(".pth"):
            for n, line in ch.added:
                if line.lstrip().startswith("import ") or line.lstrip().startswith("import\t"):
                    out.append(Finding(2, p, n, ".pth line runs at interpreter start"))
        if ch.status != "D" and any(match_glob(p, g) for g in cfg.hook_paths):
            out.append(Finding(2, p, 0, "build backend changed; runs at build time"))
        if name == "MANIFEST.in":
            for n, line in ch.added:
                if line.strip() and not line.lstrip().startswith("#"):
                    out.append(Finding(2, p, n, "MANIFEST.in adds files to the sdist"))
        if name == "pyproject.toml":
            for n, line in ch.added:
                if _PYPROJECT_HOOK.search(line):
                    out.append(Finding(2, p, n, "build backend or entry point changed"))
                elif _ENTRY.search(line):
                    out.append(Finding(2, p, n, "entry point added"))
        if name in ("setup.py", "setup.cfg"):
            for n, line in ch.added:
                if _ENTRY.search(line) or "cmdclass" in line:
                    out.append(Finding(2, p, n, "setup entry point or cmdclass"))
        if name in ("conftest.py", "pytest.ini", "tox.ini", "pyproject.toml", "setup.cfg"):
            for n, line in ch.added:
                if _PLUGIN.search(line):
                    out.append(Finding(2, p, n, "pytest plugin load"))
        if p.endswith(".py") and name != "conftest.py":
            for n, line in ch.added:
                if re.match(r"^\s*pytest_plugins\s*=", line):
                    out.append(Finding(2, p, n, "pytest plugin load"))
    return out


# --------------------------------------------------------------- section 3

_DYNAMIC = re.compile(
    r"(?<![\w.])(exec|eval|compile|__import__)\s*\("
    r"|\bmarshal\.loads?\s*\("
    r"|\b(c?pickle|dill)\.loads?\s*\("
    r"|\bzlib\.decompress\s*\("
    r"|\bbase64\.\w*decode\w*\s*\("
    r"|\bb(16|32|64|85)decode\s*\("
    r"|\bcodecs\.decode\s*\("
    r"|\bbytes\.fromhex\s*\("
)
_B64 = re.compile(r"[A-Za-z0-9+/]{120,}={0,2}")
_HEX = re.compile(r"(?:\\x[0-9a-fA-F]{2}){40,}|\b[0-9a-fA-F]{120,}\b")
_CHR_CHAIN = re.compile(r"(?:chr\s*\(\s*\d+\s*\)\s*\+\s*){3,}chr\s*\(")
# Lines in these files are prose or data; the dynamic-call pattern is skipped there.
_PROSE = (".md", ".rst", ".txt", ".html", ".css", ".svg", ".json", ".lock")


def _is_code_line(path: str) -> bool:
    return not path.endswith(_PROSE)


def section_obfuscation(root: Path, head: str, changes: list[Change]) -> list[Finding]:
    out: list[Finding] = []
    for ch in changes:
        if ch.status == "D":
            continue
        if ch.binary or not _is_text_name(ch.path):
            if ch.status == "A" or ch.binary:
                blob = _git_bytes(root, "show", f"{head}:{ch.path}") or b""
                if ch.binary or b"\0" in blob[:8192]:
                    digest = hashlib.sha256(blob).hexdigest()
                    out.append(Finding(3, ch.path, 0, f"new non-text file, {len(blob)} bytes, sha256 {digest}"))
                    continue
        code = _is_code_line(ch.path)
        for n, line in ch.added:
            if code:
                m = _DYNAMIC.search(line)
                if m and not line.lstrip().startswith("#"):
                    out.append(Finding(3, ch.path, n, f"dynamic code or decode: {m.group(0).strip()}"))
                    continue
                if _CHR_CHAIN.search(line):
                    out.append(Finding(3, ch.path, n, "chr() chain"))
                    continue
            if _B64.search(line) and not _looks_like_path_or_sha(line):
                out.append(Finding(3, ch.path, n, "base64-like literal of 120+ chars"))
            elif _HEX.search(line):
                out.append(Finding(3, ch.path, n, "hex literal of 120+ chars"))
    return out


def _looks_like_path_or_sha(line: str) -> bool:
    """A long run that is a slash path or an integrity hash is not an encoded blob."""
    for m in _B64.finditer(line):
        tok = m.group(0)
        if tok.count("/") >= 3 and not re.search(r"[+]", tok):
            continue
        pre = line[: m.start()]
        if re.search(r"sha(256|384|512)-$", pre):
            continue
        return False
    return True


# --------------------------------------------------------------- section 4

_SHA40 = re.compile(r"@[0-9a-f]{40}\b")
_USES = re.compile(r"^\s*-?\s*uses:\s*([^\s#]+)")


def _removed_or_narrowed(old: list[str], new: list[str]) -> list[str]:
    gone = []
    for g in old:
        if g in new:
            continue
        gone.append(g)
    return gone


def _in_control_plane(path: str, cfg: ScanConfig) -> bool:
    return any(match_glob(path, g) for g in cfg.control_plane)


def section_control(root: Path, base: str, head: str, changes: list[Change], cfg: ScanConfig) -> list[Finding]:
    out: list[Finding] = []
    for ch in changes:
        p = ch.path
        if _in_control_plane(p, cfg):
            for n, line in ch.added:
                s = line.strip()
                if s.startswith("permissions:") or re.match(r"^[a-z-]+:\s*(write|read|none)\b", s):
                    out.append(Finding(4, p, n, f"permission: {s}"))
                elif "secrets." in s:
                    out.append(Finding(4, p, n, "reads a secret"))
                elif "pull_request_target" in s:
                    out.append(Finding(4, p, n, "pull_request_target trigger"))
                else:
                    m = _USES.match(line)
                    if m:
                        ref = m.group(1)
                        if ref.startswith("./") or ref.startswith("docker://"):
                            out.append(Finding(4, p, n, f"uses local or docker action: {ref}"))
                        elif not _SHA40.search(ref):
                            out.append(Finding(4, p, n, f"uses not pinned to a 40-hex sha: {ref}"))
                        else:
                            action = ref.split("@", 1)[0]
                            old = _git_bytes(root, "show", f"{base}:{p}") or b""
                            if action.encode() not in old:
                                out.append(Finding(4, p, n, f"new action: {action}"))
            verb = {"A": "added", "D": "deleted"}.get(ch.status, "changed")
            out.append(Finding(4, p, 0, f"control-plane file {verb}"))
        if p == cfg.map_rel:
            old = _git_bytes(root, "show", f"{base}:{p}")
            new = _git_bytes(root, "show", f"{head}:{p}")
            og = _globs_from_toml(old.decode("utf-8", "replace")) if old else []
            ng = _globs_from_toml(new.decode("utf-8", "replace")) if new else []
            gone = _removed_or_narrowed(og, ng)
            if gone:
                for g in gone:
                    out.append(Finding(4, p, 0, f"test-select path pattern removed or narrowed: {g}"))
            else:
                out.append(Finding(4, p, 0, "test-select map changed (no pattern removed)"))
    return out


# --------------------------------------------------------------- section 5

_KNOWN_REGISTRIES = (
    "pythonhosted.org",
    "pypi.org",
    "registry.npmjs.org",
    "registry.yarnpkg.com",
    "npm.pkg.github.com",
)
_INDEX = re.compile(r"--(extra-)?index-url\b|--find-links\b|--trusted-host\b")

_DEP_NAMES = {
    "pyproject.toml", "setup.py", "setup.cfg", "package.json", "Pipfile",
    "Pipfile.lock", "poetry.lock", "uv.lock", "package-lock.json",
    "yarn.lock", "pnpm-lock.yaml",
}


def _manifest_changed(paths: set[str], lock: str, manifests: tuple[str, ...]) -> bool:
    lock_dir = lock.rsplit("/", 1)[0] if "/" in lock else ""
    for m in manifests:
        if m.endswith("/"):
            # a .in source next to a .txt lock, or any path under the prefix
            if lock.endswith(".txt") and lock[:-4] + ".in" in paths:
                return True
            if any(p.startswith(m) for p in paths if p != lock):
                return True
            continue
        if m in paths:
            return True
        # a bare filename is "the same directory as the lock"
        if "/" not in m:
            candidate = f"{lock_dir}/{m}" if lock_dir else m
            if candidate in paths:
                return True
    return False


def _dep_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if name in _DEP_NAMES or name.startswith("requirements"):
        return True
    if name.endswith(".in") and (name.startswith("requirements") or "lock" in path):
        return True
    return False


def _url_dep_reason(path: str, line: str) -> str | None:
    """Index flags, git+ VCS deps, and direct URL deps; not funding or registry pins."""
    if _INDEX.search(line):
        return "package index or find-links option"
    if re.search(r"\bgit\+", line):
        return "git+ or URL dependency"
    name = path.rsplit("/", 1)[-1]
    if name in ("package-lock.json", "package.json"):
        # funding / homepage / bugs URLs are not install sources
        if '"resolved"' not in line:
            return None
        if any(reg in line for reg in _KNOWN_REGISTRIES):
            return None
        return "git+ or URL dependency"
    if re.search(r"@\s*(https?|file)://|^\s*(https?|file)://", line):
        if any(reg in line for reg in _KNOWN_REGISTRIES):
            return None
        return "git+ or URL dependency"
    return None


def section_dependency(changes: list[Change], author: str | None, cfg: ScanConfig) -> list[Finding]:
    out: list[Finding] = []
    paths = {c.path for c in changes}
    bot = _is_dependabot(author)
    for ch in changes:
        for rule in cfg.locks:
            if match_glob(ch.path, rule.glob) and not bot and not _manifest_changed(paths, ch.path, rule.manifests):
                out.append(Finding(5, ch.path, 0, "lock changed with no matching manifest change"))
        if _dep_file(ch.path) and not bot:
            for n, line in ch.added:
                reason = _url_dep_reason(ch.path, line)
                if reason:
                    out.append(Finding(5, ch.path, n, reason))
    return out


# ------------------------------------------------------------------ main


def scan(
    root: Path,
    base: str,
    head: str,
    author: str | None = None,
    cfg: ScanConfig | None = None,
) -> list[Finding]:
    if cfg is None:
        cfg = load_config(root)
    changes = read_changes(root, base, head)
    findings = (
        section_unmapped(root, head, changes, cfg)
        + section_hooks(changes, cfg)
        + section_obfuscation(root, head, changes)
        + section_control(root, base, head, changes, cfg)
        + section_dependency(changes, author, cfg)
    )
    seen: set[Finding] = set()
    uniq = []
    for f in findings:
        if f not in seen:
            seen.add(f)
            uniq.append(f)
    uniq.sort(key=lambda f: (f.section, f.path, f.line, f.reason))
    return uniq


def to_markdown(findings: list[Finding], base: str, head: str) -> str:
    lines = [f"hidden-code report for {base[:12]}..{head[:12]}", ""]
    counts = {s: 0 for s in SECTIONS}
    for f in findings:
        counts[f.section] += 1
    lines.append("| section | findings |")
    lines.append("|---|---|")
    for s, name in SECTIONS.items():
        lines.append(f"| {s} {name} | {counts[s]} |")
    lines.append("")
    if not findings:
        lines.append("No findings.")
        return "\n".join(lines) + "\n"
    lines.append("| section | where | reason |")
    lines.append("|---|---|---|")
    for f in findings:
        reason = f.reason.replace("|", "\\|")
        lines.append(f"| {f.section} {SECTIONS[f.section]} | `{f.where}` | {reason} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="report hidden code in a PR diff")
    ap.add_argument("--base", required=True, help="diff base revision")
    ap.add_argument("--head", default="HEAD", help="diff head revision")
    ap.add_argument("--root", type=Path, default=Path.cwd(), help="git work tree to scan")
    ap.add_argument("--author", default=None, help="PR author login (dependabot[bot] skips the lock check)")
    ap.add_argument("--map", default=None, help="test-select map path, default ci/test-select.toml")
    ap.add_argument(
        "--control-plane",
        action="append",
        default=None,
        dest="control_plane",
        help="control-plane glob (repeatable); default .github/**",
    )
    ap.add_argument(
        "--config",
        type=Path,
        default=None,
        help="toml config (map, control_plane, hook_paths, [[lock]]); default ci/hidden-code.toml if present",
    )
    ap.add_argument("--json", action="store_true", help="print JSON instead of markdown")
    ap.add_argument("--strict", action="store_true", help="exit 1 when there are findings")
    args = ap.parse_args(argv)
    try:
        root = args.root
        cfg = load_config(root, args.config, args.map, args.control_plane)
        base = _git(root, "rev-parse", "--verify", f"{args.base}^{{commit}}").strip()
        head = _git(root, "rev-parse", "--verify", f"{args.head}^{{commit}}").strip()
        findings = scan(root, base, head, args.author, cfg)
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check_hidden_code: {exc}", file=sys.stderr)
        return 2
    if args.json:
        payload = {
            "base": base,
            "head": head,
            "sections": SECTIONS,
            "findings": [dict(asdict(f), where=f.where, name=SECTIONS[f.section]) for f in findings],
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        sys.stdout.write(to_markdown(findings, base, head))
    return 1 if (args.strict and findings) else 0


if __name__ == "__main__":
    sys.exit(main())
