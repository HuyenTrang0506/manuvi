from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Optional

import pymupdf
import yaml
from pydantic import BaseModel, Field

MIN_TEXT_CHARS = 10  # fewer chars than this on a page => no usable text layer
LABEL_RX = re.compile(r"\b(\d{1,2})-(\d{1,3})\b")
ZONE = 0.10  # header/footer band, fraction of page height


class IntakeError(Exception):
    """Section mapping cannot be resolved safely; the job must not start."""


class SectionRange(BaseModel):
    section: str
    start: int  # section-relative, 1-based
    end: int
    pdf_pages: list[int] = Field(default_factory=list)


class PageSpec(BaseModel):
    pdf_page: int
    section: Optional[str] = None
    section_page: Optional[int] = None
    label: Optional[str] = None
    char_count: int = 0
    scanned: bool = False


class JobSpec(BaseModel):
    pdf: str
    page_count: int
    mapping: str  # "labels" | "page_map" | "consecutive"
    sections: list[SectionRange]
    pages: list[PageSpec]
    scanned_pages: list[int] = Field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = Field(default_factory=list)


def parse_sections(spec: str) -> list[SectionRange]:
    """Parse "23:1-42,43:1-22" into section-relative ranges (no PDF placement)."""
    out: list[SectionRange] = []
    for item in (s.strip() for s in spec.split(",") if s.strip()):
        try:
            sec, rng = item.split(":", 1)
            a, b = (int(x) for x in rng.split("-", 1))
        except ValueError:
            raise ValueError(f"Bad section item {item!r}; expected SECTION:FIRST-LAST")
        if a < 1 or b < a:
            raise ValueError(f"Bad range in {item!r}")
        out.append(SectionRange(section=sec.strip(), start=a, end=b))
    if not out:
        raise ValueError("No sections given")
    return out


def read_page_labels(doc: "pymupdf.Document", sections: "set[str] | None" = None) -> tuple[dict[str, list[int]], list[int]]:
    """Return ({label: [pdf pages]}, ambiguous_pages) from header/footer bands.

    If `sections` is given, only labels of those sections count (wiring diagrams carry
    unrelated numbers such as "11-21" in their footer band).
    """
    labels: dict[str, list[int]] = defaultdict(list)
    ambiguous: list[int] = []
    for i in range(doc.page_count):
        page = doc[i]
        h, w = page.rect.height, page.rect.width
        found: set[str] = set()
        # Header/footer band by clip; fall back to stand-alone blocks anywhere on the page
        # (rotated wiring pages place the label outside the nominal page rectangle).
        for clip in (pymupdf.Rect(0, 0, w, h * ZONE), pymupdf.Rect(0, h * (1 - ZONE), w, h)):
            for m in LABEL_RX.finditer(page.get_text("text", clip=clip)):
                if sections is None or m.group(1) in sections:
                    found.add(f"{m.group(1)}-{m.group(2)}")
        if not found and sections is not None:
            for blk in page.get_text("blocks"):
                m = LABEL_RX.fullmatch(blk[4].strip())
                if m and m.group(1) in sections:
                    found.add(f"{m.group(1)}-{m.group(2)}")
        if len(found) == 1:
            labels[found.pop()].append(i + 1)
        elif len(found) > 1:
            ambiguous.append(i + 1)
    return dict(labels), ambiguous


def _ranges(nums: list[int]) -> str:
    out, start, prev = [], None, None
    for n in nums:
        if start is None:
            start = prev = n
        elif n == prev + 1:
            prev = n
        else:
            out.append(f"{start}-{prev}" if start != prev else str(start))
            start = prev = n
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(out)


def _load_page_map(path: Path) -> dict[str, int]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = data.get("sections", data)
    return {str(k): int(v) for k, v in data.items()}


def _resolve(doc, secs: list[SectionRange], page_map: Optional[Path]) -> tuple[str, bool, list[str]]:
    """Fill sec.pdf_pages. Returns (mapping mode, needs_review, warnings). Raises IntakeError."""
    n = doc.page_count
    warnings: list[str] = []
    if page_map:
        pm = _load_page_map(page_map)
        for s in secs:
            if s.section not in pm:
                raise IntakeError(f"page-map has no entry for section {s.section}")
            base = pm[s.section]
            s.pdf_pages = [base + (p - 1) for p in range(s.start, s.end + 1)]
        mode, review = "page_map", False
    else:
        labels, ambiguous = read_page_labels(doc, {s.section for s in secs})
        if labels:
            if ambiguous:
                warnings.append(f"Pages with several distinct labels ignored: {_ranges(ambiguous)}")
            for s in secs:
                for p in range(s.start, s.end + 1):
                    lab = f"{s.section}-{p}"
                    hits = labels.get(lab, [])
                    if not hits:
                        raise IntakeError(f"Printed page label {lab} not found in PDF (use --page-map)")
                    if len(hits) > 1:
                        raise IntakeError(f"Printed page label {lab} is duplicated on PDF pages {hits}")
                    s.pdf_pages.append(hits[0])
            mode, review = "labels", False
        else:
            cursor = 1
            for s in secs:
                cnt = s.end - s.start + 1
                s.pdf_pages = list(range(cursor, cursor + cnt))
                cursor += cnt
            warnings.append("No printed page labels and no page map: pages packed consecutively (guess)")
            mode, review = "consecutive", True
    flat = [p for s in secs for p in s.pdf_pages]
    bad = [p for p in flat if p < 1 or p > n]
    if bad:
        raise IntakeError(f"Resolved PDF pages out of range 1-{n}: {bad[:5]}")
    if any(b <= a for a, b in zip(flat, flat[1:])):
        raise IntakeError("Resolved PDF pages are not strictly increasing (duplicate or non-monotonic)")
    return mode, review, warnings


def build_jobspec(pdf: Path, sections: str, page_map: Optional[Path] = None) -> JobSpec:
    secs = parse_sections(sections)
    with pymupdf.open(pdf) as doc:
        n = doc.page_count
        mode, review, warnings = _resolve(doc, secs, page_map)
        pages: list[PageSpec] = []
        for s in secs:
            for i, p in enumerate(s.pdf_pages):
                chars = len(doc[p - 1].get_text("text").strip())
                pages.append(PageSpec(pdf_page=p, section=s.section, section_page=s.start + i,
                                      label=f"{s.section}-{s.start + i}",
                                      char_count=chars, scanned=chars < MIN_TEXT_CHARS))
    scanned = [p.pdf_page for p in pages if p.scanned]
    if scanned:
        warnings.append(f"Scanned pages (no text layer, OCR needed): {scanned}")
    covered = {p.pdf_page for p in pages}
    gaps = [p for p in range(1, n + 1) if p not in covered]
    if gaps:
        warnings.append(f"PDF pages not in this job (head/middle/tail gaps): {_ranges(gaps)}")
    return JobSpec(pdf=Path(pdf).name, page_count=n, mapping=mode, sections=secs, pages=pages,
                   scanned_pages=scanned, needs_review=review or bool(scanned), warnings=warnings)


def write_jobspec(spec: JobSpec, out: Path) -> None:
    out.write_text(json.dumps(spec.model_dump(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
