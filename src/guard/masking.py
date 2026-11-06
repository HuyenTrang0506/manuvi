"""BL-US-11: mask protected tokens with placeholders and restore them byte-identically."""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Iterable, Pattern

import yaml
from pydantic import BaseModel

log = logging.getLogger("manuvi.guard.mask")

DEFAULT_PATTERNS = Path(__file__).resolve().parent.parent / "intake" / "patterns.yaml"
PLACEHOLDER_RE = re.compile(r"⟦P\d{3,}⟧")

PatternSpec = tuple[str, "str | Pattern[str]"]


def load_patterns(path: Path | str | None = None) -> list[tuple[str, Pattern[str]]]:
    data = yaml.safe_load(Path(path or DEFAULT_PATTERNS).read_text(encoding="utf-8"))
    return [(p["name"], re.compile(p["regex"])) for p in data["protected_patterns"]]


def _compile(patterns: Iterable[PatternSpec]) -> list[tuple[str, Pattern[str]]]:
    return [(n, re.compile(r) if isinstance(r, str) else r) for n, r in patterns]


def mask(text: str, patterns: Iterable[PatternSpec] | None = None) -> tuple[str, dict[str, str]]:
    """Replace protected tokens by unique placeholders. Returns (masked_text, mapping)."""
    if "⟦" in text or "⟧" in text:
        raise ValueError("text already contains placeholder brackets")
    pats = _compile(patterns) if patterns is not None else load_patterns()
    cands: list[tuple[int, int, int, str]] = []
    for prio, (name, rx) in enumerate(pats):
        for m in rx.finditer(text):
            if m.end() > m.start():
                cands.append((m.start(), -(m.end() - m.start()), prio, name))
    cands.sort()
    spans: list[tuple[int, int, str]] = []
    last = 0
    for start, neg_len, _prio, name in cands:
        end = start - neg_len
        if start >= last:
            spans.append((start, end, name))
            last = end
    out: list[str] = []
    mapping: dict[str, str] = {}
    pos = 0
    for i, (s, e, name) in enumerate(spans, 1):
        ph = f"⟦P{i:03d}⟧"
        mapping[ph] = text[s:e]
        out.append(text[pos:s])
        out.append(ph)
        pos = e
        log.info("mask %s %s -> %r (%s)", ph, name, text[s:e], f"{s}-{e}")
    out.append(text[pos:])
    return "".join(out), mapping


def unmask(text: str, mapping: dict[str, str]) -> str:
    """Restore originals in a single pass (no replacement chaining)."""
    return PLACEHOLDER_RE.sub(lambda m: mapping.get(m.group(0), m.group(0)), text)


class PlaceholderReport(BaseModel):
    dropped: list[str] = []
    duplicated: list[str] = []
    unknown: list[str] = []
    reordered: bool = False

    @property
    def ok(self) -> bool:
        return not (self.dropped or self.duplicated or self.unknown or self.reordered)


def check_placeholders(translated: str, mapping: dict[str, str]) -> PlaceholderReport:
    """Detect placeholders dropped, duplicated, invented or reordered by the translator."""
    found = PLACEHOLDER_RE.findall(translated)
    counts: dict[str, int] = {}
    for f in found:
        counts[f] = counts.get(f, 0) + 1
    expected = list(mapping)
    rep = PlaceholderReport(
        dropped=[p for p in expected if p not in counts],
        duplicated=[p for p in expected if counts.get(p, 0) > 1],
        unknown=[p for p in counts if p not in mapping],
    )
    seen: list[str] = []
    for f in found:
        if f in mapping and f not in seen:
            seen.append(f)
    rep.reordered = seen != [p for p in expected if p in seen]
    for kind in ("dropped", "duplicated", "unknown"):
        for p in getattr(rep, kind):
            log.warning("placeholder %s: %s", kind, p)
    if rep.reordered:
        log.warning("placeholders reordered")
    return rep
