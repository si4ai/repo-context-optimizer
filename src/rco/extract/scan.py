"""Deterministic source scanner for brace languages and shells (no LLM, no deps).

Two masks of the same length as the text, newlines kept:
  nc:   comments blanked to spaces (strings kept), for signatures;
  code: comments and string bodies blanked, for structure (braces, keywords).
Comment spans are kept for doc lines. The scanner never raises on odd input:
an unclosed string or comment runs to the end of the text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SIG_MAX = 240

# JS: a "/" after one of these (or at the start) starts a regex literal.
_REGEX_PREV = set("(,=:[!&|?{};+-*%<>~^") | {""}
_REGEX_KW = ("return", "typeof", "case", "do", "else", "in", "of", "new", "delete",
             "void", "throw", "yield", "await")


@dataclass(frozen=True, slots=True)
class Scan:
    text: str
    nc: str
    code: str
    comments: tuple[tuple[int, int], ...]
    depth: tuple[int, ...]  # brace depth before each char of code

    def depth_at(self, i: int) -> int:
        return self.depth[i] if i < len(self.depth) else (self.depth[-1] if self.depth else 0)


def _blank(chars: list[str], a: int, b: int) -> None:
    for k in range(a, b):
        if chars[k] != "\n":
            chars[k] = " "


def scan(text: str, lang: str) -> Scan:
    """lang: "js", "go", "rust" or "sh"."""
    n = len(text)
    nc = list(text)
    code = list(text)
    comments: list[tuple[int, int]] = []
    i = 0
    last = ""  # last significant code char or word, for JS regex detection

    def prev_word(j: int) -> str:
        k = j
        while k > 0 and text[k - 1] in " \t\r\n":
            k -= 1
        e = k
        while k > 0 and (text[k - 1].isalnum() or text[k - 1] in "_$"):
            k -= 1
        return text[k:e]

    while i < n:
        c = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        # comments
        if lang == "sh" and c == "#" and (i == 0 or text[i - 1] in " \t\n;|&(") \
                and not (i > 0 and text[i - 1] == "$"):
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j))
            _blank(nc, i, j)
            _blank(code, i, j)
            i = j
            continue
        if lang != "sh" and c == "/" and nxt == "/":
            j = text.find("\n", i)
            j = n if j < 0 else j
            comments.append((i, j))
            _blank(nc, i, j)
            _blank(code, i, j)
            i = j
            continue
        if lang != "sh" and c == "/" and nxt == "*":
            if lang == "rust":  # nested block comments
                d, j = 1, i + 2
                while j < n and d:
                    if text.startswith("/*", j):
                        d, j = d + 1, j + 2
                    elif text.startswith("*/", j):
                        d, j = d - 1, j + 2
                    else:
                        j += 1
            else:
                j = text.find("*/", i + 2)
                j = n if j < 0 else j + 2
            comments.append((i, j))
            _blank(nc, i, j)
            _blank(code, i, j)
            i = j
            continue
        # strings
        q = ""
        if c in "\"'`":
            q = c
            if lang == "rust" and c == "'":
                # char literal 'x' or '\n'; otherwise a lifetime
                if not (nxt == "\\" or (i + 2 < n and text[i + 2] == "'")):
                    q = ""
            if lang == "go" and c == "'":
                q = "'"
            if lang == "rust" and c == "`":
                q = ""
        if lang == "rust" and c == "r" and (nxt == '"' or nxt == "#") \
                and not (i > 0 and (text[i - 1].isalnum() or text[i - 1] == "_")):
            m = re.match(r'r(#*)"', text[i:])
            if m:
                close = '"' + m.group(1)
                j = text.find(close, i + len(m.group(0)))
                j = n if j < 0 else j + len(close)
                _blank(code, i + len(m.group(0)), max(i + len(m.group(0)), j - len(close)))
                i = j
                last = '"'
                continue
        if lang == "js" and c == "/" and nxt not in "/*":
            pw = prev_word(i)
            k = i
            while k > 0 and text[k - 1] in " \t\r\n":
                k -= 1
            pc = text[k - 1] if k > 0 else ""
            if (pw in _REGEX_KW) or (not pw and pc in _REGEX_PREV):
                j, cls = i + 1, False
                while j < n and text[j] != "\n":
                    ch = text[j]
                    if ch == "\\":
                        j += 2
                        continue
                    if ch == "[":
                        cls = True
                    elif ch == "]":
                        cls = False
                    elif ch == "/" and not cls:
                        break
                    j += 1
                if j < n and text[j] == "/":
                    _blank(code, i + 1, j)
                    i = j + 1
                    last = "/"
                    continue
        if q:
            j = i + 1
            raw = (lang == "go" and q == "`") or (lang == "sh" and q == "'")
            while j < n:
                ch = text[j]
                if ch == "\\" and not raw:
                    j += 2
                    continue
                if ch == q:
                    break
                if ch == "\n" and q in "\"'" and lang in ("js", "go"):
                    break  # unterminated single-line string
                j += 1
            end = min(j, n)
            _blank(code, i + 1, end)
            i = end + 1
            last = q
            continue
        if not c.isspace():
            last = c
        i += 1
    del last
    depth: list[int] = []
    d = 0
    for ch in code:
        depth.append(d)
        if ch == "{":
            d += 1
        elif ch == "}":
            d = max(0, d - 1)
    return Scan(text, "".join(nc), "".join(code), tuple(comments), tuple(depth))


def match_brace(code: str, i: int) -> int:
    """Index of the } that closes the { at i, or len(code)."""
    d = 0
    for j in range(i, len(code)):
        ch = code[j]
        if ch == "{":
            d += 1
        elif ch == "}":
            d -= 1
            if d == 0:
                return j
    return len(code)


def find_open(code: str, i: int, stops: str = ";") -> int:
    """First { at paren/bracket depth 0 from i, or -1 if a stop char comes first."""
    d = 0
    for j in range(i, len(code)):
        ch = code[j]
        if ch in "([":
            d += 1
        elif ch in ")]":
            d -= 1
        elif d <= 0:
            if ch == "{":
                return j
            if ch in stops:
                return -1
            if ch == "=" and j + 1 < len(code) and code[j + 1] == ">" and "=" in stops:
                return -1
    return -1


def match_paren(code: str, i: int, open_ch: str = "(", close_ch: str = ")") -> int:
    d = 0
    for j in range(i, len(code)):
        ch = code[j]
        if ch == open_ch:
            d += 1
        elif ch == close_ch:
            d -= 1
            if d == 0:
                return j
    return len(code)


def norm(s: str, limit: int = SIG_MAX) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"([(\[<])\s+", r"\1", s)
    s = re.sub(r"\s+([)\]>,])", r"\1", s)
    if len(s) > limit:
        s = s[: limit - 3].rstrip() + "..."
    return s


_DOC_STRIP = re.compile(r"^\s*(?:/\*\*?|\*/|\*|///?|//!|#+|<#|#>)?\s?")


def comment_line(text: str) -> str:
    """First non-empty content line of a comment, markers stripped."""
    body = text
    if body.endswith("*/"):
        body = body[:-2]
    if body.endswith("#>"):
        body = body[:-2]
    for raw in body.splitlines():
        line = _DOC_STRIP.sub("", raw, count=1).strip()
        line = line.lstrip("!*/ ").strip()
        if line and not set(line) <= set("-=*#~_/"):
            return line
    return ""


def doc_before(s: Scan, start: int) -> str:
    """Doc line from the comment block that ends right before start (blank space only between)."""
    block: list[tuple[int, int]] = []
    pos = start
    for a, b in reversed(s.comments):
        if b > pos:
            continue
        gap = s.text[b:pos]
        if gap.strip() or gap.count("\n") > 1:
            break
        block.append((a, b))
        pos = a
        # a comment that starts mid-line (trailing comment) ends the block
        ls = s.text.rfind("\n", 0, a) + 1
        if s.text[ls:a].strip():
            break
    if not block:
        return ""
    block.reverse()
    return comment_line("\n".join(s.text[a:b] for a, b in block))


def header_doc(s: Scan) -> str:
    """Doc line of the first comment, when it comes before any code (shebang skipped)."""
    if not s.comments:
        return ""
    a, b = s.comments[0]
    before = s.code[:a]
    if s.text.startswith("#!"):
        before = s.code[s.text.find("\n") + 1:a] if "\n" in s.text else ""
    if before.strip():
        return ""
    if s.text[a:b].startswith("#!"):
        if len(s.comments) < 2:
            return ""
        a, b = s.comments[1]
        if s.code[s.text.find("\n") + 1:a].strip():
            return ""
    return comment_line(s.text[a:b])


def line_starts(s: Scan, lo: int, hi: int, depth: int):
    """Offsets (after indentation) of lines in [lo, hi) that start at brace depth `depth`."""
    i = lo
    while i < hi:
        j = s.code.find("\n", i, hi)
        j = hi if j < 0 else j
        k = i
        while k < j and s.code[k] in " \t\r":
            k += 1
        if k < j and s.depth_at(k) == depth:
            yield k
        i = j + 1
