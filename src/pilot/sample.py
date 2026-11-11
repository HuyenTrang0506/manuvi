"""Section page discovery (by printed label) and a deterministic page sample covering every page type."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

from src.intake.classify import classify_page
from src.intake.jobspec import read_page_labels

ORDER = ("text", "table", "diagram", "mixed", "scanned")  # round-robin order; scanned gets at most 1 slot
MAX_SCANNED = 1


@dataclass(frozen=True)
class SectionPage:
    pdf_page: int          # 1-based
    label: str             # printed label, e.g. "43-12"
    section_page: int
    kind: str = ""         # classifier category


def section_pages(doc: "pymupdf.Document", section: str) -> tuple[list[SectionPage], list[str]]:
    """Pages whose printed label is '<section>-<n>', sorted by n. Duplicated labels are skipped with a warning."""
    labels, ambiguous = read_page_labels(doc, {section})
    out, warns = [], []
    for lab, pages in labels.items():
        sec, _, n = lab.partition("-")
        if sec != section or not n.isdigit():
            continue
        if len(pages) > 1:
            warns.append(f"label {lab} appears on PDF pages {pages}; skipped")
            continue
        out.append(SectionPage(pages[0], lab, int(n)))
    if ambiguous:
        warns.append(f"{len(ambiguous)} pages with several distinct labels ignored")
    return sorted(out, key=lambda p: (p.section_page, p.pdf_page)), warns


def classify_all(doc: "pymupdf.Document", pages: list[SectionPage]) -> list[SectionPage]:
    res = []
    for p in pages:
        try:
            kind, _ = classify_page(doc[p.pdf_page - 1])
        except Exception:  # a page the classifier cannot read is reported later as unreadable
            kind = "scanned"
        res.append(SectionPage(p.pdf_page, p.label, p.section_page, kind))
    return res


def _spread(items: list[SectionPage], k: int) -> list[SectionPage]:
    m = len(items)
    return [items[int((i + 0.5) * m / k)] for i in range(k)]


def choose_sample(pages: list[SectionPage], n: int) -> list[SectionPage]:
    """Deterministic: round-robin over page types (text, table, diagram, mixed, scanned), evenly spread inside
    each type, result in manual order. Scanned pages take at most one slot."""
    pages = sorted(pages, key=lambda p: (p.section_page, p.pdf_page))
    n = max(1, min(n, len(pages)))
    by = {k: [p for p in pages if p.kind == k] for k in ORDER}
    cap = {k: min(len(by[k]), MAX_SCANNED) if k == "scanned" else len(by[k]) for k in ORDER}
    quota = {k: 0 for k in ORDER}
    left = n
    while left:
        moved = False
        for k in ORDER:
            if left and quota[k] < cap[k]:
                quota[k] += 1
                left -= 1
                moved = True
        if not moved:
            break
    picked = [p for k in ORDER if quota[k] for p in _spread(by[k], quota[k])]
    return sorted(picked, key=lambda p: p.pdf_page)
