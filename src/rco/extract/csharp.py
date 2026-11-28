"""Code contracts for C# (card 1363; no LLM, no deps).

The same deterministic scanner as Java (rco.extract.scan, lang "csharp":
it also masks verbatim strings and raw strings) and the brace helpers in
rco.extract.brace. Declarations are read in order, so attributes,
modifiers and one-line declarations work. Bodies are dropped. Each file is
one SRCMODULE node (lang csharp, namespace attr); namespaces are not nodes,
their types are children of the file. Children: CLASS (kind: class, struct,
interface, enum, record, delegate), METHOD (a constructor is `constructor`,
an operator is `operator +`, an indexer is `this`), FIELD (fields, events,
properties: a property has get / set / init attrs), CONSTANT (const, and
static readonly).

Public means: a `public` top-level type; a member that is `public` or
`protected` (not `private protected`); every member of an interface that is
not `private`; every enum member. `internal` and `private` are not shown.
"""
from __future__ import annotations

import re
from typing import ClassVar

from rco.extract.brace import (IDENT, block_end, find_term, find_term_generic, read_mods,
                               skip_angles, skip_brackets, skip_ws, split_top,
                               strip_bracket_attrs)
from rco.extract.langs import _container, _text
from rco.extract.scan import Scan, doc_before, match_paren, norm, scan
from rco.model import Kind, Node, SourceFile

CS_EXT = (".cs",)
MODS = ("public", "protected", "internal", "private", "static", "abstract", "sealed", "virtual",
        "override", "readonly", "const", "event", "async", "partial", "extern", "unsafe", "new",
        "volatile", "required", "file", "ref", "fixed", "implicit", "explicit")
_TYPE = re.compile(rf"(?P<kw>class|struct|interface|enum|record(?:\s+(?:class|struct))?"
                   rf"(?=\s+{IDENT}\s*[(<{{:;]))\s+(?P<name>{IDENT})")
_DELEGATE = re.compile(r"delegate\b")
_NS = re.compile(rf"namespace\s+(?P<name>{IDENT}(?:\s*\.\s*{IDENT})*)\s*(?P<end>[{{;])")
_LAST_IDENT = re.compile(rf"(?P<name>{IDENT})\s*(?:<[^<>]*(?:<[^<>]*>[^<>]*)*>)?\s*$")
_OPERATOR = re.compile(r"(?P<ret>.*?)\boperator\s*(?P<sym>[^\s(]+)\s*$", re.S)
_KEEP = ("abstract", "sealed", "static", "virtual", "override", "readonly", "event", "implicit",
         "explicit", "partial")
_ACCESS = re.compile(r"(?P<mods>(?:(?:private|protected|internal)\s+)*)(?P<kw>get|set|init)\b")


def _attrs(kind: str | None, mods: list[str], extra: tuple[tuple[str, str], ...] = ()):
    a = [("kind", kind)] if kind else []
    a += [(m, "1") for m in _KEEP if m in mods]
    return tuple(sorted(a + list(extra)))


def _clean(s: Scan, a: int, b: int) -> str:
    return norm(strip_bracket_attrs(s.nc[a:b]))


def _visible(mods: list[str], owner: str) -> bool:
    if "private" in mods:
        return False
    if owner in ("interface", "enum"):
        return True
    return "public" in mods or "protected" in mods


def _split_commas(code: str) -> list[tuple[int, int]]:
    """(start, end) of the comma separated parts of code, brackets respected."""
    out, d, last = [], 0, 0
    for j, ch in enumerate(code):
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
        elif ch == "," and d == 0:
            out.append((last, j))
            last = j + 1
    out.append((last, len(code)))
    return out


def _enum_members(s: Scan, lo: int, hi: int, nodes: list[Node]) -> None:
    for a, b in _split_commas(s.code[lo:hi]):
        a, b = lo + a, lo + b
        start = skip_ws(s.code, a, b)
        q = skip_brackets(s.code, start, b)
        m = re.compile(IDENT).match(s.code, q, b)
        if m:
            nodes.append(Node(Kind.FIELD, m.group(0), doc=doc_before(s, start)))


def _accessors(s: Scan, o: int, close: int) -> tuple[tuple[str, str], ...]:
    """get / set / init accessors that are not private, from the { } of a property."""
    code, found, p = s.code, set(), o + 1
    while p < close:
        p = skip_brackets(code, skip_ws(code, p, close), close)
        m = _ACCESS.match(code, p, close)
        if m:
            if "private" not in m.group("mods"):
                found.add(m.group("kw"))
            e = find_term(code, m.end(), close, "{;")
            p = close if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
            continue
        e = find_term(code, p, close, ";{")
        p = close if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
    return tuple((k, "1") for k in sorted(found))


def _member(s: Scan, q: int, hi: int, mods: list[str], owner: str, owner_name: str,
            start: int, nodes: list[Node]) -> int:
    code = s.code
    vis = _visible(mods, owner)
    if "private" in mods and "protected" in mods:
        vis = False
    if _DELEGATE.match(code, q, hi):
        e = find_term(code, q, hi, ";")
        end = hi if e < 0 else e
        if vis:
            m = _LAST_IDENT.search(code[q:code.find("(", q, end) if code.find("(", q, end) >= 0 else end])
            po = code.find("(", q, end)
            if m and po >= 0:
                name = m.group("name")
                ret = _clean(s, q + len("delegate"), q + m.start("name"))
                pe = match_paren(code, po)
                tail = _clean(s, pe + 1, end)
                nodes.append(Node(Kind.CLASS, name, doc=doc_before(s, start),
                                  sig=_clean(s, q + m.end(), po)[:0] + _clean(s, po, pe + 1)
                                  + (f": {ret}" if ret else "") + (f" {tail}" if tail else ""),
                                  attrs=(("kind", "delegate"),)))
        return hi if e < 0 else e + 1
    tparams = ""
    t0 = q
    if code[q] == "(":  # a tuple return type
        t0 = match_paren(code, q) + 1
    t = find_term_generic(code, t0, hi, "(={;")
    if t < 0:
        return hi
    if code[t] == ";" and not code[q:t].strip():
        return t + 1
    if code[t] == "{" and not code[q:t].strip():  # initializer-like block: skip
        return block_end(code, t)
    expr_body = code[t:t + 2] == "=>"
    if code[t] == "(":
        head = code[q:t]
        om = _OPERATOR.match(head)
        m = _LAST_IDENT.search(head)
        pe = match_paren(code, t)
        e = find_term(code, pe + 1, hi, "{;=")
        if e >= 0 and code[e] == "=" and code[e:e + 2] != "=>":
            e = find_term(code, e, hi, "{;")  # `where T : ...` never has =, but a default might
        e_end = hi if e < 0 else e
        if e >= 0 and code[e:e + 2] == "=>":
            semi = find_term(code, e + 2, hi, ";")
            after = hi if semi < 0 else semi + 1
        elif e >= 0 and code[e] == "{":
            after = block_end(code, e)
        else:
            after = hi if e < 0 else e + 1
        if not vis:
            return after
        params = _clean(s, t, pe + 1)
        tail = _clean(s, pe + 1, e_end)
        if om:
            name = "operator " + om.group("sym")
            ret = _clean(s, q, q + om.end("ret")) if om.group("ret").strip() else ""
            ret = ret if ret not in ("implicit", "explicit") else ret
            nodes.append(Node(Kind.METHOD, name, sig=params + (f": {ret}" if ret else ""),
                              doc=doc_before(s, start), attrs=_attrs(None, mods)))
            return after
        if not m:
            return after
        name = m.group("name")
        gm = re.search(r"<[^<>]*(?:<[^<>]*>[^<>]*)*>\s*$", head)
        tparams = _clean(s, q + gm.start(), q + gm.end()) if gm else ""
        retend = gm.start() if gm else m.start("name")
        ret = _clean(s, q, q + m.start("name")) if not gm else _clean(s, q, q + m.start("name"))
        if "." in head[max(0, m.start("name") - 1):m.start("name")]:
            return after  # explicit interface implementation
        if not ret and name != owner_name:
            return after
        tail = "" if not ret else tail
        sig = tparams + params + (f": {ret}" if ret else "") + (f" {tail}" if tail else "")
        nodes.append(Node(Kind.METHOD, name if ret else "constructor", sig=sig,
                          doc=doc_before(s, start), attrs=_attrs(None, mods)))
        del retend
        return after
    if code[t] == "{" or expr_body:  # property, indexer, or event with accessors
        head = code[q:t]
        if expr_body:
            e = find_term(code, t + 2, hi, ";")
            after = hi if e < 0 else e + 1
            acc = (("get", "1"),)
        else:
            after = block_end(code, t)
            acc = _accessors(s, t, after - 1)
            nxt = skip_ws(code, after, hi)
            if nxt < hi and code[nxt] == "=":  # = initial value;
                e = find_term(code, nxt, hi, ";")
                after = hi if e < 0 else e + 1
        if not vis:
            return after
        idx = re.search(r"\bthis\s*\[", head)
        if idx:
            typ = _clean(s, q, q + idx.start())
            b0 = q + idx.end() - 1
            b1 = match_paren(code, b0, "[", "]")
            nodes.append(Node(Kind.FIELD, "this", sig=f"{norm(s.nc[b0:b1 + 1])}: {typ}",
                              doc=doc_before(s, start), attrs=_attrs(None, mods, acc)))
            return after
        m = _LAST_IDENT.search(head)
        if not m or "." in head[max(0, m.start("name") - 1):m.start("name")]:
            return after
        typ = _clean(s, q, q + m.start("name"))
        nodes.append(Node(Kind.FIELD, m.group("name"), sig=typ, doc=doc_before(s, start),
                          attrs=_attrs(None, mods, acc)))
        return after
    # a field or constant: Type a [= v], b [= w];
    e = find_term(code, t, hi, ";")
    end = hi if e < 0 else e
    after = hi if e < 0 else e + 1
    if not vis:
        return after
    segs = split_top(s.nc[q:end])
    head = strip_bracket_attrs(segs[0].split("=", 1)[0]).strip()
    m = _LAST_IDENT.search(head)
    if not m:
        return after
    base = norm(head[:m.start("name")])
    const = "const" in mods or ("static" in mods and "readonly" in mods)
    doc = doc_before(s, start)
    for k, seg in enumerate(segs):
        head_k, eq, val = seg.partition("=")
        nm = m.group("name") if k == 0 else (re.match(rf"\s*({IDENT})", head_k) or [None, None])[1]
        if not nm:
            continue
        if const:
            nodes.append(Node(Kind.CONSTANT, nm, sig=base, doc=doc,
                              attrs=tuple(sorted([("value", _lit(val) if eq else "..."),
                                                  *_attrs(None, mods)]))))
        else:
            nodes.append(Node(Kind.FIELD, nm, sig=base, doc=doc, attrs=_attrs(None, mods)))
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
        if code[p] == "#":  # preprocessor line
            e = code.find("\n", p, hi)
            p = hi if e < 0 else e + 1
            continue
        q = skip_brackets(code, p, hi)
        if q >= hi:
            break
        if owner == "top":
            m = _NS.match(code, q, hi)
            if m:
                info.setdefault("namespace", re.sub(r"\s+", "", m.group("name")))
                if m.group("end") == ";":  # file-scoped: the rest of the file is its scope
                    p = m.end()
                    continue
                close = block_end(code, m.end() - 1)
                _scope(s, m.end(), close - 1, "top", "", nodes, info)
                p = close
                continue
            if re.match(r"(?:global\s+)?using\b|extern\s+alias\b", code[q:q + 14]):
                e = code.find(";", q, hi)
                p = hi if e < 0 else e + 1
                continue
        mods, q = read_mods(code, q, hi, MODS, bracket=True)
        if q >= hi:
            break
        m = _TYPE.match(code, q, hi)
        if m:
            kw = re.sub(r"\s+", " ", m.group("kw"))
            kind = "record" if kw.startswith("record") else kw
            o = find_term_generic(code, m.end(), hi, "{;")
            if o < 0:
                p = hi
                continue
            close = hi if code[o] == ";" else block_end(code, o)
            if code[o] == ";":
                close = o + 1
            if (owner == "top" and "public" in mods) or (owner != "top" and _visible(mods, owner)):
                kids: list[Node] = []
                if code[o] == "{":
                    if kind == "enum":
                        _enum_members(s, o + 1, close - 1, kids)
                    else:
                        _scope(s, o + 1, close - 1, kind, m.group("name"), kids, info)
                nodes.append(Node(Kind.CLASS, m.group("name"), sig=_clean(s, m.end(), o),
                                  doc=doc_before(s, start), attrs=_attrs(kind, mods),
                                  children=tuple(kids)))
            p = close
            continue
        if owner == "top" and not _DELEGATE.match(code, q, hi):  # top-level statements: skip one
            e = find_term(code, q, hi, ";{")
            p = hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
            continue
        if owner == "top":
            vis_top = "public" in mods
            if not vis_top:
                e = find_term(code, q, hi, ";")
                p = hi if e < 0 else e + 1
                continue
        p = _member(s, q, hi, mods, owner, owner_name, start, nodes)


class CSharpExtractor:
    name: ClassVar[str] = "csharp"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(CS_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "csharp")
        nodes: list[Node] = []
        info: dict = {}
        _scope(s, 0, len(s.code), "top", "", nodes, info)
        attrs = [("namespace", info["namespace"])] if info.get("namespace") else []
        return _container(src, "csharp", s, nodes, attrs)
