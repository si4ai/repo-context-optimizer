"""Canonical JSON and hashes (docs/design/tree-contract.md, section 3)."""
from __future__ import annotations

import hashlib
import json

from rco.model import Kind, Map, Module, Node, Tree

SCHEMA_TREE = "rco.tree/1"
SCHEMA_MAP = "rco.map/1"
SCHEMA_MODULEHASH = "rco.modulehash/1"

_NODE_KEYS = frozenset({"kind", "name", "path", "sig", "doc", "attrs", "children"})
_TREE_KEYS = frozenset({"schema", "module", "module_hash", "extractors", "nodes", "tree_hash"})
_MAP_KEYS = frozenset({"schema", "sha", "notes", "modules"})


def canonical_json(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def sha256_tag(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def node_to_obj(n: Node) -> dict:
    o: dict = {"kind": n.kind.value, "name": n.name}
    if n.path:
        o["path"] = n.path
    if n.sig:
        o["sig"] = n.sig
    if n.doc:
        o["doc"] = n.doc
    if n.attrs:
        o["attrs"] = dict(n.attrs)
    if n.children:
        o["children"] = [node_to_obj(c) for c in n.children]
    return o


def tree_to_obj(t: Tree) -> dict:
    return {
        "schema": SCHEMA_TREE,
        "module": t.module,
        "module_hash": t.module_hash,
        "extractors": [list(e) for e in t.extractors],
        "nodes": [node_to_obj(n) for n in t.nodes],
    }


def tree_hash(t: Tree) -> str:
    return sha256_tag(canonical_json(tree_to_obj(t)))


def map_to_obj(m: Map) -> dict:
    mods = []
    for t in m.trees:
        o = tree_to_obj(t)
        o["tree_hash"] = tree_hash(t)
        mods.append(o)
    return {"schema": SCHEMA_MAP, "sha": m.sha, "notes": list(m.notes), "modules": mods}


def module_hash(module: Module, blobs: dict[str, str],
                extractors: tuple[tuple[str, str], ...]) -> str:
    return sha256_tag(canonical_json({
        "schema": SCHEMA_MODULEHASH,
        "module": module.id,
        "files": [[p, blobs[p]] for p in module.files],
        "extractors": [list(e) for e in extractors],
    }))


def _strict(o: dict, allowed: frozenset[str], what: str) -> None:
    if not isinstance(o, dict):
        raise ValueError(f"{what}: expected an object")
    extra = set(o) - allowed
    if extra:
        raise ValueError(f"{what}: unknown keys {sorted(extra)}")


def node_from_obj(o: dict) -> Node:
    _strict(o, _NODE_KEYS, "node")
    attrs = o.get("attrs", {})
    if not isinstance(attrs, dict):
        raise ValueError("node: attrs must be an object")
    return Node(
        kind=Kind(o["kind"]),
        name=str(o["name"]),
        path=str(o.get("path", "")),
        sig=str(o.get("sig", "")),
        doc=str(o.get("doc", "")),
        attrs=tuple(sorted((str(k), str(v)) for k, v in attrs.items())),
        children=tuple(node_from_obj(c) for c in o.get("children", [])),
    )


def tree_from_obj(o: dict) -> Tree:
    _strict(o, _TREE_KEYS, "tree")
    if o.get("schema") != SCHEMA_TREE:
        raise ValueError(f"tree: schema must be {SCHEMA_TREE}")
    return Tree(
        module=str(o["module"]),
        module_hash=str(o["module_hash"]),
        extractors=tuple((str(a), str(b)) for a, b in o["extractors"]),
        nodes=tuple(node_from_obj(n) for n in o["nodes"]),
    )


def map_from_obj(o: dict) -> Map:
    _strict(o, _MAP_KEYS, "map")
    if o.get("schema") != SCHEMA_MAP:
        raise ValueError(f"map: schema must be {SCHEMA_MAP}")
    return Map(
        sha=str(o["sha"]),
        trees=tuple(tree_from_obj(t) for t in o["modules"]),
        notes=tuple(str(n) for n in o.get("notes", [])),
    )
