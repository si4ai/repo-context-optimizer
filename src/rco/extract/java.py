"""Code contracts for Java (card 1362; no LLM, no deps).

A deterministic scanner (rco.extract.scan, lang "java") masks comments,
strings and text blocks; declarations are read in order, so annotations,
modifiers and one-line declarations all work. Bodies are dropped. Each file
is one SRCMODULE node (lang java, package attr) whose children follow the
other languages: CLASS (kind: class, interface, enum, record, annotation),
METHOD (constructors are `constructor`), FIELD, CONSTANT.

Public means: a top-level type that is `public`; a member that is `public`
or `protected`; every member of an interface or annotation that is not
`private`; every enum constant. Package-private and private names are not
shown. Overloads keep one node each, told apart by their signature.
"""
from __future__ import annotations

import re
from typing import ClassVar

from rco.extract.brace import (IDENT, block_end, find_term, read_mods, skip_angles,
                               skip_annotations, skip_ws, split_top, strip_annotations)
from rco.extract.langs import _container, _text
from rco.extract.scan import Scan, doc_before, match_paren, norm, scan
from rco.model import Kind, Node, SourceFile

JAVA_EXT = (".java",)
MODS = ("public", "protected", "private", "static", "final", "abstract", "default", "sealed",
        "non-sealed", "synchronized", "native", "transient", "volatile", "strictfp")
_TYPE = re.compile(rf"(?P<kw>class|interface|enum|@\s*interface|record(?=\s+{IDENT}\s*[(<]))"
                   rf"\s+(?P<name>{IDENT})")
_PACKAGE = re.compile(rf"package\s+(?P<name>{IDENT}(?:\s*\.\s*{IDENT})*)\s*;")
_LAST_IDENT = re.compile(rf"(?P<name>{IDENT})\s*$")
_DIMS = re.compile(r"(?:\s*\[\s*\])+\s*$")
_KIND = {"class": "class", "interface": "interface", "enum": "enum", "record": "record"}
_KEEP = ("abstract", "final", "static", "sealed", "default")


def _attrs(kind: str | None, mods: list[str]) -> tuple[tuple[str, str], ...]:
    a = [("kind", kind)] if kind else []
    a += [(m, "1") for m in _KEEP if m in mods]
    if "non-sealed" in mods:
        a.append(("non-sealed", "1"))
    return tuple(sorted(a))


def _clean(s: Scan, a: int, b: int) -> str:
    return norm(strip_annotations(s.nc[a:b]))


def _visible(mods: list[str], owner: str) -> bool:
    if "private" in mods:
        return False
    if owner in ("interface", "annotation"):
        return True
    return "public" in mods or "protected" in mods


def _enum_constants(s: Scan, lo: int, hi: int, nodes: list[Node]) -> int:
    """Read `A, B(1), C { ... };` from lo; the index after the `;` (or hi)."""
    code, p = s.code, lo
    while True:
        start = p = skip_ws(code, p, hi)
        p = skip_annotations(code, p, hi)
        if p >= hi:
            return hi
        if code[p] == ";":
            return p + 1
        m = re.compile(IDENT).match(code, p, hi)
        if not m:
            return start
        nodes.append(Node(Kind.FIELD, m.group(0), doc=doc_before(s, start)))
        p = skip_ws(code, m.end(), hi)
        if p < hi and code[p] == "(":
            p = skip_ws(code, match_paren(code, p) + 1, hi)
        if p < hi and code[p] == "{":
            p = skip_ws(code, block_end(code, p), hi)
        if p < hi and code[p] == ",":
            p += 1
            continue
        if p < hi and code[p] == ";":
            return p + 1
        return p


def _member(s: Scan, q: int, hi: int, mods: list[str], owner: str, owner_name: str,
            start: int, nodes: list[Node]) -> int:
    """One method, constructor or field starting at q (after modifiers); the index after it."""
    code = s.code
    tparams = ""
    if code[q] == "<":
        r = skip_angles(code, q, hi)
        tparams = _clean(s, q, r)
        q = skip_ws(code, r, hi)
    t = find_term(code, q, hi, "(={;")
    if t < 0:
        return hi
    vis = _visible(mods, owner)
    if code[t] == "{":  # initializer block, or a compact record constructor
        return block_end(code, t)
    if code[t] == ";" and not code[q:t].strip():
        return t + 1
    if code[t] == "(":
        head = code[q:t]
        m = _LAST_IDENT.search(head)
        pe = match_paren(code, t)
        e = find_term(code, pe + 1, hi, "{;")
        e_end = hi if e < 0 else e
        after = block_end(code, e) if 0 <= e and code[e] == "{" else (e + 1 if e >= 0 else hi)
        if not m or not vis:
            return after
        name = m.group("name")
        ret = _clean(s, q, q + m.start("name"))
        params = re.sub(r"\bfinal\s+", "", _clean(s, t, pe + 1))
        tail = _clean(s, pe + 1, e_end)
        if not ret and name != owner_name:
            return after
        sig = tparams + params + (f": {ret}" if ret else "") + (f" {tail}" if tail else "")
        nodes.append(Node(Kind.METHOD, name if ret else "constructor", sig=sig,
                          doc=doc_before(s, start), attrs=_attrs(None, mods)))
        return after
    # a field: Type a [= v], b [= w];
    e = find_term(code, t, hi, ";")
    end = hi if e < 0 else e
    after = hi if e < 0 else e + 1
    if not vis:
        return after
    segs = split_top(s.nc[q:end])
    head = strip_annotations(segs[0].split("=", 1)[0]).strip()
    dims = _DIMS.search(head)
    ndims = dims.group(0).count("[") if dims else 0
    head = head[:dims.start()] if dims else head
    m = _LAST_IDENT.search(head)
    if not m:
        return after
    base = norm(head[:m.start("name")])
    const = owner in ("interface", "annotation") or ("static" in mods and "final" in mods)
    doc = doc_before(s, start)
    for k, seg in enumerate(segs):
        head_k, eq, val = seg.partition("=")
        nm = m.group("name") if k == 0 else (re.match(rf"\s*({IDENT})", head_k) or [None, None])[1]
        if not nm:
            continue
        d = ndims if k == 0 else head_k.count("[")
        typ = base + "[]" * d
        if const:
            v = _lit(val) if eq else "..."
            nodes.append(Node(Kind.CONSTANT, nm, sig=typ, doc=doc,
                              attrs=tuple(sorted([("value", v), *_attrs(None, mods)]))))
        else:
            nodes.append(Node(Kind.FIELD, nm, sig=typ, doc=doc, attrs=_attrs(None, mods)))
    return after


def _lit(val: str) -> str:
    v = re.sub(r"\s+", " ", val).strip()
    if not v or len(v) > 80:
        return "..."
    if re.fullmatch(r"-?\d[\w.]*|true|false|null|\"[^\"]*\"|'[^']*'", v):
        return v
    return "..."


def _scope(s: Scan, lo: int, hi: int, owner: str, owner_name: str, nodes: list[Node],
           info: dict) -> None:
    code = s.code
    p = lo
    while p < hi:
        p = skip_ws(code, p, hi)
        if p >= hi:
            break
        start = p
        if code[p] == ";":
            p += 1
            continue
        if code[p] == "{":
            p = block_end(code, p)
            continue
        q = skip_annotations(code, p, hi)
        if owner == "top":
            m = _PACKAGE.match(code, q, hi)
            if m:
                info["package"] = re.sub(r"\s+", "", m.group("name"))
                p = m.end()
                continue
            if re.match(r"import\b", code[q:q + 7]):
                e = code.find(";", q, hi)
                p = hi if e < 0 else e + 1
                continue
        mods, q = read_mods(code, q, hi, MODS)
        if q >= hi:
            break
        m = _TYPE.match(code, q, hi)
        if m:
            kw = re.sub(r"\s+", "", m.group("kw"))
            kind = "annotation" if kw == "@interface" else _KIND[kw]
            o = find_term(code, m.end(), hi, "{;")
            if o < 0 or code[o] == ";":
                p = hi if o < 0 else o + 1
                continue
            close = block_end(code, o)
            if (owner == "top" and "public" in mods) or (owner != "top" and _visible(mods, owner)):
                kids: list[Node] = []
                inner = skip_ws(code, o + 1, close - 1)
                if kind == "enum":
                    inner = _enum_constants(s, o + 1, close - 1, kids)
                _scope(s, inner, close - 1, kind, m.group("name"), kids, info)
                nodes.append(Node(Kind.CLASS, m.group("name"), sig=_clean(s, m.end(), o),
                                  doc=doc_before(s, start), attrs=_attrs(kind, mods),
                                  children=tuple(kids)))
            p = close
            continue
        if owner == "top":  # stray top-level text (module-info, broken files): skip a statement
            e = find_term(code, q, hi, ";{")
            p = hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
            continue
        p = _member(s, q, hi, mods, owner, owner_name, start, nodes)


class JavaExtractor:
    name: ClassVar[str] = "java"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(JAVA_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "java")
        nodes: list[Node] = []
        info: dict = {}
        _scope(s, 0, len(s.code), "top", "", nodes, info)
        attrs = [("package", info["package"])] if info.get("package") else []
        return _container(src, "java", s, nodes, attrs)
