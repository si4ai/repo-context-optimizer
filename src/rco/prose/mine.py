"""Mined prose: zero tokens, deterministic, redacted (Phase 2 step 4).

Sources, first hit wins:
1. the module docstring, when the module has one Python file besides __init__.py;
2. the package docstring (__init__.py);
3. the first paragraph of a README in the module's directory;
4. the first module docstring among its other Python files, in path order.
The first paragraph is joined to one line and capped at PROSE_CAP characters.
"""
from __future__ import annotations

import ast
import re
import warnings
from pathlib import PurePosixPath
from typing import Mapping

from rco.forest_model import PROSE_CAP
from rco.model import Module
from rco.redact import redact_text

_README = re.compile(r"^readme(\.(md|rst|txt))?$", re.IGNORECASE)


def first_paragraph(text: str) -> str:
    para: list[str] = []
    for raw in text.strip().splitlines():
        line = raw.strip()
        if not line:
            if para:
                break
            continue
        if line.startswith("#") or set(line) <= set("=-~*"):  # headings and rules
            if para:
                break
            continue
        para.append(line)
    return " ".join(para)


def cap(text: str, limit: int = PROSE_CAP) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[: limit - 3].rsplit(" ", 1)[0] or text[: limit - 3]
    return cut + "..."


def docstring(data: bytes) -> str:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tree = ast.parse(data)
    except (SyntaxError, ValueError):
        return ""
    return ast.get_docstring(tree, clean=True) or ""


def mine(module: Module, sources: Mapping[str, bytes]) -> str:
    """Prose text, at most PROSE_CAP characters, redacted; "" when nothing is found."""
    py = [p for p in module.files if p.endswith(".py") and p in sources]
    init = [p for p in py if PurePosixPath(p).name == "__init__.py"]
    others = [p for p in py if p not in init]
    readmes = [p for p in module.files if _README.match(PurePosixPath(p).name) and p in sources]
    candidates: list[str] = []
    if len(others) == 1:
        candidates.append(docstring(sources[others[0]]))
    candidates += [docstring(sources[p]) for p in init]
    candidates += [sources[p].decode("utf-8", "replace") for p in readmes]
    candidates += [docstring(sources[p]) for p in others]
    for c in candidates:
        para = first_paragraph(c)
        if para:
            return cap(redact_text(para))
    return ""
