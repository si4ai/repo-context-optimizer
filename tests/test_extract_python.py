"""Python extractor: shapes per the contract table, purity, parse errors."""
from rco.extract.python import PythonExtractor, module_name
from rco.model import Kind, Node, SourceFile

EX = PythonExtractor()


def _one(src: str, path: str = "pkg/m.py") -> Node:
    (n,) = EX.extract(SourceFile(path, "0" * 40, src.encode()))
    return n


def _child(n: Node, name: str) -> Node:
    (c,) = [c for c in n.children if c.name == name]
    return c


def test_module_name():
    assert module_name("src/rco/extract/__init__.py") == "rco.extract"
    assert module_name("tools/x.py") == "tools.x"
    assert module_name("setup.py") == "setup"


def test_function_sig_and_defaults():
    m = _one("def f(a: int, b=1, c=g(), *, d: str = 'x' * 50) -> int:\n    return a\n")
    f = _child(m, "f")
    assert f.kind is Kind.FUNCTION
    assert f.sig == "(a: int, b=1, c=..., *, d: str=...) -> int"


def test_body_change_keeps_node_signature_change_moves_it():
    a = _one("def f(x: int) -> int:\n    return x\n")
    b = _one("def f(x: int) -> int:\n    y = x + 1\n    return y\n")
    c = _one("def f(x: int, y: int) -> int:\n    return x\n")
    assert a == b
    assert a != c


def test_private_names_dropped_dunder_kept():
    m = _one("def _p(): pass\nclass C:\n    def __init__(self): pass\n    def _q(self): pass\n")
    assert [c.name for c in m.children] == ["C"]
    assert [c.name for c in _child(m, "C").children] == ["__init__"]


def test_async_decorators_constants_fields_exports():
    m = _one(
        "__all__ = ['f']\nN = 3\nlow = 1\n"
        "@dec\nasync def f(): pass\n"
        "class C(B):\n    x: int = 2\n    y: str\n"
    )
    f = _child(m, "f")
    assert dict(f.attrs) == {"async": "1", "decorators": "dec"}
    assert _child(m, "__all__").sig == '["f"]'
    assert dict(_child(m, "N").attrs) == {"value": "3"}
    assert "low" not in [c.name for c in m.children]
    c = _child(m, "C")
    assert c.sig == "(B)"
    assert dict(_child(c, "x").attrs) == {"value": "2"}
    assert _child(c, "y").sig == "str" and _child(c, "y").attrs == ()


def test_parse_error_is_a_file_node_not_an_exception():
    (n,) = EX.extract(SourceFile("pkg/bad.py", "0" * 40, b"def (:\n"))
    assert n == Node(Kind.FILE, "bad.py", "pkg/bad.py", attrs=(("error", "parse"),))


def test_pure_same_input_same_output():
    src = SourceFile("pkg/m.py", "0" * 40, b'"""Doc."""\ndef f(a): pass\n')
    assert EX.extract(src) == EX.extract(src)


def test_no_line_numbers_in_nodes():
    n = _one("def f():\n    pass\n\n\n\nclass C: pass\n")
    shifted = _one("\n\n\n\ndef f():\n    pass\n\n\n\nclass C: pass\n")
    assert n == shifted
