"""A script with no prog: the command is named after the file."""
import argparse


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true")
    return ap.parse_args()
