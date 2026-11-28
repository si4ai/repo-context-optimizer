"""Helpers shared by the brace-language extractors (Java now, the other brace languages later).

They work on the masks from rco.extract.scan: `code` has comments and
string bodies blanked, `nc` keeps strings. Nothing here is specific to one
language, so a later language card reuses them as they are (card 1362).
"""
from __future__ import annotations

import re
from collections.abc import Iterable

from rco.extract.scan import match_brace, match_paren, norm

IDENT = r"[A-Za-z_$][\w$]*"
_ANNO = re.compile(rf"@\s*(?!interface\b){IDENT}(?:\s*\.\s*{IDENT})*")
_ANNO_TEXT = re.compile(rf"@\s*{IDENT}(?:\s*\.\s*{IDENT})*(?:\s*\([^()]*(?:\([^()]*\)[^()]*)*\))?\s*")


def skip_ws(code: str, i: int, hi: int) -> int:
    while i < hi and code[i] in " \t\r\n":
        i += 1
    return i


def skip_annotations(code: str, i: int, hi: int) -> int:
    """Index after any `@Name` / `@Name(args)` annotations that start at i."""
    while True:
        i = skip_ws(code, i, hi)
        m = _ANNO.match(code, i, hi)
        if not m:
            return i
        i = skip_ws(code, m.end(), hi)
        if i < hi and code[i] == "(":
            i = match_paren(code, i) + 1


def strip_annotations(text: str) -> str:
    """Drop `@Name(args)` annotations from a signature piece."""
    return _ANNO_TEXT.sub("", text)


def read_mods(code: str, i: int, hi: int, words: Iterable[str],
              bracket: bool = False) -> tuple[list[str], int]:
    """The modifier keywords at i; `@Name` annotations (or, with bracket, `[Attr]` lists)
    between them are skipped."""
    rx = re.compile(r"(?:" + "|".join(sorted(map(re.escape, words), key=len, reverse=True))
                    + r")(?![\w$-])")
    mods: list[str] = []
    while True:
        i = skip_brackets(code, i, hi) if bracket else skip_annotations(code, i, hi)
        m = rx.match(code, i, hi)
        if not m:
            return mods, i
        mods.append(m.group(0))
        i = skip_ws(code, m.end(), hi)


def find_term(code: str, i: int, hi: int, chars: str) -> int:
    """First char of `chars` at ( [ { depth 0 from i (a { in chars is found before it nests)."""
    d = 0
    for j in range(i, hi):
        ch = code[j]
        if d == 0 and ch in chars:
            return j
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
            if d < 0:
                return -1
    return -1


def skip_brackets(code: str, i: int, hi: int) -> int:
    """Index after any `[Attr]` / `[Attr(args), Other]` attribute lists that start at i (C#)."""
    while True:
        i = skip_ws(code, i, hi)
        if i >= hi or code[i] != "[":
            return i
        i = match_paren(code, i, "[", "]") + 1


_BRACKET_ATTR = re.compile(r"(?<![\w\]>)])\[\s*[A-Za-z_@][^\[\]]*(?:\[[^\[\]]*\][^\[\]]*)*\]\s*")


def strip_bracket_attrs(text: str) -> str:
    """Drop `[Attr]` attribute lists from a signature piece; `int[]` and `T[,]` stay."""
    return _BRACKET_ATTR.sub("", text)


def find_term_generic(code: str, i: int, hi: int, chars: str) -> int:
    """find_term, but a terminator inside <...> generic arguments (or a tuple type) does not count."""
    d = a = 0
    for j in range(i, hi):
        ch = code[j]
        if d == 0 and a == 0 and ch in chars:
            return j
        if ch in "([{":
            d += 1
        elif ch in ")]}":
            d -= 1
            if d < 0:
                return -1
        elif ch == "<" and d == 0 and j > i and (code[j - 1].isalnum() or code[j - 1] in "_>?"):
            a += 1
        elif ch == ">" and a and d == 0 and code[j - 1] != "=":
            a -= 1
    return -1


def skip_angles(code: str, i: int, hi: int) -> int:
    """Index after the <...> that starts at i (generic parameters); i itself when none."""
    if i >= hi or code[i] != "<":
        return i
    d = 0
    for j in range(i, hi):
        if code[j] == "<":
            d += 1
        elif code[j] == ">":
            d -= 1
            if d == 0:
                return j + 1
        elif code[j] in ";{}":
            break
    return i


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split on sep outside ( [ { < nesting."""
    out, d, last = [], 0, 0
    for j, ch in enumerate(text):
        if ch in "([{<":
            d += 1
        elif ch in ")]}>":
            d = max(0, d - 1)
        elif ch == sep and d == 0:
            out.append(text[last:j])
            last = j + 1
    out.append(text[last:])
    return out


def block_end(code: str, i: int) -> int:
    """Index after the { ... } block at i."""
    return min(match_brace(code, i) + 1, len(code))


__all__ = ["IDENT", "block_end", "find_term", "find_term_generic", "norm", "read_mods",
           "skip_angles", "skip_annotations", "skip_brackets", "skip_ws", "split_top",
           "strip_annotations", "strip_bracket_attrs"]
