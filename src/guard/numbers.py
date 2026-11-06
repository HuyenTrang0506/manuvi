"""BL-US-35: numbers keep the source format (policy keep_source)."""
from __future__ import annotations

import re
from collections import Counter
from typing import Iterable

from .masking import PLACEHOLDER_RE, PatternSpec, mask

NUMBER_POLICY = "keep_source"
NUMBER_RE = re.compile(r"(?<![0-9])[0-9]+(?:[.,][0-9]+)*")


def extract_numbers(text: str, patterns: Iterable[PatternSpec] | None = None) -> list[str]:
    """Numbers outside protected tokens (those are checked by the protected-code rule)."""
    masked, _ = mask(text, patterns)
    return NUMBER_RE.findall(PLACEHOLDER_RE.sub(" ", masked))


def diff_numbers(source: str, target: str, patterns: Iterable[PatternSpec] | None = None):
    """Return (missing_in_target, extra_in_target) as sorted lists, exact string compare."""
    s, t = Counter(extract_numbers(source, patterns)), Counter(extract_numbers(target, patterns))
    return sorted((s - t).elements()), sorted((t - s).elements())
