"""Phase 2 step 4: prose that cites a name missing from the Tree is flagged."""
from rco.model import Kind, Node, Tree
from rco.prose import flags
from rco.forest_model import Prose, ProseSource
from rco.prose.check import cited_names, unknown_names

T = Tree("pkg", "sha256:" + "0" * 64, (("python", "1"),), (
    Node(Kind.PYMODULE, "pkg.core", "pkg/core.py", children=(
        Node(Kind.FUNCTION, "run_job", "", "() -> int"),
        Node(Kind.CLASS, "JobStore", "", children=(Node(Kind.METHOD, "get_item", "", "()"),)),
    )),
    Node(Kind.COMMAND, "tool run", "pkg/cli.py", children=(Node(Kind.FLAG, "--dry-run", ""),)),
))


def test_cited_names():
    text = "Runs `run_job` and JobStore.get_item(); see README.md and https://x.y/z."
    assert cited_names(text) == ("JobStore", "JobStore.get_item", "get_item", "run_job")


def test_known_names_pass():
    assert unknown_names("Calls run_job() through `JobStore` and `pkg.core`; `--dry-run`.", T) == ()
    assert unknown_names("Plain words only, nothing code-shaped.", T) == ()


def test_planted_bad_name_is_flagged():
    assert unknown_names("Calls `run_jobs` and MissingThing via fetch_all().", T) == (
        "MissingThing", "fetch_all", "run_jobs")
    assert flags(Prose("Uses `nope_here`.", ProseSource.MINED, "h"), T) == ("nope_here",)
    assert flags(None, T) == ()
