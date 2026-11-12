"""Frozen Phase 2 data model (docs/design/forest-contract.md, sections 2 to 4).

Changing a type here is a step-0 follow-up; every open Phase 2 step hears first.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from rco.canon import canonical_json, sha256_tag, tree_from_obj, tree_to_obj
from rco.model import Tree

SCHEMA_FOREST = "rco.forest/1"
SCHEMA_FORESTHASH = "rco.foresthash/1"
SCHEMA_PACK = "rco.pack/1"
SCHEMA_UPDATE = "rco.update/1"

# Mined prose has no model; its fingerprint uses these fixed values.
MINED_PROMPT_VERSION = "mined/1"
MINED_MODEL_ID = ""
PROSE_CAP = 300  # characters, every prose source
DEFAULT_BUDGET = 8000  # estimated tokens (characters / 4)
DEFAULT_POINTER = "main"

_FOREST_KEYS = frozenset({"schema", "sha", "notes", "modules"})
_ENTRY_KEYS = frozenset({"id", "layer", "depends_on", "used_by", "entry_points",
                         "owns", "tree_hash", "prose"})
_OWNS_KEYS = frozenset({"commands", "tables", "routes"})
_PROSE_KEYS = frozenset({"text", "source", "tree_hash", "prompt_version", "model_id"})
_PACK_KEYS = frozenset({"schema", "sha", "budget", "used", "forest", "trees", "hop",
                        "trimmed", "stale", "notes"})


class ProseSource(str, Enum):
    MINED = "mined"
    LLM = "llm"


@dataclass(frozen=True, slots=True)
class Owns:
    """Names a module owns, read from its Tree. Each tuple is sorted, unique."""

    commands: tuple[str, ...] = ()
    tables: tuple[str, ...] = ()
    routes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Prose:
    """Optional prose for one module, written for exactly one TreeHash."""

    text: str
    source: ProseSource
    tree_hash: str
    prompt_version: str = MINED_PROMPT_VERSION
    model_id: str = MINED_MODEL_ID


@dataclass(frozen=True, slots=True)
class ForestEntry:
    """One module in the Forest. Everything but prose is computed from git."""

    id: str
    layer: int
    depends_on: tuple[str, ...]
    used_by: tuple[str, ...]
    entry_points: tuple[str, ...]
    owns: Owns
    tree_hash: str
    prose: Prose | None = None


@dataclass(frozen=True, slots=True)
class Forest:
    sha: str
    entries: tuple[ForestEntry, ...]  # sorted by id
    notes: tuple[str, ...] = ()  # sorted; cycles and skipped edges land here


@dataclass(frozen=True, slots=True)
class HopLine:
    """A module shown as its name and one line."""

    id: str
    line: str


@dataclass(frozen=True, slots=True)
class Pack:
    sha: str
    budget: int  # estimated tokens
    used: int  # estimated tokens of the rendered markdown
    forest: tuple[ForestEntry, ...]
    trees: tuple[Tree, ...]  # full Trees for the --for modules
    hop: tuple[HopLine, ...]  # one import hop out, name and one line
    trimmed: tuple[str, ...]  # every module or node cut for budget, in cut order
    stale: tuple[str, ...]  # module ids whose prose TreeHash != current TreeHash
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class UpdateResult:
    """What one `rco update` did. A refused swap is not an error (exit 0)."""

    name: str  # pointer name
    old: str | None  # pointer value read at start
    new: str
    swapped: bool
    extracted: tuple[str, ...]  # module ids whose Tree was written this run
    queued: tuple[str, ...]  # module ids queued for prose, only when swapped
    notes: tuple[str, ...] = ()


def is_stale(prose: Prose | None, current_tree_hash: str) -> bool:
    """README rule 3: staleness is computed at read time, never stored."""
    return prose is not None and prose.tree_hash != current_tree_hash


def forest_hash(tree_hash: str, edges: tuple[str, ...], prompt_version: str = MINED_PROMPT_VERSION,
                model_id: str = MINED_MODEL_ID) -> str:
    """ForestHash = sha256 of canonical {tree_hash, edges, prompt_version, model_id}."""
    return sha256_tag(canonical_json({
        "schema": SCHEMA_FORESTHASH,
        "tree_hash": tree_hash,
        "edges": sorted(set(edges)),
        "prompt_version": prompt_version,
        "model_id": model_id,
    }))


def estimate_tokens(text: str) -> int:
    """README: characters / 4, rounded up."""
    return (len(text) + 3) // 4


# ---- canonical JSON ----------------------------------------------------------

def _strict(o: object, allowed: frozenset[str], what: str) -> dict:
    if not isinstance(o, dict):
        raise ValueError(f"{what}: expected an object")
    extra = set(o) - allowed
    if extra:
        raise ValueError(f"{what}: unknown keys {sorted(extra)}")
    return o


def _names(xs) -> tuple[str, ...]:
    return tuple(sorted({str(x) for x in xs}))


def prose_to_obj(p: Prose) -> dict:
    return {"text": p.text, "source": p.source.value, "tree_hash": p.tree_hash,
            "prompt_version": p.prompt_version, "model_id": p.model_id}


def prose_from_obj(o: object) -> Prose:
    o = _strict(o, _PROSE_KEYS, "prose")
    return Prose(str(o["text"]), ProseSource(o["source"]), str(o["tree_hash"]),
                 str(o.get("prompt_version", MINED_PROMPT_VERSION)),
                 str(o.get("model_id", MINED_MODEL_ID)))


def entry_to_obj(e: ForestEntry) -> dict:
    o: dict = {
        "id": e.id,
        "layer": e.layer,
        "depends_on": list(e.depends_on),
        "used_by": list(e.used_by),
        "entry_points": list(e.entry_points),
        "owns": {"commands": list(e.owns.commands), "tables": list(e.owns.tables),
                 "routes": list(e.owns.routes)},
        "tree_hash": e.tree_hash,
    }
    if e.prose is not None:
        o["prose"] = prose_to_obj(e.prose)
    return o


def entry_from_obj(o: object) -> ForestEntry:
    o = _strict(o, _ENTRY_KEYS, "forest entry")
    ow = _strict(o.get("owns", {}), _OWNS_KEYS, "owns")
    layer = o["layer"]
    if not isinstance(layer, int) or isinstance(layer, bool) or layer < 0:
        raise ValueError("forest entry: layer must be an int >= 0")
    return ForestEntry(
        id=str(o["id"]),
        layer=layer,
        depends_on=_names(o.get("depends_on", [])),
        used_by=_names(o.get("used_by", [])),
        entry_points=_names(o.get("entry_points", [])),
        owns=Owns(_names(ow.get("commands", [])), _names(ow.get("tables", [])),
                  _names(ow.get("routes", []))),
        tree_hash=str(o["tree_hash"]),
        prose=prose_from_obj(o["prose"]) if o.get("prose") is not None else None,
    )


def forest_to_obj(f: Forest) -> dict:
    return {"schema": SCHEMA_FOREST, "sha": f.sha, "notes": list(f.notes),
            "modules": [entry_to_obj(e) for e in sorted(f.entries, key=lambda e: e.id)]}


def forest_from_obj(o: object) -> Forest:
    o = _strict(o, _FOREST_KEYS, "forest")
    if o.get("schema") != SCHEMA_FOREST:
        raise ValueError(f"forest: schema must be {SCHEMA_FOREST}")
    entries = tuple(sorted((entry_from_obj(e) for e in o["modules"]), key=lambda e: e.id))
    return Forest(str(o["sha"]), entries, tuple(sorted(str(n) for n in o.get("notes", []))))


def pack_to_obj(p: Pack) -> dict:
    return {
        "schema": SCHEMA_PACK,
        "sha": p.sha,
        "budget": p.budget,
        "used": p.used,
        "forest": [entry_to_obj(e) for e in p.forest],
        "trees": [tree_to_obj(t) for t in p.trees],
        "hop": [{"id": h.id, "line": h.line} for h in p.hop],
        "trimmed": list(p.trimmed),
        "stale": list(p.stale),
        "notes": list(p.notes),
    }


def pack_from_obj(o: object) -> Pack:
    o = _strict(o, _PACK_KEYS, "pack")
    if o.get("schema") != SCHEMA_PACK:
        raise ValueError(f"pack: schema must be {SCHEMA_PACK}")
    return Pack(
        sha=str(o["sha"]), budget=int(o["budget"]), used=int(o["used"]),
        forest=tuple(entry_from_obj(e) for e in o["forest"]),
        trees=tuple(tree_from_obj(t) for t in o["trees"]),
        hop=tuple(HopLine(str(h["id"]), str(h["line"])) for h in o["hop"]),
        trimmed=tuple(str(x) for x in o["trimmed"]),
        stale=tuple(str(x) for x in o["stale"]),
        notes=tuple(str(x) for x in o.get("notes", [])),
    )
