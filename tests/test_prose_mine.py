"""Phase 2 step 4: mined prose picks a fixed source order, caps and redacts."""
from rco.forest_model import PROSE_CAP
from rco.model import Module
from rco.prose.mine import cap, first_paragraph, mine


def _m(*files):
    return Module("pkg", tuple(sorted(files)))


def test_single_file_docstring_first():
    src = {"pkg/__init__.py": b'"""Package doc."""\n', "pkg/core.py": b'"""Core doc.\n\nMore."""\n'}
    assert mine(_m(*src), src) == "Core doc."


def test_package_docstring_then_readme_then_files():
    src = {"pkg/__init__.py": b'"""Package doc\nsecond line."""\n', "pkg/a.py": b'"""A."""\n',
           "pkg/b.py": b"x = 1\n", "pkg/README.md": b"# Title\n\nReadme para.\n"}
    assert mine(_m(*src), src) == "Package doc second line."
    src["pkg/__init__.py"] = b""
    assert mine(_m(*src), src) == "Readme para."
    del src["pkg/README.md"]
    assert mine(_m(*src), src) == "A."


def test_nothing_found_is_empty():
    src = {"pkg/a.py": b"x = 1\n", "pkg/b.py": b"def broken(:\n"}
    assert mine(_m(*src), src) == ""


def test_cap_and_redact():
    long = ("word " * 200).encode()
    src = {"pkg/README.md": long}
    out = mine(_m(*src), src)
    assert len(out) <= PROSE_CAP and out.endswith("...")
    key = "AKIA" + "Q" * 16
    src = {"pkg/README.md": f"Uses key {key} here.\n".encode()}
    assert key not in mine(_m(*src), src)


def test_first_paragraph_skips_headings_and_rules():
    assert first_paragraph("Title\n=====\n\nBody text\nmore\n\nnext") == "Title"
    assert first_paragraph("# H\n---\n\n  Body  \n") == "Body"
    assert cap("a  b\n c") == "a b c"
