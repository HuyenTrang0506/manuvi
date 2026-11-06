"""Fake EN->VI generator for tests and demos ONLY (not a translation).

Keeps protected tokens and any token containing a digit unchanged, lengthens text by ~25 %
and adds Vietnamese diacritics so glyph coverage and widths are realistic.
"""
from __future__ import annotations

import re
import zlib

from ..guard.masking import load_patterns

_VOWELS = {"a": "áàảãạăâấầ", "e": "éèẻẽẹêế", "i": "íìỉĩị", "o": "óòỏõọôơố", "u": "úùủũụư", "y": "ýỳỷỹỵ"}
_FILLER = ["của", "các", "được", "và", "theo", "cho"]
_PATTERNS = None


def _protected_spans(text: str) -> list[tuple[int, int]]:
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = load_patterns()
    spans = sorted(m.span() for _, rx in _PATTERNS for m in rx.finditer(text))
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _diacritic(word: str) -> str:
    if any(c.isdigit() for c in word):
        return word
    h = zlib.crc32(word.encode())
    chars = list(word)
    for i, ch in enumerate(chars):
        low = ch.lower()
        if low in _VOWELS:
            opts = _VOWELS[low]
            new = opts[h % len(opts)]
            chars[i] = new.upper() if ch.isupper() else new
            break
    else:
        if chars and chars[0] in "dD":
            chars[0] = "Đ" if chars[0] == "D" else "đ"
    return "".join(chars)


def _chunk(chunk: str, state: dict) -> str:
    parts = re.split(r"(\s+)", chunk)
    out = []
    for p in parts:
        if not p or p.isspace():
            out.append(p)
            continue
        if re.search(r"[A-Za-z]", p):
            core = re.match(r"^(\W*)(.*?)(\W*)$", p, re.S)
            pre, mid, post = core.groups()
            p = pre + _diacritic(mid) + post
            state["n"] += 1
            if state["n"] % 2 == 0:  # ~ +25 % length
                p += " " + _FILLER[state["n"] % len(_FILLER)]
        out.append(p)
    return "".join(out)


def synth_vi(text: str) -> str:
    spans = _protected_spans(text)
    out, pos, state = [], 0, {"n": 0}
    for a, b in spans:
        out.append(_chunk(text[pos:a], state))
        out.append(text[a:b])
        pos = b
    out.append(_chunk(text[pos:], state))
    return "".join(out)
