"""Build Trees and Maps from a commit (tree-contract section 6)."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Sequence

from rco import canon, extract, gitread, modules, redact
from rco.model import Kind, Map, Module, Node, SourceFile, Tree, node_sort_key


def _sorted(n: Node) -> Node:
    return replace(
        n,
        attrs=tuple(sorted(n.attrs)),
        children=tuple(sorted((_sorted(c) for c in n.children), key=node_sort_key)),
    )


def file_node(path: str, attrs: tuple[tuple[str, str], ...] = ()) -> Node:
    return Node(Kind.FILE, PurePosixPath(path).name, path, attrs=attrs)


def nodes_for(src: SourceFile, link: bool = False) -> list[Node]:
    """Every extractor that claims the file; FILE outline when none does."""
    if link:
        return [file_node(src.path, (("link", "1"),))]
    if src.data.startswith(gitread.LFS_MAGIC):
        return [file_node(src.path, (("lfs", "pointer"),))]
    out: list[Node] = []
    claimed = False
    for ex in extract.EXTRACTORS:
        if ex.claims(src.path):
            claimed = True
            out.extend(ex.extract(src))
    if not claimed:
        return [file_node(src.path)]
    # duplicate FILE error nodes from several extractors collapse into one
    seen: set[tuple] = set()
    uniq = []
    for n in out:
        if n.kind is Kind.FILE:
            k = (n.name, n.path, n.attrs)
            if k in seen:
                continue
            seen.add(k)
        uniq.append(n)
    return uniq


def build_tree(module: Module, files: Sequence[SourceFile],
               links: frozenset[str] = frozenset()) -> Tree:
    """Tree of one module. files must cover module.files; links are symlink paths."""
    by_path = {f.path: f for f in files}
    nodes: list[Node] = []
    for p in module.files:
        nodes.extend(nodes_for(by_path[p], p in links))
    nodes = [_sorted(redact.redact_node(n)) for n in nodes]
    vers = extract.versions()
    return Tree(
        module=module.id,
        module_hash=canon.module_hash(module, {f.path: f.blob for f in files}, vers),
        extractors=vers,
        nodes=tuple(sorted(nodes, key=node_sort_key)),
    )


def build_map(repo: Path, sha: str, only: Sequence[str] | None = None) -> Map:
    """Map of every module at sha, or of the modules that own the paths in only."""
    notes: list[str] = []
    entries: list[gitread.Entry] = []
    for e in gitread.ls_tree(repo, sha):
        if e.mode == gitread.MODE_SUBMODULE:
            notes.append(f"skipped submodule {e.path}")
        else:
            entries.append(e)
    layout = modules.layout_at(repo, sha, entries)
    notes.extend(layout.notes)
    mods = layout.group(e.path for e in entries)
    if only is not None:
        want = {layout.module_of(p.strip("/")) for p in only}
        mods = tuple(m for m in mods if m.id in want)
    keep = {p for m in mods for p in m.files}
    entries = [e for e in entries if e.path in keep]
    links = frozenset(e.path for e in entries if e.mode == gitread.MODE_SYMLINK)
    data = gitread.read_blobs(repo, [e.blob for e in entries if e.path not in links])
    srcs = {e.path: SourceFile(e.path, e.blob, b"" if e.path in links else data[e.blob])
            for e in entries}
    trees = tuple(build_tree(m, [srcs[p] for p in m.files], links) for m in mods)
    return Map(sha=sha, trees=trees, notes=tuple(sorted(notes)))
