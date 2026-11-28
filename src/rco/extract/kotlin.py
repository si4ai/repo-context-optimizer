"""Code contracts for Kotlin (card 1364; no LLM, no deps).

A deterministic scanner (rco.extract.scan, lang "kotlin": nested block
comments, strings with `${...}` templates, raw strings, backtick names)
masks comments and strings; declarations are read in order. Bodies are
dropped. Each file is one SRCMODULE node (lang kotlin, package attr).
Top-level functions and properties are its children, like Go; CLASS (kind:
class, interface, object, enum, annotation, typealias), METHOD (a
secondary constructor is `constructor`; an extension function is named
`Receiver.name`), FIELD (properties) and CONSTANT (`const val`) nest inside.

Public means: not `private` and not `internal` (Kotlin's default is public).
An interface member is public unless marked private. Every enum entry is shown. A file's package
need not match its folder, so the package line is what counts.
"""
from __future__ import annotations

import re
from typing import ClassVar

from rco.extract.brace import IDENT, block_end, find_term, skip_angles, skip_ws
from rco.extract.langs import _container, _text
from rco.extract.scan import Scan, doc_before, match_paren, norm, scan
from rco.model import Kind, Node, SourceFile

KT_EXT = (".kt",)
MODS = ("public", "private", "protected", "internal", "open", "abstract", "final", "sealed",
        "data", "enum", "annotation", "inner", "value", "inline", "companion", "const",
        "lateinit", "override", "operator", "infix", "suspend", "tailrec", "external",
        "expect", "actual", "vararg", "noinline", "crossinline", "reified")
_KEEP = ("open", "abstract", "sealed", "data", "inner", "value", "inline", "const", "lateinit",
         "override", "operator", "infix", "suspend", "tailrec", "expect", "actual", "companion")
_MOD_RX = re.compile(r"(?:" + "|".join(sorted(MODS, key=len, reverse=True)) + r")(?![\w$])")
_ID = r"(?:[A-Za-z_][\w]*)"
_ANNO = re.compile(rf"@(?:{_ID}:)?{_ID}(?:\.{_ID})*")
_TYPE = re.compile(rf"(?P<kw>class|interface|object|typealias)\b[ \t]*(?P<name>{_ID})?")
_PACKAGE = re.compile(rf"package[ \t]+(?P<name>{_ID}(?:[ \t]*\.[ \t]*{_ID})*)")
_DECL_START = re.compile(
    r"(?:@\w|(?:" + "|".join(sorted(MODS, key=len, reverse=True))
    + r"|fun|val|var|class|interface|object|typealias|companion|import|package)\b(?=[ \t]+[\w@`<(])"
    r"|init\b(?=\s*\{)|constructor\b(?=\s*\())")
_ACCESSOR = re.compile(r"(?:(?:private|protected|internal|public)[ \t]+)?(?:get|set)\b(?=\s*[({=\n]|\s*$)")
_LAST = re.compile(rf"(?P<name>{_ID}|`[^`\n]+`)\s*$")


def _skip_annos(code: str, i: int, hi: int) -> int:
    """Index after annotations at i: @Name, @file:Name, @Name(args), @[A B]."""
    while True:
        i = skip_ws(code, i, hi)
        if i >= hi or code[i] != "@":
            return i
        if code.startswith("@[", i):
            i = match_paren(code, i + 1, "[", "]") + 1
            continue
        m = _ANNO.match(code, i, hi)
        if not m:
            return i
        i = m.end()
        j = i
        while j < hi and code[j] in " \t":
            j += 1
        if j < hi and code[j] == "(":
            i = match_paren(code, j) + 1


def _mods(code: str, i: int, hi: int) -> tuple[list[str], int]:
    out: list[str] = []
    while True:
        i = _skip_annos(code, i, hi)
        m = _MOD_RX.match(code, i, hi)
        # a modifier is followed by whitespace; `data.x` or `open(` is a use
        if not m or (m.end() < hi and code[m.end()] not in " \t\r\n"):
            return out, i
        out.append(m.group(0))
        i = skip_ws(code, m.end(), hi)


def _tidy(text: str) -> str:
    return re.sub(r",\s*\)", ")", norm(text))


def _clean(s: Scan, a: int, b: int) -> str:
    return _tidy(re.sub(r"@(?:\w+:)?\w+(?:\.\w+)*(?:\([^()]*\))?\s*", "", s.nc[a:b]))


def _visible(mods: list[str]) -> bool:
    return "private" not in mods and "internal" not in mods


def _attrs(kind: str | None, mods: list[str], extra: tuple = ()) -> tuple[tuple[str, str], ...]:
    a = [("kind", kind)] if kind else []
    a += [(m, "1") for m in _KEEP if m in mods]
    a += list(extra)
    return tuple(sorted(a))


def _next_tok(code: str, i: int, hi: int) -> int:
    while i < hi and code[i] in " \t\r\n":
        i += 1
    return i


def _expr_end(code: str, i: int, hi: int) -> int:
    """Index after the expression that starts at i: it ends at a newline or `;` outside
    brackets when the next line starts a declaration (or the scope ends)."""
    d = 0
    j = i
    while j < hi:
        ch = code[j]
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d = max(0, d - 1)
        elif d == 0 and ch == ";":
            return j + 1
        elif d == 0 and ch == "\n":
            k = _next_tok(code, j, hi)
            if k >= hi or _DECL_START.match(code, k, hi):
                return j
        j += 1
    return hi


def _sig_end(code: str, i: int, hi: int) -> int:
    """Index of the `{`, `=` or line end that closes a declaration header from i."""
    d = 0
    j = i
    while j < hi:
        ch = code[j]
        if ch in "([<":
            d += 1
        elif ch == ">" and code[j - 1:j] != "-":
            d = max(0, d - 1)
        elif ch in ")]":
            d = max(0, d - 1)
        elif d == 0 and ch in "{=":
            if ch == "=" and code[j + 1:j + 2] == "=":
                j += 2
                continue
            return j
        elif d == 0 and ch == ";":
            return j
        elif d == 0 and ch == "\n":
            k = _next_tok(code, j, hi)
            if k < hi and (code.startswith("where", k) or code[k] in ":,"):
                j = k
                continue
            prev = code[i:j].rstrip()
            if prev.endswith((",", ":", "->")):
                j = k
                continue
            return j
        j += 1
    return hi


def _after_body(code: str, e: int, hi: int) -> int:
    if e >= hi:
        return hi
    if code[e] == "{":
        return block_end(code, e)
    if code[e] == "=":
        return _expr_end(code, e + 1, hi)
    return e + 1 if code[e] == ";" else e


def _enum_entries(s: Scan, lo: int, hi: int, nodes: list[Node]) -> int:
    """Read `A, B(1), C { ... };` from lo; the index after the `;` (or hi)."""
    code, p = s.code, lo
    while True:
        start = p = skip_ws(code, p, hi)
        p = _skip_annos(code, p, hi)
        if p >= hi:
            return hi
        if code[p] == ";":
            return p + 1
        m = re.compile(_ID).match(code, p, hi)
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


def _name_at(s: Scan, text_nc: str) -> str:
    return text_nc.strip().strip("`")


def _function(s: Scan, q: int, hi: int, mods: list[str], start: int, nodes: list[Node]) -> int:
    code = s.code
    q = skip_ws(code, q + 3, hi)  # after `fun`
    tparams = ""
    if q < hi and code[q] == "<":
        r = skip_angles(code, q, hi)
        tparams = _clean(s, q, r)
        q = skip_ws(code, r, hi)
    t = find_term(code, q, hi, "(")
    if t < 0:
        return hi
    pe = match_paren(code, t)
    e = _sig_end(code, pe + 1, hi)
    after = _after_body(code, e, hi)
    head = s.nc[q:t]
    m = re.match(rf"(?s)(?:(?P<recv>.*)\.)?\s*(?P<name>{_ID}|`[^`\n]+`)\s*$", head)
    if not m or not _visible(mods):
        return after
    name = m.group("name").strip("`")
    recv = norm(m.group("recv") or "")
    full = f"{recv}.{name}" if recv else name
    sig = tparams + _tidy(s.nc[t:min(e, hi)])
    nodes.append(Node(Kind.METHOD, full, sig=sig, doc=doc_before(s, start), attrs=_attrs(None, mods)))
    return after


def _property(s: Scan, q: int, hi: int, mods: list[str], start: int, nodes: list[Node],
              owner: str) -> int:
    code = s.code
    m0 = re.match(r"va[lr]\b", code[q:q + 4])
    mutable = code.startswith("var", q)
    q = skip_ws(code, q + 3, hi)
    if q < hi and code[q] == "<":
        q = skip_ws(code, skip_angles(code, q, hi), hi)
    # header: up to `=`, `by`, `;` or the line end
    d, j = 0, q
    stop, kind = hi, ""
    while j < hi:
        ch = code[j]
        if ch in "([<":
            d += 1
        elif ch in ")]" or (ch == ">" and code[j - 1:j] != "-"):
            d = max(0, d - 1)
        elif d == 0 and ch == "=" and code[j + 1:j + 2] != "=":
            stop, kind = j, "="
            break
        elif d == 0 and ch in ";\n":
            stop = j
            break
        elif d == 0 and ch == "b" and re.match(r"by\b", code[j:j + 3]) and code[j - 1:j] in " \t":
            stop, kind = j, "by"
            break
        elif d == 0 and ch in "gs" and code[j - 1:j] in " \t" and re.match(r"(?:get|set)\s*\(", code[j:j + 12]):
            stop = j  # `val x: Int get() = 1`: the accessor follows on the same line
            break
        j += 1
    head = s.nc[q:stop]
    after = stop
    value = ""
    if kind == "=":
        after = _expr_end(code, stop + 1, hi)
        value = s.nc[stop + 1:after]
    elif kind == "by":
        after = _expr_end(code, stop + 2, hi)
    elif stop < hi and code[stop] == ";":
        after = stop + 1
    # accessors on the lines that follow
    while True:
        k = _next_tok(code, after, hi)
        am = _ACCESSOR.match(code, k, hi)
        if not am:
            break
        p = am.end()
        while p < hi and code[p] in " \t":
            p += 1
        if p < hi and code[p] == "(":
            p = match_paren(code, p) + 1
        e = _sig_end(code, p, hi)
        after = _after_body(code, e, hi)
    if not _visible(mods) or head.lstrip().startswith("("):
        return after
    nm, _, typ = head.partition(":")
    nm = nm.strip()
    mm = re.match(rf"(?s)(?:(?P<recv>.*)\.)?\s*(?P<name>{_ID}|`[^`\n]+`)\s*$", nm)
    if not mm:
        return after
    name = mm.group("name").strip("`")
    recv = norm(mm.group("recv") or "")
    full = f"{recv}.{name}" if recv else name
    typ = norm(typ)
    extra = (("var", "1"),) if mutable else ()
    if "const" in mods:
        v = _lit(value) if kind == "=" else "..."
        nodes.append(Node(Kind.CONSTANT, full, sig=typ, doc=doc_before(s, start),
                          attrs=tuple(sorted([("value", v), *_attrs(None, mods, extra)]))))
    else:
        nodes.append(Node(Kind.FIELD, full, sig=typ, doc=doc_before(s, start),
                          attrs=_attrs(None, mods, extra)))
    del m0, owner
    return after


def _lit(val: str) -> str:
    v = re.sub(r"\s+", " ", val).strip()
    if not v or len(v) > 80:
        return "..."
    if re.fullmatch(r"-?\d[\w.]*|true|false|null|\"[^\"]*\"|'[^']*'", v):
        return v
    return "..."


def _scope(s: Scan, lo: int, hi: int, owner: str, nodes: list[Node], info: dict) -> None:
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
        q = _skip_annos(code, p, hi)
        if owner == "top":
            m = _PACKAGE.match(code, q, hi)
            if m:
                info["package"] = re.sub(r"\s+", "", m.group("name"))
                p = m.end()
                continue
            if re.match(r"import\b", code[q:q + 7]):
                e = code.find("\n", q, hi)
                p = hi if e < 0 else e
                continue
        mods, q = _mods(code, q, hi)
        if q >= hi:
            break
        fi = re.match(r"fun[ \t]+interface\b", code[q:q + 20])
        if fi:
            q += fi.end() - len("interface")
        m = _TYPE.match(code, q, hi)
        if m and (m.group("name") or m.group("kw") == "object"):
            kw = m.group("kw")
            kind = kw
            if "enum" in mods and kw == "class":
                kind = "enum"
            elif "annotation" in mods and kw == "class":
                kind = "annotation"
            name = m.group("name") or "Companion"
            if kw == "typealias":
                e = _sig_end(code, m.end(), hi) if False else _expr_end(code, m.end(), hi)
                if _visible(mods):
                    nodes.append(Node(Kind.CLASS, name, sig=_clean(s, m.end(), e), doc=doc_before(s, start),
                                      attrs=_attrs("typealias", mods)))
                p = e
                continue
            o = _sig_end(code, m.end(), hi)
            body = o < hi and code[o] == "{"
            close = block_end(code, o) if body else o
            if _visible(mods):
                kids: list[Node] = []
                if body:
                    inner = skip_ws(code, o + 1, close - 1)
                    if kind == "enum":
                        inner = _enum_entries(s, o + 1, close - 1, kids)
                    _scope(s, inner, close - 1, kind if kind != "object" else "class", kids, info)
                extra = (("fun", "1"),) if fi else ()
                nodes.append(Node(Kind.CLASS, name, sig=_clean(s, m.end() if m.group("name") else m.end(), o),
                                  doc=doc_before(s, start), attrs=_attrs(kind, mods, extra),
                                  children=tuple(kids)))
            p = max(close, p + 1)
            continue
        word = re.match(r"(fun|val|var|init|constructor)\b", code[q:q + 12])
        if word:
            w = word.group(1)
            if w == "fun":
                p = _function(s, q, hi, mods, start, nodes)
            elif w in ("val", "var"):
                p = _property(s, q, hi, mods, start, nodes, owner)
            elif w == "init":
                b = skip_ws(code, q + 4, hi)
                p = block_end(code, b) if b < hi and code[b] == "{" else _expr_end(code, q, hi)
            else:  # secondary constructor
                t = skip_ws(code, q + 11, hi)
                if t < hi and code[t] == "(":
                    pe = match_paren(code, t)
                    e = _sig_end(code, pe + 1, hi)
                    after = _after_body(code, e, hi)
                    if _visible(mods) and owner != "top":
                        nodes.append(Node(Kind.METHOD, "constructor", sig=_clean(s, t, pe + 1),
                                          doc=doc_before(s, start), attrs=_attrs(None, mods)))
                    p = after
                else:
                    p = _expr_end(code, q, hi)
            p = max(p, start + 1)
            continue
        p = max(_expr_end(code, q, hi), start + 1)  # a statement, a script line or odd text


class KotlinExtractor:
    name: ClassVar[str] = "kotlin"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(KT_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "kotlin")
        nodes: list[Node] = []
        info: dict = {}
        _scope(s, 0, len(s.code), "top", nodes, info)
        attrs = [("package", info["package"])] if info.get("package") else []
        return _container(src, "kotlin", s, nodes, attrs)
