"""Per-page QA of a rendered document: ok / warn / error, JSON output.

error = overflow or protected-token violation (tokens re-extracted from the OUTPUT PDF text layer).
warn  = floor-size text, legend fallback, extension into free space.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pymupdf

from ..guard.numbers import extract_numbers
from .page import PageResult, Segment, patterns

_WS = re.compile(r"\s+")  # str \s includes NBSP


def norm(text: str) -> str:
    return _WS.sub(" ", text).strip()


def protected_tokens(text: str) -> Counter:
    t = norm(text)
    c: Counter = Counter()
    for _, rx in patterns():
        c.update(norm(m.group(0)) for m in rx.finditer(t))
    return c


def build_report(out_pdf: str | Path, segments: list[Segment], results: list[PageResult],
                 pages: list[int] | None = None, include_tokens: bool = True) -> dict:
    """`pages`: source page indices in output order (None = same as output order)."""
    doc = pymupdf.open(str(out_pdf))
    out_pages = []
    for k, pr in enumerate(results):
        page = doc[k]
        text = page.get_text()
        segs = [s for s in segments if s.page == pr.page]
        want: Counter = Counter()
        for s in segs:
            want.update(protected_tokens(s.source))
        missing = want - protected_tokens(text)
        want_num = Counter(n for s in segs if s.kind == "table" for n in extract_numbers(s.source, patterns()))
        num_missing = want_num - Counter(extract_numbers(norm(text), patterns()))
        errors, warns = [], []
        if any(r.overflow for r in pr.segs):
            errors.append("overflow")
        if missing:
            errors.append("protected_token_violation")
        if num_missing or any(r.numbers_missing or r.numbers_extra for r in pr.segs):
            errors.append("table_number_violation")
        if any(r.floor or r.stage.endswith("hard_floor") for r in pr.segs):
            warns.append("floor_size")
        if pr.legend:
            warns.append("legend_fallback")
        if any(r.extension > 0 for r in pr.segs):
            warns.append("extension")
        entry = {"page": pr.page + 1, "status": "error" if errors else "warn" if warns else "ok",
                 "errors": errors, "warnings": warns, "segments": len(pr.segs),
                 "min_size": round(min((r.size for r in pr.segs), default=0.0), 2),
                 "legend": pr.legend, "mediabox_grown": round(pr.grown, 1)}
        if include_tokens:
            entry["missing_tokens"] = sorted((missing + Counter(num_missing)).elements())
        else:
            entry["missing_token_count"] = sum(missing.values()) + sum(num_missing.values())
        out_pages.append(entry)
    doc.close()
    summary = Counter(p["status"] for p in out_pages)
    return {"summary": {"ok": summary["ok"], "warn": summary["warn"], "error": summary["error"],
                        "pages": len(out_pages)}, "pages": out_pages}


def to_json(report: dict) -> str:
    return json.dumps(report, ensure_ascii=False, indent=2)
