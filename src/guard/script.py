"""Script/language guard: the target must be Vietnamese (Latin script), not CJK/Cyrillic/Thai/... or English.

Small local models sometimes append or switch to another language. That is a QA error (SCR001/SCR002), so it goes
through the same capped fix loop as every other guard rule, and is never shipped (see segment.finalize_target).
"""
from __future__ import annotations

import re
import unicodedata

from .masking import PLACEHOLDER_RE, load_patterns, mask
from .models import Issue, Severity

# Blocked script ranges. Greek is NOT blocked (Ω, µ, Δ are legitimate in units and electrical symbols).
_BLOCKED = [
    (0x0400, 0x052F),   # Cyrillic (+ supplement)
    (0x0590, 0x08FF),   # Hebrew, Arabic, Syriac, Thaana, NKo, Samaritan...
    (0x0900, 0x0DFF),   # Devanagari ... Sinhala
    (0x0E00, 0x0EFF),   # Thai, Lao
    (0x0F00, 0x109F),   # Tibetan, Myanmar
    (0x10A0, 0x10FF),   # Georgian
    (0x1100, 0x11FF),   # Hangul Jamo
    (0x1200, 0x139F),   # Ethiopic
    (0x1780, 0x17FF),   # Khmer
    (0x2E80, 0x2FDF),   # CJK radicals
    (0x3000, 0x303F),   # CJK symbols and punctuation
    (0x3040, 0x30FF),   # Hiragana, Katakana
    (0x3100, 0x318F),   # Bopomofo, Hangul compatibility Jamo
    (0x3400, 0x4DBF),   # CJK extension A
    (0x4E00, 0x9FFF),   # CJK unified ideographs
    (0xA960, 0xA97F), (0xAC00, 0xD7FF),  # Hangul
    (0xF900, 0xFAFF),   # CJK compatibility ideographs
    (0xFF00, 0xFFEF),   # Halfwidth and fullwidth forms
    (0x20000, 0x2FA1F),  # CJK extensions B..F
]

_EN_STOP = frozenset("""the and or of to in is are be must not do does with without from that this these those for on at by
an as it its if when before after keep use make sure you your can will should may only any all into over under
away which where while until than then also have has had been being was were no""".split())
_VI_DIACRITICS = re.compile(r"[ăâđêôơưàáảãạằắẳẵặầấẩẫậèéẻẽẹềếểễệìíỉĩịòóỏõọồốổỗộờớởỡợùúủũụừứửữựỳýỷỹỵ]", re.I)
_WORD = re.compile(r"[A-Za-z]+")
MIN_WORDS = 5          # shorter texts are labels; "mostly English" is meaningless there
EN_SHARE = 0.4         # share of words that are English function words


def _blocked(ch: str) -> bool:
    cp = ord(ch)
    return any(a <= cp <= b for a, b in _BLOCKED)


def script_issues(source: str, target: str) -> list[Issue]:
    """SCR001: characters of a non-Latin script that the source does not contain.
    SCR002: the text (outside codes and placeholders) is mostly English function words and has no Vietnamese
    diacritics, i.e. it was not translated."""
    src = unicodedata.normalize("NFC", source)
    tgt = unicodedata.normalize("NFC", target)
    bad = sorted({c for c in tgt if _blocked(c) and c not in src})
    out: list[Issue] = []
    if bad:
        out.append(Issue(rule_id="SCR001", severity=Severity.error,
                         message=f"output contains non-Vietnamese script characters: {''.join(bad)[:20]}",
                         fix_hint="Output Vietnamese only (Latin script with Vietnamese diacritics). "
                                  "Remove every Chinese, Cyrillic, Thai or other non-Vietnamese text.",
                         detail={"chars": "".join(bad)}))
    masked, _ = mask(tgt, load_patterns()) if "⟦" not in tgt else (tgt, {})
    text = PLACEHOLDER_RE.sub(" ", masked)
    words = _WORD.findall(text)
    if len(words) >= MIN_WORDS and not _VI_DIACRITICS.search(text):
        share = sum(w.lower() in _EN_STOP for w in words) / len(words)
        if share >= EN_SHARE:
            out.append(Issue(rule_id="SCR002", severity=Severity.error,
                             message=f"output is mostly English, not Vietnamese ({share:.0%} English function words)",
                             fix_hint="Translate the whole text into Vietnamese; do not return English.",
                             detail={"english_share": round(share, 3)}))
    return out
