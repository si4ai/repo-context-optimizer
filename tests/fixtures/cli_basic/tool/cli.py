"""Fixture CLI: the argparse patterns the cli extractor follows."""
import argparse

VERBS = ("start", "stop")
COMMANDS = {
    "show": ("print one item", "ID"),
    "list": ("print every item", None),
}
SKIP_YES = {"version"}
OUT_HELP = "write the report to FILE"


class ToolParser(argparse.ArgumentParser):
    def error(self, message):
        raise SystemExit(2)


def _common(p):
    p.add_argument("--out", metavar="FILE", default=None, help=OUT_HELP)
    p.add_argument("--format", choices=("markdown", "json"), default="markdown")


def _yes_everywhere(parser):
    for act in parser._actions:
        if isinstance(act, argparse._SubParsersAction):
            for child in act.choices.values():
                has_sub = any(isinstance(a, argparse._SubParsersAction) for a in child._actions)
                if not has_sub and child.prog.split()[-1] not in SKIP_YES:
                    child.add_argument("--yes", action="store_true", help="skip any prompt")
                _yes_everywhere(child)


def make_parser():
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("-q", "--quiet", action="store_true")

    p = ToolParser(prog="tool", description="Fixture tool.\n\nMore text.")
    p.add_argument("-V", "--version", action="version", version="tool 1")
    sub = p.add_subparsers(dest="cmd", required=True)

    run = sub.add_parser("run", help="run a job", parents=[shared])
    run.add_argument("job", nargs="+", help="job names")
    g = run.add_mutually_exclusive_group()
    g.add_argument("--fast", action="store_true")
    g.add_argument("--slow", action="store_true")
    run.add_argument("--retries", type=int, default=3, required=False)
    _common(run)

    for verb in VERBS:
        vp = sub.add_parser(verb, help=f"{verb} the service")
        vp.add_argument("name")
        if verb == "stop":
            vp.add_argument("--force", action="store_true")

    for name, (text, metavar) in COMMANDS.items():
        cp = sub.add_parser(name, help=text)
        if metavar:
            cp.add_argument("item", metavar=metavar)

    sub.choices["list"].add_argument("--limit", type=int, default=50)

    db = sub.add_parser("db", help="database commands")
    db_sub = db.add_subparsers(dest="db_cmd", required=True)
    mig = db_sub.add_parser("migrate", help="apply migrations")
    mig.add_argument("--to", dest="target", default="")
    db_sub.add_parser("version", help="print the schema version")

    _yes_everywhere(p)
    return p


def main(argv=None):
    return make_parser().parse_args(argv)


if __name__ == "__main__":
    main()
