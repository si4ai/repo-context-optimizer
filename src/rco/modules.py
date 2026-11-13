"""Path to module (tree-contract section 4; README "Module").

Detection order, all read from git at one SHA so the same commit always
gives the same modules:

1. The override file, `rco.toml` at the repo root: `[[module]]` tables
   with an `id` and a list of `paths` globs. The first rule whose glob
   matches a path owns it.
2. Language manifests: a directory with `package.json` (JS/TS files) or a
   `Cargo.toml` that has a `[package]` table (Rust files) is one module for
   the files of its language under it, down to the next such manifest.
   The manifest file itself belongs to it. Files of other languages there
   keep the directory grain, so a root `package.json` in a Python repo does
   not swallow the Python packages.
3. Directory fallback: the directory holding the file (Phase 0's package
   grain); files at the repo root map to ".".

Globs: `*` and `?` stay within one path segment, `**` spans segments
(`docs/**` matches every path under docs/). A bad override entry is
skipped with a note, never an error.
"""
from __future__ import annotations

import json
import re
import tomllib
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable, Mapping

from rco.model import Module

OVERRIDE_FILE = "rco.toml"
JS_EXT = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
MANIFESTS: dict[str, tuple[str, ...]] = {"package.json": JS_EXT, "Cargo.toml": (".rs",)}


def dir_of(path: str) -> str:
    """The directory holding the file; files at the repo root map to "."."""
    head, sep, _ = path.rpartition("/")
    return head if sep else "."


def glob_regex(glob: str) -> re.Pattern[str]:
    out, i = [], 0
    while i < len(glob):
        if glob.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif glob.startswith("**", i):
            out.append(".*")
            i += 2
        elif glob[i] == "*":
            out.append("[^/]*")
            i += 1
        elif glob[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(glob[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


@dataclass(frozen=True, slots=True)
class Rule:
    id: str
    globs: tuple[str, ...]


@dataclass(frozen=True)
class Layout:
    """How paths map to modules at one SHA. The default is the directory grain."""

    rules: tuple[Rule, ...] = ()
    roots: tuple[tuple[str, tuple[str, ...], str], ...] = ()  # (dir, exts, manifest name)
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "_rx", tuple(
            (r.id, tuple(glob_regex(g) for g in r.globs)) for r in self.rules))

    def module_of(self, path: str) -> str:
        for rid, rxs in self._rx:  # type: ignore[attr-defined]
            if any(rx.match(path) for rx in rxs):
                return rid
        best = None
        for root, exts, manifest in self.roots:
            inside = root == "." or path.startswith(root + "/")
            if not inside:
                continue
            own = path == (manifest if root == "." else f"{root}/{manifest}")
            if own or path.endswith(exts):
                if best is None or best == "." or (root != "." and len(root) > len(best)):
                    best = root
        return best if best is not None else dir_of(path)

    def group(self, paths: Iterable[str]) -> tuple[Module, ...]:
        by: dict[str, list[str]] = {}
        for p in paths:
            by.setdefault(self.module_of(p), []).append(p)
        return tuple(Module(id=m, files=tuple(sorted(fs))) for m, fs in sorted(by.items()))


DEFAULT = Layout()


def module_of(path: str, layout: Layout | None = None) -> str:
    return (layout or DEFAULT).module_of(path)


def group(paths: Iterable[str], layout: Layout | None = None) -> tuple[Module, ...]:
    return (layout or DEFAULT).group(paths)


def _rules(raw: bytes) -> tuple[tuple[Rule, ...], list[str]]:
    notes: list[str] = []
    try:
        doc = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        return (), [f"{OVERRIDE_FILE}: not valid TOML ({type(e).__name__}); override ignored"]
    items = doc.get("module", [])
    if not isinstance(items, list):
        return (), [f"{OVERRIDE_FILE}: [[module]] must be an array of tables; override ignored"]
    rules: list[Rule] = []
    for i, it in enumerate(items):
        mid = it.get("id") if isinstance(it, dict) else None
        paths = it.get("paths") if isinstance(it, dict) else None
        ok = isinstance(mid, str) and (mid == "." or (bool(mid.strip())
                                                      and mid == mid.strip().strip("/")))
        if not ok or not isinstance(paths, list) or not paths \
                or not all(isinstance(p, str) and p.strip() for p in paths):
            notes.append(f"{OVERRIDE_FILE}: module {i + 1} needs an id and a non-empty "
                         f"paths list of globs; skipped")
            continue
        rules.append(Rule(mid, tuple(p.strip().lstrip("/") for p in paths)))
    return tuple(rules), notes


def _is_root(name: str, raw: bytes) -> bool:
    try:
        text = raw.decode("utf-8-sig")
        if name == "package.json":
            return isinstance(json.loads(text), dict)
        return isinstance(tomllib.loads(text).get("package"), dict)
    except (ValueError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return False


def manifest_paths(paths: Iterable[str]) -> list[str]:
    """Paths whose bytes layout_from needs: the override file and every manifest."""
    return sorted(p for p in paths
                  if p == OVERRIDE_FILE or PurePosixPath(p).name in MANIFESTS)


def layout_from(files: Mapping[str, bytes]) -> Layout:
    """Layout from the override file and manifests (path -> bytes); pure."""
    rules: tuple[Rule, ...] = ()
    notes: list[str] = []
    if OVERRIDE_FILE in files:
        rules, notes = _rules(files[OVERRIDE_FILE])
    roots = []
    for p in sorted(files):
        name = PurePosixPath(p).name
        if name in MANIFESTS and _is_root(name, files[p]):
            roots.append((dir_of(p), MANIFESTS[name], name))
    return Layout(rules, tuple(roots), tuple(sorted(notes)))


def layout_at(repo, sha: str, entries=None) -> Layout:
    """Layout of a commit, read from git (never the working tree)."""
    from rco import gitread
    ents = entries if entries is not None else gitread.ls_tree(repo, sha)
    want = {e.path: e for e in ents
            if e.mode not in (gitread.MODE_SUBMODULE, gitread.MODE_SYMLINK)}
    paths = manifest_paths(want)
    data = gitread.read_blobs(repo, [want[p].blob for p in paths])
    return layout_from({p: data[want[p].blob] for p in paths})
