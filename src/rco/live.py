"""Check a Map against real outputs the user captured (`rco live`).

rco never runs repo code. The user captures what the running system says
and passes the files in; the comparison is then as deterministic as the
rest of rco:

- `--schema FILE`: a SQL schema dump (for example `pg_dump --schema-only`
  or `sqlite3 db .schema`). It is parsed with the same SQL extractor as
  the repo's .sql files, then tables, views and column names are compared.
  Column types are not compared, because dumps spell them differently
  (TIMESTAMPTZ and timestamp with time zone).
- `--openapi FILE`: an OpenAPI or Swagger JSON document (`paths` with
  their methods), compared with ROUTE nodes. Path parameters compare
  equal in any spelling: `{id}`, `<int:id>`, `:id`. A Tree route with
  method ANY matches every method; WS routes are not in OpenAPI and are
  skipped.
- `--help-text "CMD=FILE"`: a captured `CMD --help`, compared with the
  long `--flags` of the COMMAND node named CMD (`tool db migrate`).
  `--help` itself is skipped on both sides.

Names compare case-folded. A qualified table (`public.users`) matches a
Tree table by its full name, or by its last part when that is unique.
`missing` means the live output has it and the Map does not; `extra`
means the Map claims it and the live output does not.
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from rco.check import Finding
from rco.extract.sql import SqlExtractor
from rco.model import Kind, Map, Node, SourceFile
from rco.output import Exit, UsageError

SCHEMA_LIVE = "rco.live/1"
_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")
_PARAM = re.compile(r"\{[^}/]*\}|<[^>/]*>|(?<=/):[A-Za-z_]\w*")
_LONG_FLAG = re.compile(r"(?<![\w-])--[A-Za-z0-9][\w-]*")


def _walk(nodes: Iterable[Node], path: str = "", parent: Node | None = None):
    for n in nodes:
        p = n.path or path
        yield n, p, parent
        yield from _walk(n.children, p, n)


def _nodes(m: Map):
    for t in m.trees:
        yield from _walk(t.nodes)


def norm_route(path: str) -> str:
    p = _PARAM.sub("{}", path.strip())
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


# ----------------------------------------------------------------- schema

def _tables(nodes) -> dict[str, tuple[str, str, set[str]]]:
    """casefolded name -> (name, path, casefolded column names)."""
    out: dict[str, tuple[str, str, set[str]]] = {}
    for n, p, _ in nodes:
        if n.kind in (Kind.TABLE, Kind.VIEW):
            cols = {c.name.casefold() for c in n.children if c.kind == Kind.COLUMN}
            prev = out.get(n.name.casefold())
            out[n.name.casefold()] = (n.name, p, (prev[2] if prev else set()) | cols)
    return out


def _match(name: str, other: dict[str, tuple]) -> str | None:
    if name in other:
        return name
    last = name.rsplit(".", 1)[-1]
    hits = [k for k in other if k.rsplit(".", 1)[-1] == last]
    return hits[0] if len(hits) == 1 else None


def compare_schema(m: Map, dump: str, label: str = "schema") -> list[Finding]:
    live_nodes = SqlExtractor().extract(SourceFile(f"{label}.sql", "", dump.encode("utf-8")))
    live = _tables(_walk(live_nodes))
    if not live:
        raise UsageError(f"{label}: no CREATE TABLE or CREATE VIEW found in the schema dump")
    tree = _tables(_nodes(m))
    out: list[Finding] = []
    seen: set[str] = set()
    for k, (name, _, cols) in sorted(live.items()):
        hit = _match(k, tree)
        if hit is None:
            out.append(Finding("missing", f"table:{name}", label, "in the live schema, not in the map"))
            continue
        seen.add(hit)
        tname, tpath, tcols = tree[hit]
        for c in sorted(cols - tcols):
            out.append(Finding("missing", f"column:{tname}.{c}", tpath, "in the live schema"))
        for c in sorted(tcols - cols):
            out.append(Finding("extra", f"column:{tname}.{c}", tpath, "not in the live schema"))
    for k, (name, path, _) in sorted(tree.items()):
        if k not in seen and _match(k, live) is None:
            out.append(Finding("extra", f"table:{name}", path, "not in the live schema"))
    return out


# ---------------------------------------------------------------- openapi

def compare_openapi(m: Map, doc: object, label: str = "openapi") -> list[Finding]:
    paths = doc.get("paths") if isinstance(doc, dict) else None
    if not isinstance(paths, dict):
        raise UsageError(f"{label}: no `paths` object (not an OpenAPI or Swagger document)")
    prefix = ""
    if isinstance(doc.get("basePath"), str):  # Swagger 2
        prefix = doc["basePath"].rstrip("/")
    live: dict[tuple[str, str], str] = {}
    for raw, ops in sorted(paths.items()):
        if not isinstance(ops, dict):
            continue
        for meth in _METHODS:
            if meth in ops:
                live[(meth.upper(), norm_route(prefix + raw))] = f"{meth.upper()} {prefix}{raw}"
    tree: dict[tuple[str, str], tuple[str, str]] = {}
    for n, p, _ in _nodes(m):
        if n.kind == Kind.ROUTE and " " in n.name:
            meth, path = n.name.split(" ", 1)
            if meth != "WS":
                tree[(meth, norm_route(path))] = (n.name, p)
    out: list[Finding] = []
    any_paths = {path for meth, path in tree if meth == "ANY"}
    for key, shown in sorted(live.items()):
        if key not in tree and key[1] not in any_paths:
            out.append(Finding("missing", f"route:{shown}", label, "in the live API, not in the map"))
    live_paths = {path for _, path in live}
    for key, (name, path) in sorted(tree.items()):
        ok = key[1] in live_paths if key[0] == "ANY" else key in live
        if not ok:
            out.append(Finding("extra", f"route:{name}", path, "not in the live API"))
    return out


# ------------------------------------------------------------------- help

def compare_help(m: Map, command: str, text: str) -> list[Finding]:
    cmds = [(n, p) for n, p, _ in _nodes(m) if n.kind == Kind.COMMAND and n.name == command]
    if not cmds:
        raise UsageError(f"--help-text: no command {command!r} in the map")
    n, path = cmds[0]
    tree = {c.name for c in n.children if c.kind == Kind.FLAG and c.name.startswith("--")}
    live = set(_LONG_FLAG.findall(text))
    tree.discard("--help")
    live.discard("--help")
    out = [Finding("missing", f"flag:{command} {f}", path, "in --help, not in the map")
           for f in sorted(live - tree)]
    out += [Finding("extra", f"flag:{command} {f}", path, "not in --help")
            for f in sorted(tree - live)]
    return out


# ----------------------------------------------------------------- report

@dataclass(frozen=True)
class LiveReport:
    sha: str
    sources: tuple[str, ...]
    findings: tuple[Finding, ...]

    def to_obj(self) -> dict:
        return {"schema": SCHEMA_LIVE, "sha": self.sha, "sources": list(self.sources),
                "findings": [{"status": f.status, "key": f.key, "path": f.path,
                              "detail": f.detail} for f in self.findings]}

    def to_markdown(self) -> str:
        out = [f"# rco live  (sha={self.sha[:12]})", "",
               f"checked against: {', '.join(self.sources)}", ""]
        if not self.findings:
            out.append("no findings: the map matches the live outputs.")
        by = defaultdict(list)
        for f in self.findings:
            by[f.status].append(f)
        for status in ("missing", "extra"):
            if by[status]:
                out += [f"## {status} ({len(by[status])})", ""]
                out += [f"- `{f.key}` ({f.path}): {f.detail}" for f in by[status]] + [""]
        return "\n".join(out).rstrip("\n") + "\n"

    def exit_code(self) -> Exit:
        return Exit.FINDINGS if self.findings else Exit.OK


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        raise UsageError(f"unreadable {path}: {e}") from None


def live(m: Map, schema: str | None = None, openapi: str | None = None,
         help_texts: Sequence[str] = ()) -> LiveReport:
    if not (schema or openapi or help_texts):
        raise UsageError("live needs --schema, --openapi or --help-text")
    found: list[Finding] = []
    sources: list[str] = []
    if schema:
        found += compare_schema(m, _read(schema), Path(schema).name)
        sources.append(f"schema {Path(schema).name}")
    if openapi:
        try:
            doc = json.loads(_read(openapi))
        except ValueError as e:
            raise UsageError(f"{openapi}: not JSON ({e}); convert YAML to JSON first") from None
        found += compare_openapi(m, doc, Path(openapi).name)
        sources.append(f"openapi {Path(openapi).name}")
    for spec in help_texts:
        cmd, sep, file = spec.rpartition("=")
        if not sep or not cmd.strip():
            raise UsageError(f"--help-text wants CMD=FILE, got {spec!r}")
        found += compare_help(m, cmd.strip(), _read(file))
        sources.append(f"help {cmd.strip()}")
    found.sort(key=lambda f: (f.status, f.key, f.path))
    return LiveReport(m.sha, tuple(sources), tuple(found))
