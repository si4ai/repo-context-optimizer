"""Phase 2 step 2: import edges resolve to in-repo modules only."""
from rco.imports import file_imports, module_edges
from rco.model import Module

IDX = {"app": "src/app/__init__.py", "app.util": "src/app/util/__init__.py",
       "app.util.helpers": "src/app/util/helpers.py", "app.core.engine": "src/app/core/engine.py",
       "app.core": "src/app/core/__init__.py"}


def _imp(src: str, path: str = "src/app/core/engine.py"):
    return file_imports(path, src.encode(), IDX)


def test_absolute_and_from():
    assert _imp("import app.util.helpers as h\n") == ("src/app/util/helpers.py",)
    assert _imp("from app.util import helpers\n") == ("src/app/util/helpers.py",)
    assert _imp("from app.util.helpers import clamp\n") == ("src/app/util/helpers.py",)
    assert _imp("import app.util.missing\n") == ("src/app/util/__init__.py",)


def test_relative():
    assert _imp("from ..util import helpers\n") == ("src/app/util/helpers.py",)
    assert _imp("from . import engine\n") == ()  # itself
    assert _imp("from .. import util\n") == ("src/app/util/__init__.py",)
    assert _imp("from ...... import x\n") == ()  # beyond the top
    assert _imp("from ..util import *\n") == ("src/app/util/__init__.py",)


def test_relative_from_package_init():
    assert file_imports("src/app/util/__init__.py", b"from .helpers import clamp\n",
                        IDX) == ("src/app/util/helpers.py",)


def test_external_and_broken_ignored():
    assert _imp("import os, json\nfrom collections import abc\n") == ()
    assert _imp("def broken(:\n") == ()


def test_module_edges_excludes_self_and_external():
    mods = (Module("src/app", ("src/app/__init__.py", "src/app/cli.py")),
            Module("src/app/core", ("src/app/core/__init__.py", "src/app/core/engine.py")),
            Module("src/app/util", ("src/app/util/__init__.py", "src/app/util/helpers.py")))
    src = {"src/app/__init__.py": b"", "src/app/cli.py": b"from app import core\nimport os\n",
           "src/app/core/__init__.py": b"", "src/app/core/engine.py": b"from ..util import helpers\n",
           "src/app/util/__init__.py": b"", "src/app/util/helpers.py": b"from . import helpers\n"}
    assert module_edges(mods, src) == {"src/app": ("src/app/core",),
                                       "src/app/core": ("src/app/util",), "src/app/util": ()}
