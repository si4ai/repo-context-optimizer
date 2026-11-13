"""Extractor registry (docs/design/tree-contract.md, section 6).

A fixed tuple, sorted by name: no entry points in Phase 1, so output
never depends on what else is installed.
"""
from __future__ import annotations

from typing import ClassVar, Protocol

from rco import redact
from rco.extract.cli import CliExtractor
from rco.extract.langs import GoExtractor, JsExtractor, RustExtractor, ShellExtractor
from rco.extract.python import PythonExtractor
from rco.extract.routes import RoutesExtractor
from rco.extract.sql import SqlExtractor
from rco.model import Node, SourceFile


class Extractor(Protocol):
    name: ClassVar[str]
    version: ClassVar[str]

    def claims(self, path: str) -> bool: ...

    def extract(self, src: SourceFile) -> list[Node]: ...


EXTRACTORS: tuple[Extractor, ...] = (
    CliExtractor(),
    GoExtractor(),
    JsExtractor(),
    PythonExtractor(),
    RoutesExtractor(),
    RustExtractor(),
    ShellExtractor(),
    SqlExtractor(),
)


def versions() -> tuple[tuple[str, str], ...]:
    """Sorted (name, version) of every extractor plus the redaction pass."""
    pairs = [(e.name, e.version) for e in EXTRACTORS] + [("redact", redact.VERSION)]
    return tuple(sorted(pairs))
