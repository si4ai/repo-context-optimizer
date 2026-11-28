"""Shared with/without file and token counts (cards 1351 and 1359).

`without` is the changed files, plus the in-repo files they import (one hop),
plus the in-repo files that import a changed file whose public signature
changed (card 1359). `with` is the pack plus the changed files (the files
the agent still opens). A skipped pack (nothing to add) is a tie: with
equals without.

The files counted are defined by `read_set`. `metrics.measure` and
`rco replay` both use it, including the no-loss / call-site coverage
check. The pack itself does not yet add importer call sites (0.1.9).
"""
from __future__ import annotations

import re
from typing import Iterable, Mapping, Sequence


def arms(*, changed_tokens: int, imported_tokens: int, pack_tokens: int,
         skipped: bool, n_changed: int, n_imported: int,
         importer_tokens: int = 0, n_importers: int = 0) -> tuple[dict, dict]:
    """Return (without, with) file and token counts under the shared definition."""
    without = {"files": n_changed + n_imported + n_importers,
               "tokens": changed_tokens + imported_tokens + importer_tokens}
    if skipped:
        with_ = {"files": without["files"], "tokens": without["tokens"],
                 "pack_tokens": 0, "pack_skipped": True,
                 "changed_tokens": changed_tokens}
        return without, with_
    return without, {"files": n_changed + 1,
                     "tokens": pack_tokens + changed_tokens,
                     "pack_tokens": pack_tokens, "pack_skipped": False,
                     "changed_tokens": changed_tokens}


def pack_is_skipped(pack_text: str) -> bool:
    """True when there is no pack, or the pack is a nothing-to-add note."""
    if not pack_text.strip():
        return True
    from rco.pack import NOOP_NOTES
    return any(n in pack_text for n in NOOP_NOTES)


def merge_graphs(*graphs: Mapping[str, Sequence[str]] | None) -> dict[str, tuple[str, ...]]:
    """The union of forward import edges from one or more file graphs."""
    out: dict[str, set[str]] = {}
    for g in graphs:
        if not g:
            continue
        for k, vs in g.items():
            out.setdefault(k, set()).update(vs)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def reverse_graph(graph: Mapping[str, Sequence[str]]) -> dict[str, tuple[str, ...]]:
    """path -> files that import it (one hop)."""
    out: dict[str, set[str]] = {}
    for src, dests in graph.items():
        for d in dests:
            out.setdefault(d, set()).add(src)
    return {k: tuple(sorted(v)) for k, v in out.items()}


def public_contract(path: str, data: bytes | None) -> frozenset[tuple]:
    """Public names and their contract shapes in one file. Empty when there is no file."""
    if not data:
        return frozenset()
    from rco import build
    from rco.extract.python import is_public
    from rco.model import Kind, SourceFile

    containers = {Kind.FILE, Kind.PYMODULE, Kind.SRCMODULE}
    public_kinds = {
        Kind.CLASS, Kind.FUNCTION, Kind.METHOD, Kind.FIELD, Kind.CONSTANT,
        Kind.EXPORT, Kind.COMMAND, Kind.FLAG, Kind.TABLE, Kind.VIEW,
        Kind.COLUMN, Kind.INDEX, Kind.PROCEDURE, Kind.ROUTE,
    }
    out: set[tuple] = set()

    def rec(n, parent: str) -> None:
        qn = f"{parent}.{n.name}" if parent and n.kind not in containers else n.name
        if n.kind in public_kinds and is_public(n.name):
            sig, attrs = n.shape()
            out.add((n.kind.value, qn, sig, attrs))
        child_parent = "" if n.kind in containers else qn
        for c in n.children:
            rec(c, child_parent)

    src = SourceFile(path, "", data)
    for n in build.nodes_for(src):
        rec(n, "")
    return frozenset(out)


def interface_changed(path: str, base_data: bytes | None, head_data: bytes | None) -> bool:
    """True when a file's public signatures differ between base and head."""
    return public_contract(path, base_data) != public_contract(path, head_data)


def changed_names(path: str, base_data: bytes | None, head_data: bytes | None) -> frozenset[str]:
    """Public qualnames (and their last component) whose contract changed."""
    before = {(k, q): (s, a) for k, q, s, a in public_contract(path, base_data)}
    after = {(k, q): (s, a) for k, q, s, a in public_contract(path, head_data)}
    names: set[str] = set()
    for key in set(before) | set(after):
        if before.get(key) != after.get(key):
            qn = key[1]
            names.add(qn)
            tail = qn.replace(" ", ".").rsplit(".", 1)[-1]
            if tail:
                names.add(tail)
    return frozenset(names)


def read_set(changed: Iterable[str], graph: Mapping[str, Sequence[str]],
             interface_changed_paths: Iterable[str] = (),
             importer_graph: Mapping[str, Sequence[str]] | None = None
             ) -> tuple[list[str], list[str], list[str]]:
    """Changed files, forward imports (one hop), and importers of interface changes.

    The three lists are disjoint and stable. `importer_graph` defaults to
    `graph`; pass a union of base and head graphs so a deleted file still
    finds the files that imported it.
    """
    changed_list = list(dict.fromkeys(changed))
    seen = set(changed_list)
    imported: list[str] = []
    for p in changed_list:
        for q in graph.get(p, ()):
            if q not in seen:
                seen.add(q)
                imported.append(q)
    imported.sort()
    rev = reverse_graph(importer_graph if importer_graph is not None else graph)
    importers: list[str] = []
    for p in dict.fromkeys(interface_changed_paths):
        for q in rev.get(p, ()):
            if q not in seen:
                seen.add(q)
                importers.append(q)
    importers.sort()
    return changed_list, imported, importers


def call_sites(importer_text: str, names: Iterable[str]) -> tuple[str, ...]:
    """Changed public names that appear as identifiers in an importer."""
    found: list[str] = []
    for n in sorted(set(names)):
        if len(n) < 2:
            continue
        if re.search(rf"\b{re.escape(n)}\b", importer_text):
            found.append(n)
    return tuple(found)


def call_site_in_pack(pack_text: str, importer_path: str, name: str) -> bool:
    """True when the pack mentions both the importer and the changed name.

    This is the 0.1.8 coverage check. The pack does not yet include call-site
    lines (that is 0.1.9 work), so a hop one-liner alone is not enough unless
    it also carries the name.
    """
    if not pack_text or name not in pack_text:
        return False
    hints = [
        importer_path,
        importer_path.rsplit("/", 1)[-1],
        importer_path.replace("/", ".").removesuffix(".py"),
    ]
    return any(h and h in pack_text for h in hints)
