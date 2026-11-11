"""Deterministic, offline metrics. Hard metrics (protected codes, numbers/units, safety signals) gate on any
drop; soft metrics gate on a tolerance (see report.py). Every function is pure: (case, target) -> MetricResult."""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from typing import Optional

from src.guard.masking import check_placeholders, load_patterns, mask
from src.guard.signal_words import _count as _signal_count
from src.guard.signal_words import load_signal_table

from .cases import EvalCase, GlossaryTerm, nfc

HARD = ("protected_codes", "number_unit", "safety_signals", "kept_names")
SOFT = ("glossary_hit", "length_ratio", "leakage", "placeholder_roundtrip")
LOWER_IS_BETTER = ("leakage",)
NEAR_HARD = ("leakage",)             # soft tolerance does not apply: absolute limits in report.gate()
LEAKAGE_MAX_OVERALL = 0.005          # founder rule: <= 0.5% leftover English words overall, 0 in p0 cases
# Brand names that legitimately stay in English (never counted as leakage).
BRAND_NAMES = frozenset({"kobelco", "komatsu", "hitachi", "caterpillar", "denso", "bosch", "yanmar", "isuzu",
                         "hino", "toyota", "cummins", "perkins", "deutz", "kubota", "hyundai", "volvo"})
LENGTH_BAND = (0.6, 1.9)  # VI chars / EN chars; EN->VI expands, short strings vary a lot


@dataclass
class MetricResult:
    score: Optional[float]            # 0..1, None = not applicable to this case
    passed: Optional[bool] = None     # pass/fail view of the same result
    detail: dict = field(default_factory=dict)


def norm(s: str) -> str:
    """NFC + NBSP/thin spaces folded to a plain space."""
    s = nfc(s)
    return re.sub(r"[   ]", " ", s)


def fold(s: str) -> str:
    return norm(s).casefold()


_OLD_TONE = re.compile(r"(?<!q)(oa|oe|uy)([\u0300\u0301\u0309\u0303\u0323])")


def fold_vi(s: str) -> str:
    """Matching key for Vietnamese: NFC, case-insensitive, hyphens/spaces ignored ('rơ-le' == 'rơ le' == 'rơle'),
    old and new tone placement equal ('hoà' == 'hòa', 'thuỷ' == 'thủy'). Vietnamese has no inflection, so a
    plain substring test on this key is the whole matcher."""
    t = unicodedata.normalize("NFD", norm(s).casefold())
    t = _OLD_TONE.sub(lambda m: m.group(1)[0] + m.group(2) + m.group(1)[1], t)
    t = unicodedata.normalize("NFC", t)
    return re.sub(r"[\s\-\u2010-\u2015]+", "", t)


def forbidden_found(target: str, term: GlossaryTerm) -> list[str]:
    """Forbidden VI variants present as whole words. Codes (`Pin 1`, `C-12`) are masked first so that the forbidden
    'pin' (dry cell) does not fire on the protected 'Pin 1'."""
    if not term.forbidden_vi:
        return []
    try:
        text = fold(mask(nfc(target), load_patterns())[0])
    except ValueError:      # target already contains placeholder brackets
        text = fold(target)
    return [f for f in term.forbidden_vi
            if re.search(r"(?<!\w)" + r"\s+".join(map(re.escape, fold(f).split())) + r"(?!\w)", text)]


def has_form(target_key: str, term: GlossaryTerm) -> Optional[str]:
    """First accepted VI form (canonical, then variants) of `term` found in an already folded target key."""
    for f in term.vi_forms:
        k = fold_vi(f)
        if k and k in target_key:
            return f
    return None


# --- protected codes ---------------------------------------------------------------------------------
def _auto_codes(text: str) -> Counter:
    return Counter(mask(text, load_patterns())[1].values())


def protected_codes(case: EvalCase, target: str) -> MetricResult:
    """Exact (NFC only) preservation of explicit protected strings and of every code the guard detects."""
    src, tgt = nfc(case.source), nfc(target)
    expected: Counter = Counter()
    for item in set(case.protected):
        expected[item] = max(1, src.count(item))
    for item, n in _auto_codes(src).items():
        expected[item] = max(expected[item], n)
    lost = sorted(i for i, n in expected.items() if tgt.count(i) < n)
    extra_counter = _auto_codes(tgt) - _auto_codes(src)
    extra = sorted(i for i in extra_counter.elements() if i not in case.protected and i not in src)
    total = len(expected) or 1
    if not expected and not extra:
        return MetricResult(None, None, {})
    ok = not lost and not extra
    return MetricResult((total - len(lost)) / total if not extra else min((total - len(lost)) / total, 0.99),
                        ok, {"missing": lost, "extra": extra})


# --- numbers and units -------------------------------------------------------------------------------
_UNITS = ["mm²", "mm2", "mm", "kΩ", "MΩ", "mΩ", "Ω", "kV", "mV", "V", "mA", "A", "kW", "W", "Hz", "kPa", "MPa",
          "bar", "N·m", "kgf·m", "rpm", "°C", "°F", "%"]
_UNIT_ALT = "|".join(re.escape(u) for u in sorted(_UNITS, key=len, reverse=True))
NUM_TOKEN = re.compile(
    rf"(?<![A-Za-z0-9.,])(?P<pm>±\s?)?(?P<num>\d+(?:[.,]\d+)?)(?:\s?(?P<unit>{_UNIT_ALT})(?![A-Za-z]))?")


def _canon_text(s: str) -> str:
    s = norm(s)
    s = s.replace("℃", "°C").replace("℉", "°F").replace("Ω", "Ω").replace("µ", "µ")
    s = re.sub(r"\s?°\s?([CF])", r"°\1", s)
    s = re.sub(r"N\s?[•⋅∙·]\s?m", "N·m", s)
    return s


def _strip_codes(text: str, protected: list[str]) -> str:
    """Remove code-like protected strings so their digits are not counted as measured numbers."""
    for rx in [rx for n, rx in load_patterns() if n != "value_unit"]:
        text = rx.sub(" ", text)
    for p in sorted(protected, key=len, reverse=True):
        if re.search(r"[A-Za-z]", NUM_TOKEN.sub("", _canon_text(p)).replace("±", "").replace(" ", "")):
            text = text.replace(nfc(p), " ")
    return text


def number_tokens(text: str, protected: list[str] = ()) -> list[str]:
    t = _canon_text(_strip_codes(nfc(text), list(protected)))
    out = []
    for m in NUM_TOKEN.finditer(t):
        out.append(f"{'±' if m['pm'] else ''}{m['num']}{m['unit'] or ''}")
    return out


def number_unit(case: EvalCase, target: str) -> MetricResult:
    """Multiset equality of number(+unit) tokens. Whitespace between number and unit is ignored (24V == 24 V);
    the decimal separator is NOT (policy keep_source: 0.5 stays 0.5)."""
    s = Counter(number_tokens(case.source, case.protected))
    t = Counter(number_tokens(target, case.protected))
    if not s and not t:
        return MetricResult(None, None, {})
    missing, extra = sorted((s - t).elements()), sorted((t - s).elements())
    sep = [m for m in missing if re.sub(r"[.,]", "", m) in {re.sub(r"[.,]", "", e) for e in extra}]
    total = sum(s.values()) or 1
    return MetricResult(max(0.0, (total - len(missing)) / total) if not extra else
                        min(max(0.0, (total - len(missing)) / total), 0.99),
                        not missing and not extra,
                        {"missing": missing, "extra": extra, "decimal_separator_changed": sep})


# --- glossary ------------------------------------------------------------------------------------------
def _term_re(term_en: str) -> re.Pattern[str]:
    words = [re.escape(w) for w in term_en.split()]
    last = words[-1]
    last = last[:-1] + "(?:y|ies)" if term_en.endswith("y") else last + "(?:e?s)?"
    return re.compile(r"(?<![\w])" + r"\s+".join(words[:-1] + [last]) + r"(?![\w])", re.I)


def find_terms(text: str, glossary: dict[str, GlossaryTerm]) -> list[str]:
    """Glossary keys occurring in text; longest first, no overlapping spans (so 'battery relay' wins)."""
    taken: list[tuple[int, int]] = []
    found: list[str] = []
    for key in sorted(glossary, key=lambda k: (-len(k), k)):
        hit = False
        for m in _term_re(glossary[key].term_en).finditer(text):
            if not any(a < m.end() and m.start() < b for a, b in taken):
                taken.append(m.span())
                hit = True
        if hit:
            found.append(key)
    return found


def glossary_hit(case: EvalCase, target: str, glossary: dict[str, GlossaryTerm]) -> MetricResult:
    """Expected terms found with the canonical VI term or any listed variant (`variants_vi`)."""
    tgt = fold_vi(target)
    hits, misses, variant_hits = [], [], []
    for en in case.expected_terms:
        t = glossary.get(" ".join(en.split()).casefold())
        if t is None:
            raise KeyError(f"{case.id}: expected term {en!r} is not in the glossary")
        form = has_form(tgt, t)
        (hits if form else misses).append(t.term_en)
        if form and fold_vi(form) != fold_vi(t.term_vi):
            variant_hits.append(t.term_en)
    forbidden = sorted({f for en in case.expected_terms if (t := glossary.get(en.casefold()))
                        for f in forbidden_found(target, t)})
    if not case.expected_terms:
        return MetricResult(None, None, {})
    return MetricResult(len(hits) / len(case.expected_terms), not misses and not forbidden,
                        {"hits": hits, "misses": misses, "forbidden": forbidden, "variant_hits": variant_hits})


def consistency(cases: list[EvalCase], targets: dict[str, str], glossary: dict[str, GlossaryTerm]) -> dict:
    """Document level: a term that is rendered with an accepted VI form in some segments must not be missing or
    replaced by a forbidden variant in others (same EN term -> same VI term). Accepted variants count as hits;
    terms rendered with more than one accepted form are listed in `mixed_forms` (reported, not scored)."""
    per: dict[str, dict] = {}
    for c in cases:
        tgt = fold_vi(targets.get(c.id, ""))
        src = c.source
        for p in sorted(c.protected, key=len, reverse=True):
            src = src.replace(p, " ")  # 'terminal 3' is a protected code, not the glossary term
        for key in find_terms(src, glossary):
            t = glossary[key]
            d = per.setdefault(key, {"term": t.term_en, "vi": t.term_vi, "hit": [], "miss": [], "forbidden": [],
                                     "forms": set()})
            form = has_form(tgt, t)
            if form:
                d["hit"].append(c.id)
                d["forms"].add(form)
            else:
                d["miss"].append(c.id)
            if forbidden_found(targets.get(c.id, ""), t):
                d["forbidden"].append(c.id)
    bad = [d for d in per.values() if (d["hit"] and d["miss"]) or d["forbidden"]]
    mixed = [{"term": d["term"], "forms": sorted(d["forms"])} for d in per.values() if len(d["forms"]) > 1]
    for d in per.values():
        d["forms"] = sorted(d["forms"])
    return {"terms_seen": len(per), "inconsistent": sorted(bad, key=lambda d: d["term"]),
            "mixed_forms": sorted(mixed, key=lambda d: d["term"]),
            "score": 1 - len(bad) / len(per) if per else None}


def safety_signals(case: EvalCase, target: str, table: Optional[dict[str, str]] = None) -> MetricResult:
    """Each EN signal word in the source needs its fixed VI form in the target (case-insensitive, NFC).
    A shouted VI signal the source does not have (downgrade/upgrade) also fails."""
    table = table or load_signal_table()
    src, tgt = nfc(case.source), norm(target)
    missing, unexpected = [], []
    n_src = 0
    for en, vi in table.items():
        s = _signal_count(src, en)
        n_src += s
        vi_n = nfc(vi)
        t = len(re.findall(rf"(?<!\w){re.escape(vi_n)}(?!\w)", tgt, flags=re.I))
        t_upper = len(re.findall(rf"(?<!\w){re.escape(vi_n.upper())}(?!\w)", tgt))
        if t < s:
            missing.append(vi)
        if t_upper > s:
            unexpected.append(vi)
    if n_src == 0 and not unexpected:
        return MetricResult(None, None, {})
    ok = not missing and not unexpected
    return MetricResult(1.0 if ok else 0.0, ok, {"missing": missing, "unexpected": unexpected})


# --- names and abbreviations that must stay in English ---------------------------------------------------
def kept_names(case: EvalCase, target: str) -> MetricResult:
    """Every string in `case.keep` (ECU, SOL, GND, B+, wire colour codes, brand names) appears in the output
    exactly as written (NFC, case-sensitive, whole token). Not applicable when the case lists none."""
    if not case.keep:
        return MetricResult(None, None, {})
    tgt = nfc(target)
    missing = [k for k in case.keep
               if not re.search(r"(?<![\w])" + re.escape(nfc(k)) + r"(?![\w])", tgt)]
    return MetricResult(1.0 if not missing else 0.0, not missing, {"missing": missing})


# --- length, leakage, placeholders ----------------------------------------------------------------------
def length_ratio(case: EvalCase, target: str, band: tuple[float, float] = LENGTH_BAND) -> MetricResult:
    s, t = len(nfc(case.source).strip()), len(nfc(target).strip())
    r = t / s if s else 0.0
    ok = band[0] <= r <= band[1]
    return MetricResult(1.0 if ok else 0.0, ok, {"ratio": round(r, 3), "band": list(band)})


_WORD = re.compile(r"[A-Za-zÀ-ỹ]+")
_ASCII_WORD = re.compile(r"(?<![\w])[A-Za-z]{3,}(?![\w])")


def leakage(case: EvalCase, target: str, glossary: Optional[dict[str, GlossaryTerm]] = None) -> MetricResult:
    """Share of target words that are untranslated English: ASCII words (>=3 letters) that occur in the source,
    outside protected strings, `keep` names, brand names, all-caps tokens (ON, OFF, GND) and words of the expected
    VI forms. `detail.leaked` lists the leftover words (no 'English term (Vietnamese)' pairs either way)."""
    text = nfc(target)
    for p in sorted([*case.protected, *case.keep], key=len, reverse=True):
        text = text.replace(nfc(p), " ")
    for rx in [rx for n, rx in load_patterns()]:
        text = rx.sub(" ", text)
    src_words = {w.lower() for w in _ASCII_WORD.findall(nfc(case.source))}
    allowed: set[str] = set(BRAND_NAMES)
    for en in case.expected_terms:
        t = (glossary or {}).get(en.casefold())
        if t:
            for f in t.vi_forms:
                allowed |= {w.lower() for w in _WORD.findall(f)}
    total = len(_WORD.findall(text)) or 1
    leaked = [w for w in _ASCII_WORD.findall(text) if not w.isupper() and w.lower() in src_words
              and w.lower() not in allowed]
    ratio = len(leaked) / total
    return MetricResult(ratio, ratio == 0, {"leaked": sorted(set(w.lower() for w in leaked)), "ratio": round(ratio, 4)})


def placeholder_roundtrip(masked_target: Optional[str], mapping: Optional[dict[str, str]]) -> MetricResult:
    """Placeholders sent to the model must come back exactly once each, none invented. Reordering is reported
    but does not fail (word order differs between EN and VI)."""
    if masked_target is None or not mapping:
        return MetricResult(None, None, {})
    rep = check_placeholders(masked_target, mapping)
    ok = not (rep.dropped or rep.duplicated or rep.unknown)
    return MetricResult(1.0 if ok else 0.0, ok, rep.model_dump())
