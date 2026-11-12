import argparse
import os

from .core import engine


def main() -> int:
    p = argparse.ArgumentParser(prog="app")
    p.add_argument("--fast", action="store_true")
    p.parse_args()
    return engine.run()
