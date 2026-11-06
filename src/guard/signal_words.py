"""BL-US-37: signal words map to fixed VI forms (config/signal_words.yaml)."""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

import yaml

DEFAULT_TABLE = Path(__file__).resolve().parents[2] / "config" / "signal_words.yaml"


def load_signal_table(path: Path | str | None = None) -> dict[str, str]:
    data = yaml.safe_load(Path(path or DEFAULT_TABLE).read_text(encoding="utf-8"))
    return dict(data["signal_words"])


def _count(text: str, word: str) -> int:
    return len(re.findall(rf"(?<!\w){re.escape(word)}(?!\w)", text))


def signal_mismatches(source: str, target: str, table: dict[str, str]) -> list[tuple[str, str, int, int]]:
    """Per EN signal word: (en, vi, count_in_source, count_in_target) where counts differ.

    Source signals are matched case-sensitively as UPPERCASE whole words; target must carry
    the fixed VI form the same number of times.
    """
    out = []
    for en, vi in table.items():
        s, t = _count(source, en), _count(target, vi)
        if s != t:
            out.append((en, vi, s, t))
    return out
