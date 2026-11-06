"""Mask original text and write replacement text into oriented boxes (vector, no rasterising)."""
from __future__ import annotations

import pymupdf

from .fit import ASC, Fit, fit_text

FONTNAME = "viF"


def reading_rotation(direction: tuple[float, float]) -> int:
    """PyMuPDF `rotate` value for a text line direction (page-space unit vector)."""
    dx, dy = direction
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 180
    return 90 if dy < 0 else 270


def box_extent(rect: pymupdf.Rect, rotate: int) -> tuple[float, float]:
    """(length along text, thickness across text) of a page-space rect."""
    return (rect.width, rect.height) if rotate in (0, 180) else (rect.height, rect.width)


def line_origin(rect: pymupdf.Rect, rotate: int, offset: float) -> pymupdf.Point:
    """Baseline start point for a line whose baseline is `offset` below the reading-frame top."""
    if rotate == 0:
        return pymupdf.Point(rect.x0, rect.y0 + offset)
    if rotate == 90:
        return pymupdf.Point(rect.x0 + offset, rect.y1)
    if rotate == 180:
        return pymupdf.Point(rect.x1, rect.y1 - offset)
    return pymupdf.Point(rect.x1 - offset, rect.y0)


def placed_rect(rect: pymupdf.Rect, rotate: int, fit: Fit) -> pymupdf.Rect:
    """Page-space rect actually covered by the fitted text."""
    L, T = fit.width, fit.height
    if rotate == 0:
        return pymupdf.Rect(rect.x0, rect.y0, rect.x0 + L, rect.y0 + T)
    if rotate == 90:
        return pymupdf.Rect(rect.x0, rect.y1 - L, rect.x0 + T, rect.y1)
    if rotate == 180:
        return pymupdf.Rect(rect.x1 - L, rect.y1 - T, rect.x1, rect.y1)
    return pymupdf.Rect(rect.x1 - T, rect.y0, rect.x1, rect.y0 + L)


def register_font(page: pymupdf.Page, fontfile: str) -> pymupdf.Font:
    page.insert_font(fontname=FONTNAME, fontfile=fontfile)
    return pymupdf.Font(fontfile=fontfile)


def mask_rects(page: pymupdf.Page, rects: list[pymupdf.Rect]) -> None:
    """Remove text under rects (true redaction, keeps images and line art)."""
    for r in rects:
        page.add_redact_annot(r, fill=(1, 1, 1))
    page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE,
                          graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def draw_fit(page: pymupdf.Page, rect: pymupdf.Rect, rotate: int, fit: Fit, lh: float = 1.15,
             color=(0, 0, 0)) -> None:
    for i, line in enumerate(fit.lines):
        p = line_origin(rect, rotate, fit.size * (ASC + i * lh))
        page.insert_text(p, line, fontsize=fit.size, fontname=FONTNAME, rotate=rotate, color=color)


def place_text(page: pymupdf.Page, font: pymupdf.Font, rect: pymupdf.Rect, text: str,
               rotate: int, max_size: float, floor: float = 5.0, **kw) -> Fit:
    L, T = box_extent(rect, rotate)
    fit = fit_text(text, font, L, T, max_size, floor, **kw)
    draw_fit(page, rect, rotate, fit)
    return fit
