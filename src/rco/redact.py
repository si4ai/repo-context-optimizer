"""Redaction pass (docs/design/tree-contract.md, section 7).

Step-1 stub: identity. Step 5 adds the README Secrets list and bumps
VERSION. Applied centrally by build.py before hashing.
"""
from __future__ import annotations

from dataclasses import replace

from rco.model import Kind, Node

VERSION = "0"
PLACEHOLDER = "<redacted>"

_SIG_KINDS = frozenset({Kind.CONSTANT, Kind.FIELD})
_ATTR_KEYS = frozenset({"value", "default"})


def redact_text(s: str) -> str:
    return s


def redact_node(n: Node) -> Node:
    """Redact doc, the sig of CONSTANT/FIELD, and attrs value/default."""
    return replace(
        n,
        doc=redact_text(n.doc),
        sig=redact_text(n.sig) if n.kind in _SIG_KINDS else n.sig,
        attrs=tuple((k, redact_text(v) if k in _ATTR_KEYS else v) for k, v in n.attrs),
        children=tuple(redact_node(c) for c in n.children),
    )
