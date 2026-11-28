"""Code contracts for PHP (card 1365; no LLM, no deps).

A deterministic scanner (rco.extract.scan, lang "php": only the code between
`<?php` and `?>`, `#` and `//` comments, heredocs, strings with `{$...}`)
masks comments and strings; declarations are read in order. Bodies are
dropped. Each file is one SRCMODULE node (lang php, namespace attr).
Top-level functions and constants are its children, like Go; CLASS (kind:
class, interface, trait, enum), METHOD, FIELD (properties and enum cases)
and CONSTANT (`const`) nest inside.

Public means: a top-level declaration; a member that is `public`,
`protected` or has no visibility (methods and constants are public by
default); every member of an interface. `private` members are not shown.
A declaration inside a top-level `if (...) { }` block counts too, as in a
helpers file that guards each function with `function_exists`.
"""
from __future__ import annotations

import re
from typing import ClassVar

from rco.extract.brace import block_end, find_term, skip_ws
from rco.extract.langs import _container, _text
from rco.extract.scan import Scan, doc_before, match_paren, norm, scan
from rco.model import Kind, Node, SourceFile

PHP_EXT = (".php",)
_ID = r"[A-Za-z_\x80-\uffff][\w\x80-\uffff]*"
_MODS = re.compile(r"(?:public|protected|private|static|abstract|final|readonly|var)(?![\w$])")
_TYPE = re.compile(rf"(?P<kw>class|interface|trait|enum)\s+(?P<name>{_ID})")
_NAMESPACE = re.compile(rf"namespace(?:\s+(?P<name>{_ID}(?:\\{_ID})*))?\s*(?=[;{{])")
_KEEP = ("abstract", "final", "static", "readonly")


def _skip_attrs(code: str, i: int, hi: int) -> int:
    while True:
        i = skip_ws(code, i, hi)
        if code.startswith("#[", i) and i < hi:
            i = match_paren(code, i + 1, "[", "]") + 1
        else:
            return i


def _mods(code: str, i: int, hi: int) -> tuple[list[str], int]:
    out: list[str] = []
    while True:
        i = _skip_attrs(code, i, hi)
        m = _MODS.match(code, i, hi)
        if not m:
            return out, i
        out.append(m.group(0))
        i = skip_ws(code, m.end(), hi)


def _visible(mods: list[str], owner: str) -> bool:
    return "private" not in mods


def _attrs(kind: str | None, mods: list[str], extra: tuple = ()) -> tuple[tuple[str, str], ...]:
    a = [("kind", kind)] if kind else []
    a += [(m, "1") for m in _KEEP if m in mods]
    return tuple(sorted(a + list(extra)))


def _tidy(text: str) -> str:
    return re.sub(r",\s*\)", ")", norm(text))


def _lit(val: str) -> str:
    v = re.sub(r"\s+", " ", val).strip()
    if not v or len(v) > 80:
        return "..."
    if re.fullmatch(r"-?\d[\w.]*|true|false|null|TRUE|FALSE|NULL|\"[^\"]*\"|'[^']*'", v):
        return v
    return "..."


def _function(s: Scan, q: int, hi: int, mods: list[str], owner: str, start: int,
              nodes: list[Node]) -> int:
    code = s.code
    q = skip_ws(code, q + 8, hi)  # after `function`
    if q < hi and code[q] == "&":
        q = skip_ws(code, q + 1, hi)
    m = re.compile(_ID).match(code, q, hi)
    if not m:  # a closure at top level
        e = find_term(code, q, hi, ";{")
        return hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
    t = skip_ws(code, m.end(), hi)
    if t >= hi or code[t] != "(":
        return t
    pe = match_paren(code, t)
    e = find_term(code, pe + 1, hi, "{;")
    end = hi if e < 0 else e
    after = hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
    if _visible(mods, owner):
        sig = _tidy(s.nc[t:end])
        nodes.append(Node(Kind.METHOD, m.group(0), sig=sig, doc=doc_before(s, start),
                          attrs=_attrs(None, mods)))
    return after


def _const(s: Scan, q: int, hi: int, mods: list[str], owner: str, start: int,
           nodes: list[Node]) -> int:
    code = s.code
    e = find_term(code, q + 5, hi, ";")
    end = hi if e < 0 else e
    after = hi if e < 0 else e + 1
    if not _visible(mods, owner):
        return after
    doc = doc_before(s, start)
    for k, seg in enumerate(_split(s.nc[q + 5:end])):
        head, eq, val = seg.partition("=")
        m = re.search(rf"(?P<name>{_ID})\s*$", head)
        if not m:
            continue
        typ = norm(head[:m.start("name")])
        nodes.append(Node(Kind.CONSTANT, m.group("name"), sig=typ, doc=doc,
                          attrs=tuple(sorted([("value", _lit(val) if eq else "..."), *_attrs(None, mods)]))))
    return after


def _split(text: str) -> list[str]:
    out, d, last = [], 0, 0
    for j, ch in enumerate(text):
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d = max(0, d - 1)
        elif ch == "," and d == 0:
            out.append(text[last:j])
            last = j + 1
    out.append(text[last:])
    return out


def _property(s: Scan, q: int, hi: int, mods: list[str], start: int, nodes: list[Node]) -> int:
    code = s.code
    e = find_term(code, q, hi, ";")
    end = hi if e < 0 else e
    after = hi if e < 0 else e + 1
    if "private" in mods:
        return after
    text = s.nc[q:end]
    d = text.find("$")
    typ = norm(text[:d]) if d > 0 else ""
    doc = doc_before(s, start)
    for seg in _split(text[d:] if d >= 0 else ""):
        m = re.match(rf"\s*\$(?P<name>{_ID})", seg)
        if m:
            nodes.append(Node(Kind.FIELD, m.group("name"), sig=typ, doc=doc, attrs=_attrs(None, mods)))
    return after


def _enum_case(s: Scan, q: int, hi: int, start: int, nodes: list[Node]) -> int:
    code = s.code
    e = find_term(code, q + 4, hi, ";")
    after = hi if e < 0 else e + 1
    m = re.compile(rf"\s*(?P<name>{_ID})").match(code, q + 4, hi)
    if m:
        nodes.append(Node(Kind.FIELD, m.group("name"), doc=doc_before(s, start)))
    return after


def _members(s: Scan, lo: int, hi: int, owner: str, nodes: list[Node]) -> None:
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
        q = _skip_attrs(code, p, hi)
        mods, q = _mods(code, q, hi)
        if q >= hi:
            break
        word = re.compile(r"(function|const|case|use)(?![\w$])").match(code, q, hi)
        if word:
            w = word.group(1)
            if w == "function":
                p = _function(s, q, hi, mods, owner, start, nodes)
            elif w == "const":
                p = _const(s, q, hi, mods, owner, start, nodes)
            elif w == "case" and owner == "enum":
                p = _enum_case(s, q, hi, start, nodes)
            else:  # trait use, or a stray `case`
                e = find_term(code, q, hi, ";{")
                p = hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
            p = max(p, start + 1)
            continue
        if q < hi and code[q] == "{":
            p = block_end(code, q)
            continue
        # a property: [type] $name [= v], $other;  (a typed const is handled above)
        p = max(_property(s, q, hi, mods, start, nodes), start + 1)


def _scope(s: Scan, lo: int, hi: int, nodes: list[Node], info: dict) -> None:
    code = s.code
    p = lo
    while p < hi:
        p = skip_ws(code, p, hi)
        if p >= hi:
            break
        start = p
        if code[p] in ";}":
            p += 1
            continue
        q = _skip_attrs(code, p, hi)
        m = _NAMESPACE.match(code, q, hi)
        if m:
            if m.group("name") and "namespace" not in info:
                info["namespace"] = m.group("name")
            p = m.end()
            if p < hi and code[p] == "{":  # namespace X { ... }
                close = block_end(code, p)
                _scope(s, p + 1, close - 1, nodes, info)
                p = close
            else:
                p += 1
            continue
        mods, q = _mods(code, q, hi)
        if q >= hi:
            break
        m = _TYPE.match(code, q, hi)
        if m and not re.match(r"new\s", code[max(0, q - 4):q]):
            kind = m.group("kw")
            o = find_term(code, m.end(), hi, "{;")
            if o < 0 or code[o] == ";":
                p = hi if o < 0 else o + 1
                continue
            close = block_end(code, o)
            kids: list[Node] = []
            _members(s, o + 1, close - 1, kind, kids)
            nodes.append(Node(Kind.CLASS, m.group("name"), sig=_tidy(s.nc[m.end():o]),
                              doc=doc_before(s, start), attrs=_attrs(kind, mods),
                              children=tuple(kids)))
            p = close
            continue
        if re.compile(r"function(?![\w$])").match(code, q, hi):
            p = max(_function(s, q, hi, mods, "top", start, nodes), start + 1)
            continue
        if re.compile(r"const(?![\w$])").match(code, q, hi):
            p = max(_const(s, q, hi, mods, "top", start, nodes), start + 1)
            continue
        if re.compile(r"(?:if|else|elseif)(?![\w$])").match(code, q, hi):
            e = find_term(code, q, hi, "{;")  # a guarded declaration: read inside the block
            if e >= 0 and code[e] == "{":
                close = block_end(code, e)
                _scope(s, e + 1, close - 1, nodes, info)
                p = close
                continue
        e = find_term(code, q, hi, ";{")  # any other statement
        p = hi if e < 0 else (block_end(code, e) if code[e] == "{" else e + 1)
        p = max(p, start + 1)


class PhpExtractor:
    name: ClassVar[str] = "php"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(PHP_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "php")
        nodes: list[Node] = []
        info: dict = {}
        _scope(s, 0, len(s.code), nodes, info)
        attrs = [("namespace", info["namespace"])] if info.get("namespace") else []
        return _container(src, "php", s, nodes, attrs)
