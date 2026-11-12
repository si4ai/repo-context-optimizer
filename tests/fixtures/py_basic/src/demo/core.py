"""Core engine.

Second docstring line is not part of the Tree.
"""
import os

LIMIT: int = 10
TIMEOUT = 2.5
_PRIVATE = 1
PATTERN = os.sep
lower_name = 3


def run(argv: list[str], *, verbose: bool = False, sink=print) -> int:
    """Run the engine."""
    return 0


async def fetch(url: str, retries: int = 3) -> bytes:
    return b""


def _helper() -> None:
    pass


class Engine(Base, metaclass=Meta):
    """An engine."""

    name: str
    size: int = 4
    _hidden: int = 0
    KIND = "engine"

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def label(self) -> str:
        return self.name

    @staticmethod
    def make(cfg: dict = {"a": 1}) -> "Engine":
        return Engine("x")

    def _private(self) -> None:
        pass

    class Options:
        fast: bool = True


class _Internal:
    def visible(self) -> None:
        pass
