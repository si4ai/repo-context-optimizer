"""SQL schema: tables, views, columns, indexes, procedures (step 2).

A small tokenizer plus a reader for CREATE statements; no SQL engine and
no dialect switch. It reads MySQL, PostgreSQL and SQLite DDL:

- CREATE [TEMPORARY] TABLE: TABLE with COLUMN and INDEX children. A
  TEMPORARY table is scratch space, not schema, and is skipped.
- CREATE VIEW: VIEW with COLUMN children from the explicit column list,
  else from the names in the top-level SELECT list.
- CREATE [UNIQUE] INDEX ... ON t: an INDEX child of table t when t is
  created in the same file. An index on a table defined elsewhere is
  not part of this file's Tree.
- CREATE PROCEDURE / FUNCTION: PROCEDURE, sig `(IN a INT, OUT b TEXT)`,
  plus ` RETURNS T` for a function.
- PRIMARY KEY (a,b) as a table constraint is the INDEX named `PRIMARY`
  with sig `PRIMARY (a,b)`. An unnamed UNIQUE takes its first column's
  name (the MySQL rule).

Identifiers (table, view, column, index, procedure and parameter names)
are lower-cased unless quoted; a quoted name keeps its case and loses its
quotes. In a sig every bare word is upper-case, string literals and
quoted names are kept as written, commas take no space, and the rest is
single-spaced. DEFAULT goes to attrs `default`; COMMENT 'x' goes to doc,
else the first line of the `--` comment block right above the name, else
a `--` comment that ends the same line.

DELIMITER lines (the mysql client directive) are understood, and
$tag$ bodies are skipped as strings. ALTER TABLE, triggers, sequences,
types and foreign keys are not part of the Tree yet.

Pure and never raises: an unterminated string, quoted name or block
comment, or an unclosed parenthesis in a statement it reads, gives one
FILE node with attrs error=parse.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import PurePosixPath
from typing import ClassVar

from rco.model import Kind, Node, SourceFile


class _ParseError(Exception):
    pass


@dataclass(slots=True)
class _Tok:
    kind: str           # "w" word, "q" quoted name, "s" string, "n" number, "p" punct, "sep"
    text: str
    line: int           # internal only; never reaches a Node
    lead: str = ""      # first line of the -- block right above this token
    trail: str = ""     # -- comment that ends this token's line
    end_line: int = 0


_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_$]*")
_NUM = re.compile(r"0[xX][0-9A-Fa-f]+|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_DELIM = re.compile(r"[ \t]*DELIMITER[ \t]+(\S+)[^\n]*", re.IGNORECASE)
_DOLLAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")
_PUNCT2 = ("::", "<=", ">=", "<>", "!=", "||", "->>", "->", ":=")


def _comment_text(raw: str) -> str:
    return raw.lstrip("-").strip()


def _tokenize(text: str) -> list[_Tok]:
    toks: list[_Tok] = []
    pending: list[str] = []         # -- lines since the last token, blank line resets
    delim = ";"
    i, n, line = 0, len(text), 1
    at_line_start = True
    depth = 0          # parenthesis depth: DELIMITER is a directive only at depth 0

    def add(kind: str, s: str, start_line: int) -> None:
        nonlocal pending
        lead = next((p for p in pending if p), "")
        toks.append(_Tok(kind, s, start_line, lead=lead, end_line=line))
        pending = []

    while i < n:
        c = text[i]
        if at_line_start and depth == 0 and (not toks or toks[-1].kind == "sep"):
            m = _DELIM.match(text, i)
            if m:
                delim = m.group(1)
                add("sep", delim, line)
                i = m.end()
                continue
        if c == "\n":
            # a blank line ends a comment block
            j = text.rfind("\n", 0, i)
            if text[j + 1:i].strip() == "":
                pending = []
            line += 1
            i += 1
            at_line_start = True
            continue
        if c in " \t\r\f\v":
            i += 1
            continue
        at_line_start = False
        if delim != ";" and text.startswith(delim, i):
            add("sep", delim, line)
            i += len(delim)
            continue
        if text.startswith("--", i) or c == "#":
            j = text.find("\n", i)
            j = n if j < 0 else j
            body = _comment_text(text[i:j]) if c == "-" else ""
            if toks and toks[-1].end_line == line:
                if c == "-" and not toks[-1].trail:
                    toks[-1].trail = body
            elif c == "-":
                pending.append(body)
            i = j
            continue
        if text.startswith("/*", i):
            j = text.find("*/", i + 2)
            if j < 0:
                raise _ParseError("unterminated block comment")
            line += text.count("\n", i, j)
            i = j + 2
            continue
        start = line
        if c in "'\"`":
            j = i + 1
            while True:
                if j >= n:
                    raise _ParseError("unterminated literal")
                ch = text[j]
                if ch == "\\" and c == "'":
                    j += 2
                    continue
                if ch == c:
                    if j + 1 < n and text[j + 1] == c:
                        j += 2
                        continue
                    break
                j += 1
            raw = text[i:j + 1]
            line += raw.count("\n")
            add("s" if c == "'" else "q", raw, start)
            i = j + 1
            continue
        if c == "$":
            m = _DOLLAR.match(text, i)
            if m:
                j = text.find(m.group(0), m.end())
                if j < 0:
                    raise _ParseError("unterminated dollar string")
                raw = text[i:j + len(m.group(0))]
                line += raw.count("\n")
                add("s", raw, start)
                i += len(raw)
                continue
        m = _WORD.match(text, i)
        if m:
            add("w", m.group(0), start)
            i = m.end()
            continue
        m = _NUM.match(text, i)
        if m and m.group(0):
            add("n", m.group(0), start)
            i = m.end()
            continue
        if c == ";":
            add("sep", ";", start)
            i += 1
            continue
        p2 = next((p for p in _PUNCT2 if text.startswith(p, i)), None)
        p = p2 or c
        if p == "(":
            depth += 1
        elif p == ")":
            depth = max(0, depth - 1)
        add("p", p, start)
        i += len(p)
    return toks


# --- rendering ------------------------------------------------------------

_TIGHT_BEFORE = {",", ")", ".", "::", "@", "[", "]"}
_TIGHT_AFTER = {"(", ",", ".", "::", "@", "["}


def _render(toks: list[_Tok]) -> str:
    out: list[str] = []
    prev: _Tok | None = None
    unary = False       # the previous token was a sign with no operand before it
    for t in toks:
        s = t.text.upper() if t.kind == "w" else t.text
        if prev is not None:
            tight = unary or (t.text in _TIGHT_BEFORE and t.kind == "p") \
                or (prev.text in _TIGHT_AFTER and prev.kind == "p") \
                or (t.text == "(" and t.kind == "p" and prev.kind in ("w", "q"))
            if not tight:
                out.append(" ")
        out.append(s)
        unary = t.kind == "p" and t.text in ("-", "+") and (
            prev is None or (prev.kind == "p" and prev.text != ")")
            or _is_w(prev, "DEFAULT", "AND", "OR", "NOT", "THEN", "ELSE", "WHEN"))
        prev = t
    return "".join(out)


def _ident(t: _Tok) -> str:
    if t.kind == "q":
        q = t.text[0]
        return t.text[1:-1].replace(q + q, q)
    return t.text.lower()


def _unquote(t: _Tok) -> str:
    body = t.text[1:-1].replace("''", "'")
    return body.replace("\\'", "'")


def _first_line(s: str) -> str:
    return next((ln.strip() for ln in s.splitlines() if ln.strip()), "")


def _is_w(t: _Tok | None, *words: str) -> bool:
    return t is not None and t.kind == "w" and t.text.upper() in words


def _is_p(t: _Tok | None, p: str) -> bool:
    return t is not None and t.kind == "p" and t.text == p


# --- reader ---------------------------------------------------------------

@dataclass(slots=True)
class _Reader:
    toks: list[_Tok]
    i: int = 0

    def peek(self, k: int = 0) -> _Tok | None:
        j = self.i + k
        return self.toks[j] if j < len(self.toks) else None

    def take(self) -> _Tok:
        t = self.peek()
        if t is None:
            raise _ParseError("unexpected end")
        self.i += 1
        return t

    def accept(self, *words: str) -> bool:
        if _is_w(self.peek(), *words):
            self.i += 1
            return True
        return False

    def accept_seq(self, *words: str) -> bool:
        for k, w in enumerate(words):
            if not _is_w(self.peek(k), w):
                return False
        self.i += len(words)
        return True

    def group(self) -> list[_Tok]:
        """Tokens inside the balanced (...) at the cursor, cursor after ')'."""
        if not _is_p(self.peek(), "("):
            raise _ParseError("expected (")
        self.i += 1
        depth, out = 1, []
        while True:
            t = self.peek()
            if t is None or t.kind == "sep" and t.text != ";":
                raise _ParseError("unclosed (")
            self.i += 1
            if _is_p(t, "("):
                depth += 1
            elif _is_p(t, ")"):
                depth -= 1
                if depth == 0:
                    return out
            out.append(t)

    def qname(self) -> tuple[str, _Tok] | None:
        t = self.peek()
        if t is None or t.kind not in ("w", "q"):
            return None
        parts, first = [], t
        while True:
            t = self.peek()
            if t is None or t.kind not in ("w", "q"):
                break
            self.i += 1
            parts.append(_ident(t))
            if _is_p(self.peek(), ".") and self.peek(1) is not None \
                    and self.peek(1).kind in ("w", "q"):
                self.i += 1
                continue
            break
        return ".".join(parts), first


def _split(toks: list[_Tok]) -> list[list[_Tok]]:
    """Split on top-level commas."""
    out: list[list[_Tok]] = [[]]
    depth = 0
    for t in toks:
        if _is_p(t, "("):
            depth += 1
        elif _is_p(t, ")"):
            depth -= 1
        if depth == 0 and _is_p(t, ","):
            out.append([])
            continue
        out[-1].append(t)
    return [x for x in out if x]


def _take_expr(toks: list[_Tok], j: int) -> int:
    """End index (exclusive) of one DEFAULT expression starting at toks[j]."""
    n = len(toks)

    def skip_group(k: int) -> int:
        depth = 0
        while k < n:
            if _is_p(toks[k], "("):
                depth += 1
            elif _is_p(toks[k], ")"):
                depth -= 1
                if depth == 0:
                    return k + 1
            k += 1
        return k

    if j >= n:
        return j
    if toks[j].kind == "p" and toks[j].text in ("-", "+") and j + 1 < n:
        j += 1
    if _is_p(toks[j], "("):
        k = skip_group(j)
    else:
        k = j + 1
        if toks[j].kind == "w" and k < n and toks[j + 1].kind == "s" \
                and toks[j].text.upper() in ("N", "X", "B", "E", "_UTF8MB4", "DATE", "TIMESTAMP"):
            k += 1
        if k < n and _is_p(toks[k], "("):
            k = skip_group(k)
    while k + 1 < n and _is_p(toks[k], "::"):
        k += 2
        if k < n and _is_p(toks[k], "("):
            k = skip_group(k)
    return k


_TABLE_CONSTRAINT = {"PRIMARY", "UNIQUE", "KEY", "INDEX", "CONSTRAINT", "FOREIGN",
                     "CHECK", "FULLTEXT", "SPATIAL", "EXCLUDE", "PERIOD", "LIKE"}


def _cols(group: list[_Tok]) -> str:
    items = []
    for item in _split(group):
        plain = item[0].kind in ("w", "q") and not (
            len(item) > 2 and _is_p(item[1], "(") and item[2].kind != "n")
        if plain:
            head = _ident(item[0])
            rest = item[1:]
            s = head + (_render(rest) if rest and _is_p(rest[0], "(") else
                        (" " + _render(rest) if rest else ""))
        else:
            s = _render(item)
        items.append(s)
    return "(" + ",".join(items) + ")"


def _index_from_constraint(item: list[_Tok]) -> Node | None:
    r = _Reader(item)
    cname = ""
    if r.accept("CONSTRAINT"):
        t = r.peek()
        if t is not None and t.kind in ("w", "q") and not _is_w(
                t, "PRIMARY", "UNIQUE", "FOREIGN", "CHECK"):
            cname = _ident(r.take())
    if r.accept_seq("PRIMARY", "KEY"):
        prefix, name = "PRIMARY ", "PRIMARY"
    elif r.accept("UNIQUE"):
        r.accept("KEY", "INDEX")
        prefix, name = "UNIQUE ", cname
    elif r.accept("FULLTEXT", "SPATIAL"):
        prefix = r.toks[r.i - 1].text.upper() + " "
        r.accept("KEY", "INDEX")
        name = cname
    elif r.accept("KEY", "INDEX"):
        prefix, name = "", cname
    else:
        return None     # FOREIGN KEY, CHECK, EXCLUDE: not part of the Tree yet
    while r.peek() is not None and not _is_p(r.peek(), "("):
        t = r.take()
        if not name and t.kind in ("w", "q") and not _is_w(t, "USING", "BTREE", "HASH"):
            name = _ident(t)
    if r.peek() is None:
        return None
    group = r.group()
    if not name:
        first = _split(group)
        name = _ident(first[0][0]) if first and first[0][0].kind in ("w", "q") else "index"
    return Node(Kind.INDEX, name, sig=prefix + _cols(group))


def _column(item: list[_Tok], sep_after: _Tok | None) -> Node:
    name_tok = item[0]
    sig_toks: list[_Tok] = []
    default = ""
    comment = ""
    j = 1
    while j < len(item):
        t = item[j]
        if _is_w(t, "DEFAULT"):
            k = _take_expr(item, j + 1)
            default = _render(item[j + 1:k])
            j = k
            continue
        if _is_w(t, "COMMENT") and j + 1 < len(item) and item[j + 1].kind == "s":
            comment = _first_line(_unquote(item[j + 1]))
            j += 2
            continue
        sig_toks.append(t)
        j += 1
    trail = (sep_after.trail if sep_after is not None else "") or item[-1].trail
    doc = comment or name_tok.lead or trail
    return Node(Kind.COLUMN, _ident(name_tok), sig=_render(sig_toks),
                doc=doc, attrs=(("default", default),) if default else ())


def _table_body(group: list[_Tok], seps: dict[int, _Tok]) -> list[Node]:
    children: list[Node] = []
    for item in _split(group):
        first = item[0]
        if first.kind == "w" and first.text.upper() in _TABLE_CONSTRAINT:
            idx = _index_from_constraint(item)
            if idx is not None:
                children.append(idx)
            continue
        if first.kind not in ("w", "q"):
            continue
        children.append(_column(item, seps.get(id(item[-1]))))
    return children


def _options_comment(r: _Reader) -> str:
    """Scan table or routine options to the end of the statement; COMMENT text."""
    doc = ""
    while True:
        t = r.peek()
        if t is None or t.kind == "sep" or _is_w(t, "CREATE"):
            return doc
        if _is_w(t, "COMMENT"):
            r.i += 1
            if _is_p(r.peek(), "="):
                r.i += 1
            if r.peek() is not None and r.peek().kind == "s":
                doc = doc or _first_line(_unquote(r.take()))
            continue
        if _is_p(t, "("):
            r.group()
            continue
        r.i += 1


_ALIAS_STOP = {"END", "NULL", "TRUE", "FALSE", "DISTINCT", "ALL"}


def _view_columns(r: _Reader) -> list[Node]:
    """Names of the top-level SELECT list after AS."""
    while r.peek() is not None and r.peek().kind != "sep" and not _is_w(r.peek(), "SELECT"):
        if _is_w(r.peek(), "CREATE"):
            return []
        if _is_p(r.peek(), "("):
            r.group()      # WITH ... AS (...) and friends
            continue
        r.i += 1
    if not r.accept("SELECT"):
        return []
    r.accept("DISTINCT", "ALL")
    items: list[_Tok] = []
    while True:
        t = r.peek()
        if t is None or t.kind == "sep" or _is_w(t, "FROM", "CREATE", "WHERE", "UNION"):
            break
        if _is_p(t, "("):
            items.append(t)
            items.extend(r.group())
            items.append(_Tok("p", ")", 0))
            continue
        items.append(r.take())
    names: list[Node] = []
    for item in _split(items):
        last = item[-1]
        name = ""
        if len(item) >= 2 and _is_w(item[-2], "AS") and last.kind in ("w", "q"):
            name = _ident(last)
        elif last.kind in ("w", "q") and not _is_w(last, *_ALIAS_STOP):
            if len(item) == 1 or _is_p(item[-2], ".") or item[-2].kind in ("w", "q") \
                    or _is_p(item[-2], ")"):
                name = _ident(last)
        if name:
            names.append(Node(Kind.COLUMN, name))
    return names


_RET_STOP = {"NOT", "DETERMINISTIC", "READS", "MODIFIES", "NO", "CONTAINS", "LANGUAGE",
             "SQL", "COMMENT", "BEGIN", "AS", "RETURN", "IMMUTABLE", "STABLE", "VOLATILE",
             "STRICT", "SECURITY", "CALLED", "PARALLEL", "COST", "ROWS", "LEAKPROOF",
             "SET", "WINDOW", "EXTERNAL", "DECLARE", "SELECT", "CREATE", "SUPPORT"}
_MODES = {"IN", "OUT", "INOUT", "VARIADIC"}


def _param(item: list[_Tok]) -> str:
    parts: list[str] = []
    j = 0
    if _is_w(item[0], *_MODES) and len(item) > 1:
        parts.append(item[0].text.upper())
        j = 1
    rest = item[j:]
    if len(rest) >= 2 and rest[0].kind in ("w", "q") and rest[1].kind in ("w", "q"):
        parts.append(_ident(rest[0]))
        rest = rest[1:]
    if rest:
        parts.append(_render(rest))
    return " ".join(parts)


def _routine(r: _Reader) -> tuple[str, str]:
    """(sig, COMMENT doc) of a procedure or function; cursor after its header."""
    params = r.group() if _is_p(r.peek(), "(") else []
    sig = "(" + ", ".join(_param(p) for p in _split(params)) + ")"
    if r.accept("RETURNS"):
        ret: list[_Tok] = []
        while True:
            t = r.peek()
            if t is None or t.kind in ("sep", "s") or _is_w(t, *_RET_STOP):
                break
            if _is_p(t, "("):
                ret.append(t)
                ret.extend(r.group())
                ret.append(_Tok("p", ")", 0))
                continue
            ret.append(r.take())
        sig += " RETURNS " + _render(ret)
    doc = ""
    for _ in range(64):
        t = r.peek()
        if t is None or t.kind == "sep" or _is_w(
                t, "BEGIN", "AS", "RETURN", "CREATE", "DECLARE", "SELECT", "INSERT",
                "UPDATE", "DELETE", "CALL", "IF"):
            break
        if t.kind == "s":
            break
        r.i += 1
        if _is_w(t, "COMMENT") and r.peek() is not None and r.peek().kind == "s":
            doc = _first_line(_unquote(r.take()))
    return sig, doc


def _skip_definer(r: _Reader) -> None:
    if not _is_p(r.peek(), "="):
        return
    r.i += 1
    t = r.take()
    if _is_p(r.peek(), "(") and _is_w(t, "CURRENT_USER"):
        r.group()
    if _is_p(r.peek(), "@"):
        r.i += 2


def _create(r: _Reader, path: str, seps: dict[int, _Tok],
            pending_idx: list[tuple[str, Node]]) -> Node | None:
    create_tok = r.take()
    temp = False
    unique = ""
    while True:
        t = r.peek()
        if t is None:
            return None
        u = t.text.upper() if t.kind == "w" else ""
        if u in ("OR",) and _is_w(r.peek(1), "REPLACE"):
            r.i += 2
        elif u in ("TEMPORARY", "TEMP"):
            temp = True
            r.i += 1
        elif u in ("UNLOGGED", "GLOBAL", "LOCAL", "MATERIALIZED", "RECURSIVE", "AGGREGATE",
                   "CLUSTERED", "NONCLUSTERED", "ONLINE", "OFFLINE"):
            r.i += 1
        elif u in ("UNIQUE", "FULLTEXT", "SPATIAL"):
            unique = u + " "
            r.i += 1
        elif u in ("ALGORITHM", "DEFINER"):
            r.i += 1
            _skip_definer(r)
        elif u == "SQL" and _is_w(r.peek(1), "SECURITY"):
            r.i += 3
        else:
            break
    obj = r.take().text.upper() if r.peek() is not None and r.peek().kind == "w" else ""
    if obj not in ("TABLE", "VIEW", "INDEX", "PROCEDURE", "FUNCTION"):
        return None
    r.accept("CONCURRENTLY")
    r.accept_seq("IF", "NOT", "EXISTS")
    if obj == "INDEX":
        name = ""
        if not _is_w(r.peek(), "ON"):
            q = r.qname()
            name = q[0] if q else ""
        if not r.accept("ON"):
            return None
        r.accept("ONLY")
        q = r.qname()
        if q is None:
            return None
        while r.peek() is not None and not _is_p(r.peek(), "(") and r.peek().kind != "sep":
            r.i += 1
        if not _is_p(r.peek(), "("):
            return None
        group = r.group()
        if not name:
            first = _split(group)
            name = _ident(first[0][0]) if first and first[0][0].kind in ("w", "q") else "index"
        pending_idx.append((q[0], Node(Kind.INDEX, name, sig=unique + _cols(group),
                                       doc=create_tok.lead)))
        return None
    q = r.qname()
    if q is None:
        return None
    name = q[0]
    if obj in ("PROCEDURE", "FUNCTION"):
        sig, doc = _routine(r)
        return Node(Kind.PROCEDURE, name, path, sig=sig, doc=doc or create_tok.lead)
    if obj == "VIEW":
        cols: list[Node] = []
        if _is_p(r.peek(), "("):
            cols = [Node(Kind.COLUMN, _ident(c[0])) for c in _split(r.group())
                    if c[0].kind in ("w", "q")]
        if not cols:
            cols = _view_columns(r)
        doc = _options_comment(r)
        return Node(Kind.VIEW, name, path, doc=doc or create_tok.lead, children=tuple(cols))
    # TABLE
    children: list[Node] = []
    if _is_p(r.peek(), "("):
        children = _table_body(_group_seps(r, seps), seps)
    doc = _options_comment(r)
    if temp:
        return None
    return Node(Kind.TABLE, name, path, doc=doc or create_tok.lead, children=tuple(children))


def _group_seps(r: _Reader, seps: dict[int, _Tok]) -> list[_Tok]:
    """Like _Reader.group, and remember the comma after each top-level item."""
    start = r.i
    group = r.group()
    depth = 0
    prev: _Tok | None = None
    for t in r.toks[start + 1:r.i]:
        if _is_p(t, "("):
            depth += 1
        elif _is_p(t, ")"):
            depth -= 1
        elif depth == 0 and _is_p(t, ",") and prev is not None:
            seps[id(prev)] = t
        prev = t
    return group


def parse(path: str, text: str) -> list[Node]:
    """Top-level nodes of one SQL file. Raises _ParseError on bad input."""
    toks = _tokenize(text)
    r = _Reader(toks)
    nodes: list[Node] = []
    seps: dict[int, _Tok] = {}
    pending_idx: list[tuple[str, Node]] = []
    while r.peek() is not None:
        if _is_w(r.peek(), "CREATE"):
            n = _create(r, path, seps, pending_idx)
            if n is not None:
                nodes.append(n)
            continue
        r.i += 1
    for table, idx in pending_idx:
        for k, n in enumerate(nodes):
            if n.kind is Kind.TABLE and n.name == table:
                nodes[k] = replace(n, children=n.children + (idx,))
    return nodes


class SqlExtractor:
    name: ClassVar[str] = "sql"
    version: ClassVar[str] = "1"

    def claims(self, path: str) -> bool:
        return path.lower().endswith(".sql")

    def extract(self, src: SourceFile) -> list[Node]:
        try:
            return parse(src.path, src.text())
        except Exception:  # never raise: a bad file is a FILE error node
            return [Node(Kind.FILE, PurePosixPath(src.path).name, src.path,
                         attrs=(("error", "parse"),))]
