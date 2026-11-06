"""BL-US-10b: glossary subset selection, translation memory, translation prompt builder."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Optional

from pydantic import BaseModel

from src.intake.terms import load_patterns

from .store import TermRecord, TermStore

PLACEHOLDER = re.compile(r"⟦P\d{3}⟧")
_PROTECTED = None


def _is_protected_code(term: str) -> bool:
    global _PROTECTED
    if _PROTECTED is None:
        _PROTECTED = load_patterns()
    return any(rx.fullmatch(term.strip()) for _, rx in _PROTECTED)


def select_glossary_subset(source_text: str, store: TermStore, max_terms: int = 30) -> list[TermRecord]:
    """Approved, non-protected terms present in the text. Longest match first; shorter terms inside an
    already matched longer term are not selected separately."""
    terms = [r for r in store.approved() if not r.protected and not _is_protected_code(r.term_en)]
    terms.sort(key=lambda r: (-len(r.term_en), r.key))
    taken: list[tuple[int, int]] = []
    out: list[TermRecord] = []
    for r in terms:
        pat = re.compile(r"(?<![\w])" + r"\s+".join(map(re.escape, r.term_en.split())) + r"(?![\w])", re.I)
        hit = False
        for m in pat.finditer(source_text):
            if not any(a < m.end() and m.start() < b for a, b in taken):
                taken.append(m.span())
                hit = True
        if hit:
            out.append(r)
            if len(out) >= max_terms:
                break
    return out


class TMHit(BaseModel):
    source: str
    target: str
    score: float  # 1.0 = exact


class TranslationMemory:
    """Previously approved segments. Exact then fuzzy (difflib ratio) lookup."""

    def __init__(self) -> None:
        self._segs: list[tuple[str, str]] = []

    def add(self, source: str, target: str) -> None:
        self._segs.append((source, target))

    @staticmethod
    def _norm(s: str) -> str:
        return " ".join(s.split()).casefold()

    def lookup(self, text: str, threshold: float = 0.85, limit: int = 3) -> list[TMHit]:
        q = self._norm(text)
        hits = []
        for s, t in self._segs:
            n = self._norm(s)
            score = 1.0 if n == q else SequenceMatcher(None, q, n).ratio()
            if score >= threshold:
                hits.append(TMHit(source=s, target=t, score=round(score, 3)))
        hits.sort(key=lambda h: -h.score)
        return hits[:limit]


SYSTEM_RULES = """You are a technical manual translator, English to Vietnamese.
Rules:
1. Keep every protected placeholder of the form ⟦Pnnn⟧ exactly as written, unchanged, in the same order. Never translate, remove, add or renumber them.
2. Use the glossary below for the listed terms; do not use other renderings of those terms.
3. Do not invent content. Translate only what is in the source. If unsure, keep the English and append [?].
4. Do not translate part numbers, codes, values with units, or figure/page references.
5. Output only the Vietnamese translation."""


def build_translation_prompt(chunk: str, subset: list[TermRecord], tm_hits: list[TMHit],
                             prompt_version: str) -> dict:
    """OpenAI-style request payload parts: {'prompt_version', 'messages'}."""
    system = f"{SYSTEM_RULES}\n\n[prompt_version: {prompt_version}]"
    if subset:
        system += "\n\nGlossary (EN => VI):\n" + "\n".join(f"- {r.term_en} => {r.term_vi}" for r in subset)
    if tm_hits:
        system += "\n\nSimilar approved translations (reuse wording where the source matches):\n" + "\n".join(
            f"- [{h.score:.2f}] {h.source} => {h.target}" for h in tm_hits)
    return {"prompt_version": prompt_version,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": chunk}]}
