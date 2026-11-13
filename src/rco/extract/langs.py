"""Code contracts for JavaScript/TypeScript, Go, Rust and shell (no LLM, no deps).

A deterministic scanner (rco.extract.scan) masks comments and strings and
tracks brace depth; declarations are read at the top level only (plus the
body of a top-level IIFE in JS scripts). Bodies are dropped. Each file is
one SRCMODULE node with a lang attr; its children follow the Python
extractor's kinds: CLASS (with a kind attr: class, interface, type, enum,
struct, trait, impl, alias), FUNCTION, METHOD, FIELD, CONSTANT, EXPORT.

Public means: JS/TS modules, exported names (scripts: every top-level
name not starting with _); Go, a capitalized name; Rust, `pub`; shell,
every function. The doc is the first line of the comment right above a
declaration, or of the file's leading comment for the SRCMODULE.
"""
from __future__ import annotations

import json
import re
from pathlib import PurePosixPath
from typing import ClassVar

from rco.extract.scan import (Scan, doc_before, find_open, header_doc, line_starts,
                              match_brace, match_paren, norm, scan)
from rco.model import Kind, Node, SourceFile

VALUE_MAX = 80
ID = r"[A-Za-z_$][\w$]*"

JS_EXT = (".js", ".mjs", ".cjs", ".jsx", ".ts", ".mts", ".cts", ".tsx")
GO_EXT = (".go",)
RUST_EXT = (".rs",)
SH_EXT = (".sh", ".bash", ".ps1")


def _text(src: SourceFile) -> str:
    return src.data.decode("utf-8-sig", errors="replace")


def _value(s: Scan, a: int, b: int, bare: bool = False) -> str:
    v = s.nc[a:b].strip().rstrip(";,").strip()
    v = re.sub(r"\s+", " ", v)
    if not v or len(v) > VALUE_MAX or "\n" in v:
        return "..."
    if bare and re.fullmatch(r"[\w./:@%+,=-]+", v):
        return v
    if re.fullmatch(r"-?\d[\w.]*|true|false|null|nil|None|undefined|\"[^\"]*\"|'[^']*'|`[^`$]*`", v):
        return v
    return "..."


def _container(src: SourceFile, lang: str, s: Scan, kids: list[Node],
               attrs: list[tuple[str, str]] | None = None) -> list[Node]:
    a = [("lang", lang)] + list(attrs or [])
    return [Node(Kind.SRCMODULE, src.path, src.path, doc=header_doc(s),
                 attrs=tuple(sorted(a)), children=tuple(kids))]


def _merge(nodes: list[Node]) -> list[Node]:
    """Merge CLASS nodes of the same name (Go methods, Rust impl blocks)."""
    out: list[Node] = []
    idx: dict[str, int] = {}
    for n in nodes:
        if n.kind is Kind.CLASS and n.name in idx:
            i = idx[n.name]
            old = out[i]
            base = old if dict(old.attrs).get("kind") != "impl" else n
            other = n if base is old else old
            attrs = dict(base.attrs)
            for k, v in other.attrs:
                if k == "trait":
                    attrs[k] = ", ".join(sorted(set(filter(None, (attrs.get(k, "") + ", " + v)
                                                           .split(", ")))))
            out[i] = Node(Kind.CLASS, base.name, sig=base.sig, doc=base.doc or other.doc,
                          attrs=tuple(sorted(attrs.items())),
                          children=base.children + other.children)
        else:
            if n.kind is Kind.CLASS:
                idx[n.name] = len(out)
            out.append(n)
    return out


# ------------------------------------------------------------------ JS / TS

_JS_FUNC = re.compile(rf"(?P<exp>export\s+(?:default\s+)?)?(?:declare\s+)?(?P<async>async\s+)?"
                      rf"function\b\s*\*?\s*(?P<name>{ID})\s*")
_JS_CLASS = re.compile(rf"(?P<exp>export\s+(?:default\s+)?)?(?:declare\s+)?(?:abstract\s+)?"
                       rf"class\s+(?P<name>{ID})")
_JS_TYPE = re.compile(rf"(?P<exp>export\s+)?(?:declare\s+)?(?:const\s+)?"
                      rf"(?P<kw>interface|type|enum)\s+(?P<name>{ID})")
_JS_VAR = re.compile(rf"(?P<exp>export\s+)?(?:declare\s+)?(?:const|let|var)\s+(?P<name>{ID})"
                     rf"\s*(?::[^=;]+)?=\s*")
_JS_ARROW = re.compile(rf"(?P<async>async\s+)?(?:function\b\s*\*?\s*(?:{ID})?\s*(?=\()|(?=\()|"
                       rf"(?P<one>{ID})\s*=>)")
_JS_EXPORT_LIST = re.compile(r"export\s*(?:type\s*)?\{(?P<names>[^}]*)\}")
_JS_EXPORT_DEFAULT = re.compile(rf"export\s+default\s+(?P<name>{ID})\s*;?\s*$", re.M)
_JS_GLOBAL = re.compile(rf"(?:module\.exports|exports|global|globalThis|window|self|root)"
                        rf"(?:\.(?P<name>{ID}))?\s*=(?!=)")
_JS_IIFE = re.compile(r"[;!+~]?\s*\(?\s*(?:async\s+)?(?:function\b[^({]*\([^)]*\)|\([^)]*\)\s*=>"
                      r"|[A-Za-z_$][\w$]*\s*=>)\s*\{")
_JS_METHOD = re.compile(rf"(?P<mods>(?:(?:static|async|get|set|public|private|protected|readonly|"
                        rf"override|abstract|declare)\s+)*)(?P<star>\*\s*)?(?P<name>#?{ID})\s*"
                        rf"(?P<q>\?)?\s*(?=[(<])")
_JS_FIELD = re.compile(rf"(?P<mods>(?:(?:static|public|private|protected|readonly|declare)\s+)*)"
                       rf"(?P<name>{ID})\s*(?P<q>[?!])?\s*(?::(?P<type>[^=;]+))?(?:=|;|$)")
_JS_KW = {"if", "for", "while", "switch", "catch", "function", "return", "with", "do", "else",
          "new", "typeof", "await", "yield", "super", "this", "constructor_"}


def _js_sig(s: Scan, a: int, open_at: int) -> str:
    return norm(s.nc[a:open_at])


def _js_params_sig(s: Scan, a: int) -> tuple[str, int]:
    """Signature from a ( or < at a to the body {; returns (sig, body_open or -1)."""
    o = find_open(s.code, a, stops=";")
    if o < 0:
        end = s.code.find(";", a)
        end = len(s.code) if end < 0 else end
        return norm(s.nc[a:end]), -1
    return norm(s.nc[a:o]), o


def _js_class_members(s: Scan, o: int, close: int, ts: bool) -> list[Node]:
    kids: list[Node] = []
    d = s.depth_at(o) + 1
    for k in line_starts(s, o + 1, close, d):
        line_end = s.code.find("\n", k)
        line_end = close if line_end < 0 or line_end > close else line_end
        if s.code[k] in "}@;":
            continue
        m = _JS_METHOD.match(s.code, k, line_end)
        if m and m.group("name") not in _JS_KW:
            name = m.group("name")
            mods = m.group("mods").split()
            if name.startswith(("#", "_")) or "private" in mods or "protected" in mods:
                continue
            sig, _ = _js_params_sig(s, m.end())
            attrs = []
            if "async" in mods:
                attrs.append(("async", "1"))
            if "static" in mods:
                attrs.append(("static", "1"))
            for acc in ("get", "set"):
                if acc in mods:
                    attrs.append(("accessor", acc))
            kids.append(Node(Kind.METHOD, name, sig=sig, doc=doc_before(s, k),
                             attrs=tuple(sorted(attrs))))
            continue
        f = _JS_FIELD.match(s.code, k, line_end)
        if f and f.group("name") not in _JS_KW and ts and f.group("type"):
            mods = f.group("mods").split()
            name = f.group("name")
            if name.startswith("_") or "private" in mods or "protected" in mods:
                continue
            kids.append(Node(Kind.FIELD, name, sig=norm(s.nc[f.start("type"):f.end("type")]),
                             doc=doc_before(s, k),
                             attrs=(("static", "1"),) if "static" in mods else ()))
    return kids


def _js_scope(s: Scan, lo: int, hi: int, depth: int, module: bool, ts: bool,
              exports: set[str], nodes: list[Node], iife_ok: bool) -> None:
    for k in line_starts(s, lo, hi, depth):
        line_end = s.code.find("\n", k)
        line_end = hi if line_end < 0 or line_end > hi else line_end
        m = _JS_FUNC.match(s.code, k)
        if m:
            name = m.group("name")
            public = (bool(m.group("exp")) if module else True) and not name.startswith("_")
            sig, _ = _js_params_sig(s, m.end())
            if public:
                attrs = [("async", "1")] if m.group("async") else []
                nodes.append(Node(Kind.FUNCTION, name, sig=sig, doc=doc_before(s, k),
                                  attrs=tuple(attrs)))
            continue
        m = _JS_CLASS.match(s.code, k)
        if m:
            name = m.group("name")
            o = find_open(s.code, m.end(), stops=";")
            if o < 0:
                continue
            public = (bool(m.group("exp")) if module else True) and not name.startswith("_")
            if public:
                close = match_brace(s.code, o)
                nodes.append(Node(Kind.CLASS, name, sig=norm(s.nc[m.end():o]),
                                  doc=doc_before(s, k), attrs=(("kind", "class"),),
                                  children=tuple(_js_class_members(s, o, close, ts))))
            continue
        m = _JS_TYPE.match(s.code, k) if ts else None
        if m:
            name, kw = m.group("name"), m.group("kw")
            public = (bool(m.group("exp")) if module else True) and not name.startswith("_")
            if not public:
                continue
            if kw == "type":
                eq = s.code.find("=", m.end())
                end = _stmt_end(s, eq + 1) if eq >= 0 else line_end
                nodes.append(Node(Kind.CLASS, name, sig=norm(s.nc[m.end():end].rstrip(";")),
                                  doc=doc_before(s, k), attrs=(("kind", "type"),)))
                continue
            o = find_open(s.code, m.end(), stops=";")
            if o < 0:
                continue
            close = match_brace(s.code, o)
            kids = []
            if kw == "interface":
                kids = _js_class_members(s, o, close, ts=True)
            else:
                body = s.code[o + 1:close]
                for v in re.finditer(rf"({ID})\s*(?:=[^,}}]*)?(?:,|$)", body):
                    kids.append(Node(Kind.FIELD, v.group(1)))
            nodes.append(Node(Kind.CLASS, name, sig=norm(s.nc[m.end():o]),
                              doc=doc_before(s, k), attrs=(("kind", kw),),
                              children=tuple(kids)))
            continue
        m = _JS_VAR.match(s.code, k)
        if m:
            name = m.group("name")
            public = (bool(m.group("exp")) if module else True) and not name.startswith("_")
            a = _JS_ARROW.match(s.code, m.end())
            if a and public:
                if a.group("one"):
                    sig = f"({a.group('one')})"
                else:
                    p = s.code.find("(", m.end())
                    pe = match_paren(s.code, p)
                    rest = s.code[pe + 1:pe + 200]
                    ret = re.match(r"\s*:\s*([^=]+?)\s*=>", rest)
                    sig = norm(s.nc[p:pe + 1]) + (f": {norm(ret.group(1))}" if ret else "")
                attrs = [("async", "1")] if a.group("async") else []
                nodes.append(Node(Kind.FUNCTION, name, sig=sig, doc=doc_before(s, k),
                                  attrs=tuple(attrs)))
            elif public and (name.isupper() or (module and m.group("exp"))):
                end = _stmt_end(s, m.end())
                val = _value(s, m.end(), end)
                nodes.append(Node(Kind.CONSTANT, name, doc=doc_before(s, k),
                                  attrs=(("value", val),)))
            continue
        m = _JS_EXPORT_LIST.match(s.code, k)
        if m:
            for part in s.code[m.start("names"):m.end("names")].split(","):
                bits = part.split()
                if bits:
                    exports.add(bits[-1] if len(bits) >= 3 and bits[-2] == "as" else bits[0])
            continue
        m = _JS_EXPORT_DEFAULT.match(s.code, k)
        if m:
            exports.add("default")
            continue
        m = _JS_GLOBAL.match(s.code, k)
        if m:
            if m.group("name"):
                exports.add(m.group("name"))
            else:
                o = s.code.find("{", m.end(), line_end + 1)
                if o >= 0 and not s.code[m.end():o].strip():
                    close = match_brace(s.code, o)
                    body = s.code[o + 1:close]
                    inner_d = s.depth_at(o) + 1
                    for km in re.finditer(rf"(?:^|,|\{{)\s*({ID})\s*(?=[:,}}(]|$)", body, re.M):
                        if s.depth_at(o + 1 + km.start(1)) == inner_d:
                            exports.add(km.group(1))
            continue
        if iife_ok and not module:
            m = _JS_IIFE.match(s.code, k)
            if m:
                o = m.end() - 1
                close = match_brace(s.code, o)
                _js_scope(s, o + 1, close, s.depth_at(o) + 1, module, ts, exports, nodes,
                          iife_ok=False)


def _stmt_end(s: Scan, i: int) -> int:
    """End of a statement starting at i: the ; or newline at bracket depth 0."""
    d = 0
    for j in range(i, len(s.code)):
        ch = s.code[j]
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
            if d < 0:
                return j
        elif d == 0 and ch in ";\n":
            if ch == "\n" and j + 1 < len(s.code) and s.code[j + 1:].lstrip(" \t")[:1] in ".?:|&+":
                continue
            return j
    return len(s.code)


_JS_MODULE = re.compile(r"^\s*(?:import\s*[\w{*'\"(]|export\b)", re.M)


class JsExtractor:
    name: ClassVar[str] = "js"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(JS_EXT) and not path.endswith(".d.ts.map")

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "js")
        ts = src.path.endswith((".ts", ".mts", ".cts", ".tsx"))
        module = bool(_JS_MODULE.search(s.code)) or src.path.endswith((".mjs", ".mts"))
        nodes: list[Node] = []
        exports: set[str] = set()
        _js_scope(s, 0, len(s.code), 0, module, ts, exports, nodes, iife_ok=True)
        if exports:
            nodes.append(Node(Kind.EXPORT, "exports", sig=json.dumps(sorted(exports))))
        attrs = [("module", "esm" if module else "script")]
        return _container(src, "ts" if ts else "js", s, _merge(nodes), attrs)


# ------------------------------------------------------------------ Go

_GO_PKG = re.compile(r"package\s+(\w+)")
_GO_FUNC = re.compile(r"func\s+(?P<name>\w+)\s*")
_GO_METHOD = re.compile(r"func\s*\(\s*(?:\w+\s+)?\*?\s*(?P<recv>\w+)(?:\[[^\]]*\])?\s*\)\s*"
                        r"(?P<name>\w+)\s*")
_GO_TYPE = re.compile(r"type\s+(?P<name>\w+)\s*(?P<tp>\[[^\]]*\])?\s*(?P<eq>=\s*)?(?P<rest>.*)")
_GO_GROUP = re.compile(r"(?P<kw>const|var|type)\s*\(")
_GO_VALUE = re.compile(r"(?P<kw>const|var)\s+(?P<names>\w+(?:\s*,\s*\w+)*)\s*(?P<rest>.*)")


def _go_public(name: str) -> bool:
    return name[:1].isupper()


def _go_func_sig(s: Scan, at: int) -> str:
    o = find_open(s.code, at, stops="\n")
    end = o if o >= 0 else s.code.find("\n", at)
    end = len(s.code) if end < 0 else end
    return norm(s.nc[at:end])


def _go_struct_fields(s: Scan, o: int, close: int, iface: bool) -> list[Node]:
    kids: list[Node] = []
    d = s.depth_at(o) + 1
    for k in line_starts(s, o + 1, close, d):
        le = s.code.find("\n", k)
        le = close if le < 0 or le > close else le
        line = s.nc[k:le].strip()
        if not line or line.startswith("}"):
            continue
        if iface:
            m = re.match(r"(\w+)\s*(\(.*)", line)
            if m and _go_public(m.group(1)):
                kids.append(Node(Kind.METHOD, m.group(1), sig=norm(m.group(2)),
                                 doc=doc_before(s, k)))
            elif re.fullmatch(r"[\w.*]+", line):
                kids.append(Node(Kind.FIELD, line, attrs=(("embedded", "1"),)))
            continue
        line = re.sub(r"`[^`]*`\s*$", "", line).strip()
        m = re.match(r"(\w+(?:\s*,\s*\w+)*)\s+(.+)", line)
        if m:
            for nm in (x.strip() for x in m.group(1).split(",")):
                if _go_public(nm):
                    kids.append(Node(Kind.FIELD, nm, sig=norm(m.group(2)), doc=doc_before(s, k)))
        elif re.fullmatch(r"\*?[\w.]+", line):
            nm = line.lstrip("*").rsplit(".", 1)[-1]
            if _go_public(nm):
                kids.append(Node(Kind.FIELD, nm, sig=line, attrs=(("embedded", "1"),)))
    return kids


def _go_type(s: Scan, k: int, name: str, tp: str, eq: bool, rest_at: int) -> Node | None:
    if not _go_public(name):
        return None
    rest = s.code[rest_at:s.code.find("\n", rest_at) if "\n" in s.code[rest_at:] else len(s.code)]
    m = re.match(r"\s*(struct|interface)\s*\{", rest)
    tps = norm(tp or "")
    if m and not eq:
        o = rest_at + rest.index("{")
        close = match_brace(s.code, o)
        kind = m.group(1)
        return Node(Kind.CLASS, name, sig=tps, doc=doc_before(s, k), attrs=(("kind", kind),),
                    children=tuple(_go_struct_fields(s, o, close, kind == "interface")))
    end = _stmt_end(s, rest_at)
    return Node(Kind.CLASS, name, sig=norm(tps + (" = " if eq else " ") + s.nc[rest_at:end]),
                doc=doc_before(s, k), attrs=(("kind", "alias" if eq else "type"),))


class GoExtractor:
    name: ClassVar[str] = "go"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(GO_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "go")
        nodes: list[Node] = []
        methods: dict[str, list[Node]] = {}
        pkg = ""
        for k in line_starts(s, 0, len(s.code), 0):
            if not pkg:
                m = _GO_PKG.match(s.code, k)
                if m:
                    pkg = m.group(1)
                    continue
            m = _GO_METHOD.match(s.code, k)
            if m:
                if _go_public(m.group("name")):
                    methods.setdefault(m.group("recv"), []).append(
                        Node(Kind.METHOD, m.group("name"), sig=_go_func_sig(s, m.end()),
                             doc=doc_before(s, k)))
                continue
            m = _GO_FUNC.match(s.code, k)
            if m:
                if _go_public(m.group("name")):
                    nodes.append(Node(Kind.FUNCTION, m.group("name"),
                                      sig=_go_func_sig(s, m.end()), doc=doc_before(s, k)))
                continue
            m = _GO_GROUP.match(s.code, k)
            if m:
                p = s.code.index("(", k)
                pe = match_paren(s.code, p)
                for k2 in _paren_lines(s, p + 1, pe):
                    self._decl(s, k2, m.group("kw"), nodes)
                continue
            for kw in ("type", "const", "var"):
                if re.match(rf"{kw}\b", s.code[k:k + 6]):
                    self._decl(s, k + len(kw), kw, nodes, at=k)
                    break
        for recv, ms in methods.items():
            nodes.append(Node(Kind.CLASS, recv, attrs=(("kind", "impl"),), children=tuple(ms)))
        return _container(src, "go", s, _merge(nodes), [("package", pkg)] if pkg else [])

    @staticmethod
    def _decl(s: Scan, k: int, kw: str, nodes: list[Node], at: int | None = None) -> None:
        doc_at = k if at is None else at
        while k < len(s.code) and s.code[k] in " \t":
            k += 1
        if kw == "type":
            m = _GO_TYPE.match("type " + s.code[k:k + 400])
            if m:
                rest_at = k + m.start("rest") - 5
                n = _go_type(s, doc_at, m.group("name"), m.group("tp") or "", bool(m.group("eq")),
                             rest_at)
                if n is not None:
                    nodes.append(n)
            return
        m = re.match(r"(\w+(?:\s*,\s*\w+)*)\s*(.*)", s.code[k:s.code.find("\n", k)])
        if not m:
            return
        rest = m.group(2)
        typ, val = "", "..."
        if "=" in rest:
            t, _, _v = rest.partition("=")
            typ = norm(t)
            eq = k + m.start(2) + rest.index("=") + 1
            val = _value(s, eq, _stmt_end(s, eq))
        else:
            typ = norm(rest)
        for nm in (x.strip() for x in m.group(1).split(",")):
            if _go_public(nm):
                attrs = [("value", val)] if val else []
                if kw == "var":
                    attrs.append(("var", "1"))
                nodes.append(Node(Kind.CONSTANT, nm, sig=typ, doc=doc_before(s, doc_at),
                                  attrs=tuple(sorted(attrs))))


def _paren_lines(s: Scan, lo: int, hi: int):
    """Line starts inside a ( ... ) group, one per spec, skipping nested brackets."""
    d = 0
    i = lo
    at_line = True
    while i < hi:
        ch = s.code[i]
        if at_line and d == 0 and ch not in " \t\r\n":
            yield i
            at_line = False
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
        elif ch == "\n" and d == 0:
            at_line = True
        i += 1


# ------------------------------------------------------------------ Rust

_RS_VIS = r"(?P<vis>pub(?:\s*\([^)]*\))?\s+)"
_RS_FN = re.compile(_RS_VIS + r"?(?P<mods>(?:(?:const|async|unsafe|extern\s+\"[^\"]*\"|extern)\s+)*)"
                    r"fn\s+(?P<name>\w+)")
_RS_ITEM = re.compile(_RS_VIS + r"?(?P<kw>struct|enum|trait|union|type|mod)\s+(?P<name>\w+)")
_RS_CONST = re.compile(_RS_VIS + r"?(?P<kw>const|static)\s+(?:mut\s+)?(?P<name>\w+)\s*:\s*")
_RS_IMPL = re.compile(r"(?:unsafe\s+)?impl\b")
_RS_USE = re.compile(r"pub(?:\s*\([^)]*\))?\s+use\s+(?P<path>[^;]+);")
_RS_ATTR = re.compile(r"#\[(derive|cfg)\(([^)]*)\)\]")


def _rs_public(m) -> bool:
    v = (m.group("vis") or "").strip()
    return v.startswith("pub")


def _rs_vis_attr(m) -> list[tuple[str, str]]:
    v = re.sub(r"\s+", "", (m.group("vis") or ""))
    return [("vis", v)] if v and v != "pub" else []


def _rs_attrs_before(s: Scan, k: int) -> list[tuple[str, str]]:
    """derive/cfg attributes on the #[...] lines right above k."""
    out: dict[str, list[str]] = {}
    pos = _doc_anchor(s, k)
    for m in _RS_ATTR.finditer(s.nc[pos:k]):
        out.setdefault(m.group(1), []).extend(x.strip() for x in m.group(2).split(","))
    return [(k2, ", ".join(sorted(set(filter(None, v))))) for k2, v in sorted(out.items())]


def _rs_fn_sig(s: Scan, at: int) -> str:
    o = find_open(s.code, at, stops=";")
    end = o if o >= 0 else s.code.find(";", at)
    end = len(s.code) if end < 0 else end
    return norm(s.nc[at:end])


def _rs_body_items(s: Scan, o: int, close: int, all_fns: bool, kind: str) -> list[Node]:
    kids: list[Node] = []
    d = s.depth_at(o) + 1
    for k in line_starts(s, o + 1, close, d):
        m = _RS_FN.match(s.code, k)
        if m:
            if all_fns or _rs_public(m):
                attrs = _rs_vis_attr(m)
                if "async" in m.group("mods"):
                    attrs.append(("async", "1"))
                if "unsafe" in m.group("mods"):
                    attrs.append(("unsafe", "1"))
                kids.append(Node(Kind.METHOD, m.group("name"), sig=_rs_fn_sig(s, m.end()),
                                 doc=doc_before(s, k), attrs=tuple(sorted(attrs))))
            continue
        if kind == "struct":
            f = re.match(_RS_VIS + r"(?P<name>\w+)\s*:\s*", s.code[k:])
            if f and _rs_public(f):
                end = _field_end(s, k + f.end())
                kids.append(Node(Kind.FIELD, f.group("name"), sig=norm(s.nc[k + f.end():end]),
                                 doc=doc_before(s, k), attrs=tuple(_rs_vis_attr(f))))
        elif kind == "enum":
            v = re.match(r"(\w+)\s*(.*)", s.code[k:s.code.find("\n", k)])
            if v and v.group(1)[:1].isupper():
                rest = v.group(2)
                sig = ""
                if rest[:1] in "({":
                    p = k + v.start(2)
                    pe = match_paren(s.code, p, rest[0], ")" if rest[0] == "(" else "}")
                    sig = norm(s.nc[p:pe + 1])
                kids.append(Node(Kind.FIELD, v.group(1), sig=sig, doc=doc_before(s, k)))
        elif kind == "trait":
            t = re.match(r"type\s+(\w+)", s.code[k:])
            if t:
                kids.append(Node(Kind.FIELD, t.group(1), attrs=(("kind", "type"),)))
    return kids


def _field_end(s: Scan, i: int) -> int:
    d = 0
    for j in range(i, len(s.code)):
        ch = s.code[j]
        if ch in "([<{":
            d += 1
        elif ch in ")]>}":
            if d == 0:
                return j
            d -= 1
        elif ch == "," and d == 0:
            return j
    return len(s.code)


class RustExtractor:
    name: ClassVar[str] = "rust"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(RUST_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        s = scan(_text(src), "rust")
        nodes: list[Node] = []
        uses: set[str] = set()
        for k in line_starts(s, 0, len(s.code), 0):
            m = _RS_USE.match(s.code, k)
            if m:
                uses.add(norm(m.group("path")))
                continue
            m = _RS_FN.match(s.code, k)
            if m:
                if _rs_public(m):
                    attrs = _rs_vis_attr(m)
                    if "async" in m.group("mods"):
                        attrs.append(("async", "1"))
                    if "const" in m.group("mods"):
                        attrs.append(("const", "1"))
                    if "unsafe" in m.group("mods"):
                        attrs.append(("unsafe", "1"))
                    nodes.append(Node(Kind.FUNCTION, m.group("name"), sig=_rs_fn_sig(s, m.end()),
                                      doc=doc_before(s, _doc_anchor(s, k)),
                                      attrs=tuple(sorted(attrs))))
                continue
            m = _RS_ITEM.match(s.code, k)
            if m:
                kw, name = m.group("kw"), m.group("name")
                if not _rs_public(m) or kw == "mod":
                    if kw == "mod" and _rs_public(m):
                        nodes.append(Node(Kind.CLASS, name, doc=doc_before(s, _doc_anchor(s, k)),
                                          attrs=(("kind", "mod"),)))
                    continue
                attrs = [("kind", kw)] + _rs_vis_attr(m) + _rs_attrs_before(s, k)
                o = find_open(s.code, m.end(), stops=";")
                if kw == "type" or o < 0:
                    end = s.code.find(";", m.end())
                    end = len(s.code) if end < 0 else end
                    nodes.append(Node(Kind.CLASS, name, sig=norm(s.nc[m.end():end]),
                                      doc=doc_before(s, _doc_anchor(s, k)),
                                      attrs=tuple(sorted(attrs))))
                    continue
                close = match_brace(s.code, o)
                kids = _rs_body_items(s, o, close, all_fns=(kw == "trait"), kind=kw)
                nodes.append(Node(Kind.CLASS, name, sig=norm(s.nc[m.end():o]),
                                  doc=doc_before(s, _doc_anchor(s, k)),
                                  attrs=tuple(sorted(attrs)), children=tuple(kids)))
                continue
            m = _RS_CONST.match(s.code, k)
            if m:
                if _rs_public(m):
                    eq = s.code.find("=", m.end())
                    semi = s.code.find(";", m.end())
                    typ_end = eq if 0 <= eq < (semi if semi >= 0 else len(s.code)) else semi
                    val = _value(s, eq + 1, semi) if 0 <= eq < semi else "..."
                    attrs = [("value", val)] + _rs_vis_attr(m)
                    if m.group("kw") == "static":
                        attrs.append(("static", "1"))
                    nodes.append(Node(Kind.CONSTANT, m.group("name"),
                                      sig=norm(s.nc[m.end():typ_end]),
                                      doc=doc_before(s, _doc_anchor(s, k)),
                                      attrs=tuple(sorted(attrs))))
                continue
            m = _RS_IMPL.match(s.code, k)
            if m:
                o = find_open(s.code, m.end(), stops=";")
                if o < 0:
                    continue
                head = norm(s.nc[m.end():o])
                head = re.sub(r"\s+where\s.*$", "", head)
                head = re.sub(r"^<.*?>\s*(?=\w)", "", head) if head.startswith("<") else head
                trait, _, typ = head.rpartition(" for ")
                typ = re.sub(r"<.*", "", typ).strip().lstrip("&").split("::")[-1].strip()
                if not typ:
                    continue
                close = match_brace(s.code, o)
                kids = _rs_body_items(s, o, close, all_fns=bool(trait), kind="impl")
                attrs = [("kind", "impl")]
                if trait:
                    attrs.append(("trait", trait.strip()))
                if kids or trait:
                    nodes.append(Node(Kind.CLASS, typ, attrs=tuple(sorted(attrs)),
                                      children=tuple(kids)))
        if uses:
            nodes.append(Node(Kind.EXPORT, "pub use", sig=json.dumps(sorted(uses))))
        return _container(src, "rust", s, _merge(nodes))


def _doc_anchor(s: Scan, k: int) -> int:
    """Start of the #[...] attribute lines right above k, so doc comments above them count."""
    ls = s.text.rfind("\n", 0, k) + 1
    while ls > 0:
        prev = s.text.rfind("\n", 0, ls - 1) + 1
        if not s.code[prev:ls - 1].strip().startswith("#["):
            break
        ls = prev
    return ls


# ------------------------------------------------------------------ shell

_SH_FUNC = re.compile(r"(?:function\s+(?P<a>[\w.:-]+)\s*(?:\(\s*\))?|(?P<b>[\w.:-]+)\s*\(\s*\))"
                      r"\s*(?:\{|\(|$)")
_PS_FUNC = re.compile(r"function\s+(?P<a>[\w.:-]+)\s*(?P<params>\([^)]*\))?", re.I)
_SH_CONST = re.compile(r"(?:export\s+|readonly\s+|declare\s+-\w+\s+)?(?P<name>[A-Z][A-Z0-9_]*)=")


class ShellExtractor:
    name: ClassVar[str] = "shell"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.endswith(SH_EXT)

    def extract(self, src: SourceFile) -> list[Node]:
        ps = src.path.endswith(".ps1")
        text = _text(src)
        if ps:
            # PowerShell block comments <# ... #> become line comments for the scanner
            text = re.sub(r"<#.*?#>", lambda m: re.sub(r"[^\n]", "#", m.group(0)), text, flags=re.S)
        s = scan(text, "sh")
        nodes: list[Node] = []
        for k in line_starts(s, 0, len(s.code), 0):
            if ps:
                m = _PS_FUNC.match(s.code, k)
                if m:
                    params = m.group("params") or ""
                    if not params:
                        o = s.code.find("{", m.end())
                        pm = re.search(r"param\s*\(", s.code[o:o + 2000], re.I) if o >= 0 else None
                        if pm and s.code[o + 1:o + pm.start()].strip() == "":
                            p = o + pm.end() - 1
                            params = s.nc[p:match_paren(s.code, p) + 1]
                    nodes.append(Node(Kind.FUNCTION, m.group("a"), sig=norm(params),
                                      doc=doc_before(s, k)))
                continue
            m = _SH_FUNC.match(s.code, k)
            if m and (m.group("a") or m.group("b")) not in ("if", "while", "for", "case"):
                nodes.append(Node(Kind.FUNCTION, m.group("a") or m.group("b"), sig="()",
                                  doc=doc_before(s, k)))
                continue
            m = _SH_CONST.match(s.code, k)
            if m and (k == 0 or s.code[k - 1] == "\n"):
                end = _sh_value_end(s, m.end())
                nodes.append(Node(Kind.CONSTANT, m.group("name"), doc=doc_before(s, k),
                                  attrs=(("value", _value(s, m.end(), end, bare=True)),)))
        # one CONSTANT per name: the first assignment wins
        seen: set[str] = set()
        uniq = []
        for n in nodes:
            key = (n.kind, n.name)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(n)
        return _container(src, "powershell" if ps else "sh", s, uniq)


def _sh_value_end(s: Scan, i: int) -> int:
    j = s.code.find("\n", i)
    return len(s.code) if j < 0 else j


LANG_OF = {**{e: "js" for e in JS_EXT}, ".go": "go", ".rs": "rust",
           **{e: "sh" for e in SH_EXT}}


def lang_of(path: str) -> str:
    return LANG_OF.get(PurePosixPath(path).suffix, "")
