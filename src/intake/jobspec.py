from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import pymupdf
from pydantic import BaseModel, Field

MIN_TEXT_CHARS = 10  # fewer chars than this on a page => no usable text layer


class SectionRange(BaseModel):
    section: str
    start: int  # section-relative, 1-based
    end: int
    pdf_start: int  # 1-based PDF page
    pdf_end: int


class PageSpec(BaseModel):
    pdf_page: int
    section: Optional[str] = None
    section_page: Optional[int] = None
    char_count: int = 0
    scanned: bool = False


class JobSpec(BaseModel):
    pdf: str
    page_count: int
    sections: list[SectionRange]
    pages: list[PageSpec]
    scanned_pages: list[int] = Field(default_factory=list)
    needs_review: bool = False
    warnings: list[str] = Field(default_factory=list)


def parse_sections(spec: str, page_count: int) -> list[SectionRange]:
    """Parse "23:1-42,43:1-22".

    Each item is `section:first-last` where the range is relative to the section.
    Sections are laid out consecutively in the PDF in the order given, so the
    example maps section 23 to PDF pages 1-42 and section 43 to PDF pages 43-64.
    """
    out: list[SectionRange] = []
    cursor = 1
    for item in (s.strip() for s in spec.split(",") if s.strip()):
        try:
            sec, rng = item.split(":", 1)
            a, b = (int(x) for x in rng.split("-", 1))
        except ValueError:
            raise ValueError(f"Bad section item {item!r}; expected SECTION:FIRST-LAST")
        if a < 1 or b < a:
            raise ValueError(f"Bad range in {item!r}")
        n = b - a + 1
        out.append(SectionRange(section=sec.strip(), start=a, end=b,
                                pdf_start=cursor, pdf_end=cursor + n - 1))
        cursor += n
    if not out:
        raise ValueError("No sections given")
    if cursor - 1 > page_count:
        raise ValueError(f"Sections need {cursor - 1} pages but PDF has {page_count}")
    return out


def build_jobspec(pdf: Path, sections: str) -> JobSpec:
    with pymupdf.open(pdf) as doc:
        n = doc.page_count
        secs = parse_sections(sections, n)
        pages: list[PageSpec] = []
        for s in secs:
            for i, p in enumerate(range(s.pdf_start, s.pdf_end + 1)):
                chars = len(doc[p - 1].get_text("text").strip())
                pages.append(PageSpec(pdf_page=p, section=s.section, section_page=s.start + i,
                                      char_count=chars, scanned=chars < MIN_TEXT_CHARS))
    scanned = [p.pdf_page for p in pages if p.scanned]
    warnings = [f"Scanned pages (no text layer, OCR needed): {scanned}"] if scanned else []
    covered = secs[-1].pdf_end
    if covered < n:
        warnings.append(f"{n - covered} PDF pages not covered by any section")
    return JobSpec(pdf=Path(pdf).name, page_count=n, sections=secs, pages=pages,
                   scanned_pages=scanned, needs_review=bool(scanned), warnings=warnings)


def write_jobspec(spec: JobSpec, out: Path) -> None:
    out.write_text(json.dumps(spec.model_dump(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                   encoding="utf-8")
