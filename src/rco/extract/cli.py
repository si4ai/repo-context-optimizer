"""CLI commands and flags, argparse first (step 4).

Step-1 stub: claims its files and returns no nodes. The owning step
replaces extract() and bumps version.
"""
from __future__ import annotations

from typing import ClassVar

from rco.model import Node, SourceFile


class CliExtractor:
    name: ClassVar[str] = "cli"
    version: ClassVar[str] = "0"

    def claims(self, path: str) -> bool:
        return path.endswith(".py")

    def extract(self, src: SourceFile) -> list[Node]:
        return []
