from __future__ import annotations

from pathlib import Path
from typing import Optional

import pymupdf
import yaml
from pydantic import BaseModel

from .jobspec import MIN_TEXT_CHARS

CATEGORIES = ("text", "table", "diagram", "mixed", "scanned")
DIAGRAM_DRAWINGS = 15  # vector paths needed to count as a diagram


class PageClass(BaseModel):
    pdf_page: int
    auto: str
    final: str
    override: Optional[str] = None
    overridden: bool = False
    signals: dict[str, int] = {}


def load_overrides(path: Optional[Path]) -> dict[int, str]:
    """overrides.yaml: `pages: {12: diagram, 13: table}` (flat mapping also accepted)."""
    if not path:
        return {}
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    data = data.get("pages", data)
    out: dict[int, str] = {}
    for k, v in data.items():
        if v not in CATEGORIES:
            raise ValueError(f"Invalid category {v!r} for page {k}; use one of {CATEGORIES}")
        out[int(k)] = v
    return out


def classify_page(page: "pymupdf.Page") -> tuple[str, dict[str, int]]:
    chars = len(page.get_text("text").strip())
    images = len(page.get_images())
    try:
        tables = len(page.find_tables().tables)
    except Exception:
        tables = 0
    # Table borders are drawings too; subtract them from the diagram signal.
    drawings = len(page.get_drawings())
    signals = {"chars": chars, "images": images, "tables": tables, "drawings": drawings}
    if chars < MIN_TEXT_CHARS:
        return "scanned", signals
    has_table = tables > 0
    has_diagram = images > 0 or (drawings >= DIAGRAM_DRAWINGS and not has_table) or \
        (has_table and drawings >= DIAGRAM_DRAWINGS * 4)
    if has_table and has_diagram:
        return "mixed", signals
    if has_table:
        return "table", signals
    if has_diagram:
        return "diagram", signals
    return "text", signals


def classify_pdf(pdf: Path, overrides: Optional[dict[int, str]] = None) -> list[PageClass]:
    overrides = overrides or {}
    out: list[PageClass] = []
    with pymupdf.open(pdf) as doc:
        for i in range(doc.page_count):
            n = i + 1
            auto, sig = classify_page(doc[i])
            ov = overrides.get(n)
            out.append(PageClass(pdf_page=n, auto=auto, final=ov or auto, override=ov,
                                 overridden=ov is not None and ov != auto, signals=sig))
    return out
