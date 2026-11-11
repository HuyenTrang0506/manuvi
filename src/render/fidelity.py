"""Fidelity analysis: how close is the Vietnamese page to the original page (fonts, sizes, positions, lines)?

Vietnamese is SIMULATED by a deterministic expansion of the English words (no model, no cost), so the numbers show
what the renderer does to text that is 15 to 35 percent longer. The report holds numbers and font names only,
never page text.
"""
from __future__ import annotations

import json
import re
import statistics
import time
import zlib
from collections import Counter, defaultdict
from pathlib import Path

import pymupdf

from .fonts import FontSet, classify_font
from .page import Segment, SegResult, anchor_drift, render_document
from .synth import _diacritic, _protected_spans

PAD = "ươạảấềộ"
OVERLAP_MIN = 1.0   # pt^2 of new overlap between two placed text boxes that counts as a collision


def expand_vi(text: str, lo: float = 1.15, hi: float = 1.35) -> str:
    """Vietnamese-like text, deterministic: every plain word gets diacritics and grows by lo..hi (hash of the word).

    Protected codes, tokens with digits and short all-caps abbreviations stay as they are."""
    spans = _protected_spans(text)

    def word(w: str) -> str:
        core = re.match(r"^(\W*)(.*?)(\W*)$", w, re.S)
        pre, mid, post = core.groups()
        if not re.search(r"[A-Za-z]", mid) or any(c.isdigit() for c in mid) or (mid.isupper() and len(mid) <= 3):
            return w
        h = zlib.crc32(mid.encode())
        f = lo + (h % 1000) / 1000.0 * (hi - lo)
        out = _diacritic(mid)
        n = max(len(out), int(round(len(mid) * f + 0.2)))
        k = 0
        while len(out) < n:
            out += PAD[(h + k) % len(PAD)]
            k += 1
        return pre + out + post

    def chunk(c: str) -> str:
        return "".join(p if (not p or p.isspace()) else word(p) for p in re.split(r"(\s+)", c))

    out, pos = [], 0
    for a, b in spans:
        out.append(chunk(text[pos:a]))
        out.append(text[a:b])
        pos = b
    out.append(chunk(text[pos:]))
    return "".join(out)


def _legacy_max(kind: str, size: float) -> float:
    return 8.0 if kind == "table" else min(size, 11.0)


def _rect(t):
    return pymupdf.Rect(t)


def _overlaps(segs: list[Segment], res: list[SegResult]) -> int:
    items = [(pymupdf.Rect(s.bbox), pymupdf.Rect(r.placed)) for s, r in zip(segs, res)
             if r.placed is not None and r.stage != "legend"]
    n = 0
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            (o1, p1), (o2, p2) = items[i], items[j]
            if (p1 & p2).get_area() > OVERLAP_MIN and (o1 & o2).get_area() <= OVERLAP_MIN:
                n += 1
    return n


def simulate_page(pdf: Path, p0: int, engine: str, fontset: FontSet, keep_to: Path | None = None) -> dict:
    """Render one page with simulated Vietnamese; return per-block numbers (no text)."""
    from src.pilot.extract import page_units

    with pymupdf.open(str(pdf)) as doc:
        units, kept = page_units(doc[p0])
    segs = []
    for u in units:
        st = u.style if engine == "faithful" else None
        mx = u.max_size if engine == "faithful" else _legacy_max(u.kind, u.style.size if u.style else u.max_size)
        segs.append(Segment(p0, u.bbox, u.kind, u.text, expand_vi(u.text), rotate=u.rotate, max_size=mx, style=st))
    out = keep_to or Path(f"/tmp/manuvi-fid-{p0}-{engine}.pdf")
    results = render_document(pdf, segs, out, fontfile=fontset, pages=[p0])
    pr = results[0]
    blocks = []
    for u, s, r in zip(units, segs, pr.segs):
        st = u.style
        if engine == "legacy":
            r.size0, r.lines0, r.font_from = st.size, st.lines, st.font
            r.font_to = Path(fontset.face().path).stem
            r.shrunk90 = r.size < 0.9 * st.size - 1e-6
            if r.placed:
                r.drift = anchor_drift(pymupdf.Rect(s.bbox), pymupdf.Rect(r.placed), s.rotate, "left")
        match = classify_font(r.font_from) == classify_font(r.font_to)
        grow = len(s.target) / max(1, len(s.source))
        blocks.append({"kind": s.kind, "font_from": r.font_from, "font_to": r.font_to, "font_match": match,
                       "size0": r.size0, "size": round(r.size, 2), "ratio": r.ratio, "drift": r.drift,
                       "lines0": r.lines0, "lines": r.lines, "stage": r.stage, "overflow": bool(r.overflow),
                       "legend": r.stage == "legend", "needs_review": r.needs_review, "below90": r.shrunk90,
                       "growth": round(grow, 3), "box": list(r.box), "room": list(r.room)})
    return {"blocks": blocks, "kept": kept, "overlaps": _overlaps(segs, pr.segs), "legend_page": pr.legend}


def _stats(vals: list[float]) -> dict:
    if not vals:
        return {"min": 0, "median": 0, "p10": 0}
    v = sorted(vals)
    return {"min": round(v[0], 3), "median": round(statistics.median(v), 3), "p10": round(v[max(0, int(0.1 * len(v)) - 0)], 3)}


def summarize(blocks: list[dict], overlaps: int = 0) -> dict:
    n = len(blocks)
    pct = lambda k: round(100.0 * sum(1 for b in blocks if k(b)) / n, 1) if n else 0.0
    fm = defaultdict(lambda: [0, 0])
    for b in blocks:
        fm[b["font_from"]][0] += 1
        fm[b["font_from"]][1] += b["font_match"] and b["font_from"] != ""
    drift = [b["drift"] for b in blocks]
    return {"blocks": n, "size_ratio": _stats([b["ratio"] for b in blocks]),
            "below90_pct": pct(lambda b: b["below90"]), "below90_n": sum(b["below90"] for b in blocks),
            "overflow": sum(b["overflow"] for b in blocks), "legend": sum(b["legend"] for b in blocks),
            "needs_review": sum(b["needs_review"] for b in blocks),
            "font_match_pct": pct(lambda b: b["font_match"]),
            "line_count_changed_pct": pct(lambda b: b["lines"] != b["lines0"]),
            "drift_pt": {"median": round(statistics.median(drift), 2) if drift else 0, "max": round(max(drift, default=0), 2),
                         "over2_pct": pct(lambda b: b["drift"] > 2.0)},
            "overlaps": overlaps,
            "stages": dict(Counter(b["stage"] for b in blocks)),
            "fonts": {k: {"blocks": v[0], "matched": int(v[1])} for k, v in sorted(fm.items(), key=lambda kv: -kv[1][0])}}


def analyze(pdf: Path, section: str, pages: int, engines: tuple[str, ...] = ("faithful",),
            fontset: FontSet | None = None, progress=lambda m: None, labels: list[str] | None = None,
            keep_dir: Path | None = None) -> dict:
    from src.pilot.sample import choose_sample, classify_all, section_pages

    fontset = fontset or FontSet()
    doc = pymupdf.open(str(pdf))
    with doc:
        found, warns = section_pages(doc, section)
        found = classify_all(doc, found)
    if labels:
        sample = [p for p in found if p.label in labels]
    else:
        sample = [p for p in choose_sample(found, pages) if p.kind != "scanned"]
    rep: dict = {"section": section, "pages": [], "engines": {}, "font_files": {},
                 "simulation": "deterministic expansion x1.15-1.35, no model"}
    for eng in engines:
        allb, ov, per = [], 0, []
        for sp in sample:
            t0 = time.time()
            keep = (keep_dir / f"{eng}-p{sp.pdf_page:04d}.pdf") if keep_dir else None
            r = simulate_page(pdf, sp.pdf_page - 1, eng, fontset, keep)
            s = summarize(r["blocks"], r["overlaps"])
            per.append({"label": sp.label, "pdf_page": sp.pdf_page, "type": sp.kind, "kept_as_printed": r["kept"],
                        "legend_page": r["legend_page"], "seconds": round(time.time() - t0, 1), **s})
            allb += r["blocks"]
            ov += r["overlaps"]
            progress(f"{eng} {sp.label}: {s['blocks']} blocks")
        rep["engines"][eng] = {"total": summarize(allb, ov), "pages": per}
    rep["font_files"] = {k: Path(fontset.face(k).path).name for k in ("ArialMT", "Arial-BoldMT", "MSGothic", "Helvetica",
                                                                       "SymbolMT", "Arial-ItalicMT")}
    return rep


def to_markdown(rep: dict) -> str:
    L = [f"# Fidelity report, section {rep['section']}", "", f"Vietnamese is simulated ({rep['simulation']}). "
         "Numbers only; no page text.", ""]
    for eng, d in rep["engines"].items():
        t = d["total"]
        L += [f"## Engine `{eng}`: {t['blocks']} blocks on {len(d['pages'])} pages", "",
              f"- size ratio (Vietnamese / original): min {t['size_ratio']['min']}, p10 {t['size_ratio']['p10']}, "
              f"median {t['size_ratio']['median']}",
              f"- blocks below 90 % of original size: {t['below90_n']} ({t['below90_pct']} %)",
              f"- overflow: {t['overflow']}; legend fallback: {t['legend']}; needs_review: {t['needs_review']}; "
              f"overlaps: {t['overlaps']}",
              f"- font family match: {t['font_match_pct']} %; line count changed: {t['line_count_changed_pct']} %",
              f"- position drift: median {t['drift_pt']['median']} pt, max {t['drift_pt']['max']} pt, "
              f"over 2 pt: {t['drift_pt']['over2_pct']} %",
              f"- stages: {t['stages']}", ""]
        L += ["| Page | Type | Blocks | Ratio min / median | <90 % | Overflow | Legend | Review | Lines changed % | Drift max |",
              "|---|---|---|---|---|---|---|---|---|---|"]
        for p in d["pages"]:
            L.append(f"| {p['label']} | {p['type']} | {p['blocks']} | {p['size_ratio']['min']} / {p['size_ratio']['median']} "
                     f"| {p['below90_n']} | {p['overflow']} | {p['legend']} | {p['needs_review']} "
                     f"| {p['line_count_changed_pct']} | {p['drift_pt']['max']} |")
        L += ["", "| Original font | Blocks | Same family and weight |", "|---|---|---|"]
        for k, v in t["fonts"].items():
            L.append(f"| {k or '(unknown)'} | {v['blocks']} | {v['matched']} |")
        L.append("")
    if rep.get("font_files"):
        L += ["Chosen font files: " + ", ".join(f"{k} -> {v}" for k, v in rep["font_files"].items()), ""]
    return "\n".join(L)


def write(rep: dict, out: Path) -> tuple[Path, Path]:
    out.mkdir(parents=True, exist_ok=True)
    j, m = out / "fidelity.json", out / "fidelity.md"
    j.write_text(json.dumps(rep, indent=1), "utf-8")
    m.write_text(to_markdown(rep), "utf-8")
    return j, m
