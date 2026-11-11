"""Render one pilot page: Vietnamese text in place, visible review markers, banners for unprocessed pages."""
from __future__ import annotations

from pathlib import Path

import pymupdf

from src.render.page import PageResult, Segment, render_document
from src.render.qa_report import build_report

MARK = "[!] "                     # text marker (readable in greyscale and by screen readers)
RED = (0.75, 0.0, 0.0)


def mark_boxes(page: pymupdf.Page, boxes: list[tuple[float, float, float, float]]) -> None:
    """Dashed frame plus a warning triangle at the top-left corner: text + shape, never colour alone."""
    for b in boxes:
        r = pymupdf.Rect(b)
        page.draw_rect(r, color=RED, width=1.2, dashes="[3 2] 0")
        x, y = max(r.x0 - 9.0, 1.0), max(r.y0 - 1.0, 1.0)
        tri = [pymupdf.Point(x, y + 8), pymupdf.Point(x + 4.5, y), pymupdf.Point(x + 9, y + 8)]
        page.draw_polyline(tri + [tri[0]], color=RED, fill=RED, closePath=True)
        page.insert_text((x + 3.2, y + 7.0), "!", fontsize=6.5, fontname="hebo", color=(1, 1, 1))


def banner(page: pymupdf.Page, text: str, fontfile) -> None:
    path = fontfile.face().path if hasattr(fontfile, "face") else fontfile
    page.insert_font(fontname="vib", fontfile=path)
    r = pymupdf.Rect(12, 12, page.rect.width - 12, 40)
    page.draw_rect(r, color=RED, fill=(1, 1, 1), width=1.5, dashes="[4 2] 0")
    page.insert_text((20, 30), MARK + text, fontsize=10, fontname="vib", color=RED)


def render_page(src: Path, p0: int, segs: list[Segment], review_boxes: list, out: Path,
                fontfile: str) -> tuple[PageResult, dict]:
    """Render page `p0` (0-based) alone into `out`. Returns the renderer result and the page QA entry."""
    results = render_document(src, segs, out, fontfile=fontfile, pages=[p0])
    qa = build_report(out, segs, results, include_tokens=False)["pages"][0]
    review_boxes = list(review_boxes) + [s.bbox for s, r in zip(segs, results[0].segs) if r.needs_review]
    if review_boxes:
        tmp = out.with_suffix(".mark.pdf")
        doc = pymupdf.open(str(out))
        mark_boxes(doc[0], review_boxes)
        doc.save(str(tmp), garbage=3, deflate=True)
        doc.close()
        tmp.replace(out)
    return results[0], qa


def unprocessed_page(src: Path, p0: int, message: str, out: Path, fontfile: str) -> None:
    """Original page with a banner (needs OCR, render error): never shown as if it were translated."""
    doc = pymupdf.open(str(src))
    doc.select([p0])
    banner(doc[0], message, fontfile)
    doc.save(str(out), garbage=3, deflate=True)
    doc.close()
