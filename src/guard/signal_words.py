"""BL-US-37: signal words map to fixed VI forms (config/signal_words.yaml)."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from pathlib import Path

import yaml

DEFAULT_TABLE = Path(__file__).resolve().parents[2] / "config" / "signal_words.yaml"


def load_signal_table(path: Path | str | None = None) -> dict[str, str]:
    data = yaml.safe_load(Path(path or DEFAULT_TABLE).read_text(encoding="utf-8"))
    return dict(data["signal_words"])


_LEAD = r"(?:^|(?<=\n))[ \t]*[-•*]?[ \t]*"


def _word_re(word: str) -> re.Pattern[str]:
    w = re.escape(unicodedata.normalize("NFC", word))
    # Signal = at segment start in any case, or UPPERCASE anywhere (whole word).
    return re.compile(rf"(?i:{_LEAD}{w}(?!\w))|(?<!\w){w}(?!\w)")


def _count(text: str, word: str) -> int:
    text = unicodedata.normalize("NFC", text)
    rx = _word_re(word)
    return sum(1 for m in rx.finditer(text))


def is_signal(text: str, table: dict[str, str] | None = None) -> bool:
    """True if text starts with (case-insensitive) or is a signal word (EN or fixed VI form)."""
    table = table or load_signal_table()
    t = unicodedata.normalize("NFC", text).lstrip(" \t-•*")
    for en, vi in table.items():
        for w in (en, vi):
            if re.match(rf"(?i:{re.escape(unicodedata.normalize('NFC', w))})(?!\w)", t):
                return True
    return False


def signal_mismatches(source: str, target: str, table: dict[str, str]) -> list[tuple[str, str, int, int]]:
    """Per EN signal word: (en, vi, count_in_source, count_in_target) where counts differ."""
    out = []
    for en, vi in table.items():
        s, t = _count(source, en), _count(target, vi)
        if s != t:
            out.append((en, vi, s, t))
    return out
