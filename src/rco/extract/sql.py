"""SQL schema.

Step-1 stub: claims its files and returns no nodes. The owning step
replaces extract() and bumps version.
"""
from __future__ import annotations

from typing import ClassVar

from rco.model import Node, SourceFile


class SqlExtractor:
    name: ClassVar[str] = "sql"
    version: ClassVar[str] = "0"

    def claims(self, path: str) -> bool:
        return path.endswith(" tables, views, columns, indexes, procedures (step 2):.sql")

    def extract(self, src: SourceFile) -> list[Node]:
        return []
