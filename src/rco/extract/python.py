"""Python code contracts via ast (filled in by step 1)."""
from __future__ import annotations

from typing import ClassVar

from rco.model import Node, SourceFile


class PythonExtractor:
    name: ClassVar[str] = "python"
    version: ClassVar[str] = "0"

    def claims(self, path: str) -> bool:
        return path.endswith(".py")

    def extract(self, src: SourceFile) -> list[Node]:
        return []
