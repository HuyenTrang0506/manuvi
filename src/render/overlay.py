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


def draw_fit(page: pymupdf.Page, rect: pymupdf.Rect, rotate: int, fit: Fit, lh: float | None = None,
             color=(0, 0, 0)) -> None:
    lh = fit.lh if lh is None else lh
    for i, line in enumerate(fit.lines):
        p = line_origin(rect, rotate, fit.size * (ASC + i * lh))
        page.insert_text(p, line, fontsize=fit.size, fontname=FONTNAME, rotate=rotate, color=color)


def place_text(page: pymupdf.Page, font: pymupdf.Font, rect: pymupdf.Rect, text: str,
               rotate: int, max_size: float, floor: float = 5.0, **kw) -> Fit:
    L, T = box_extent(rect, rotate)
    fit = fit_text(text, font, L, T, max_size, floor, **kw)
    draw_fit(page, rect, rotate, fit)
    return fit


# ---- faithful drawing: per-face fonts, colour, alignment, precise redaction --------------------------------------
def mask_spans(page: pymupdf.Page, rects: list[pymupdf.Rect], inset: float = 0.2) -> None:
    """Remove only the characters of the given span rectangles. No fill is painted (lines, shading and images
    under the text stay), and each rectangle is shrunk vertically so a neighbouring line is never touched."""
    n = 0
    for r in rects:
        r = pymupdf.Rect(r)
        dy = r.height * inset
        r = pymupdf.Rect(r.x0 + 0.2, r.y0 + dy, r.x1 - 0.2, r.y1 - dy)
        if r.is_empty:
            continue
        page.add_redact_annot(r, fill=False)
        n += 1
    if n:
        page.apply_redactions(images=pymupdf.PDF_REDACT_IMAGE_NONE, graphics=pymupdf.PDF_REDACT_LINE_ART_NONE)


def register_face(page: pymupdf.Page, face, seen: set | None = None) -> None:
    if seen is not None and face.key in seen:
        return
    page.insert_font(fontname=face.key, fontfile=face.path)
    if seen is not None:
        seen.add(face.key)


def expand_rect(rect: pymupdf.Rect, rotate: int, start_ext: float, end_ext: float) -> pymupdf.Rect:
    r = pymupdf.Rect(rect)
    if rotate == 0:
        r.x0 -= start_ext
        r.x1 += end_ext
    elif rotate == 180:
        r.x1 += start_ext
        r.x0 -= end_ext
    elif rotate == 90:
        r.y1 += start_ext
        r.y0 -= end_ext
    else:
        r.y0 -= start_ext
        r.y1 += end_ext
    return r


def _shift(p: pymupdf.Point, rotate: int, dx: float) -> pymupdf.Point:
    return {0: pymupdf.Point(p.x + dx, p.y), 90: pymupdf.Point(p.x, p.y - dx),
            180: pymupdf.Point(p.x - dx, p.y), 270: pymupdf.Point(p.x, p.y + dx)}[rotate]


def draw_faithful(page: pymupdf.Page, rect: pymupdf.Rect, rotate: int, ft, face, font: pymupdf.Font, align: str,
                  color=(0, 0, 0), seen: set | None = None) -> pymupdf.Rect:
    """Draw fitted lines. Returns the page-space rectangle the text occupies."""
    r2 = expand_rect(rect, rotate, ft.start_ext, ft.end_ext)
    register_face(page, face, seen)
    W = ft.width
    widest = 0.0
    for i, line in enumerate(ft.lines):
        lw = font.text_length(line, fontsize=ft.size)
        widest = max(widest, lw)
        dx = 0.0 if align == "left" else (W - lw) / 2 if align == "center" else W - lw
        p = _shift(line_origin(r2, rotate, ft.size * (ft.base + i * ft.lh)), rotate, max(dx, 0.0))
        page.insert_text(p, line, fontsize=ft.size, fontname=face.key, rotate=rotate, color=color)
    h = ft.size * (ft.base + (len(ft.lines) - 1) * ft.lh + ft.desc)
    L = widest if align == "left" else W
    if rotate == 0:
        return pymupdf.Rect(r2.x0, r2.y0, r2.x0 + L, r2.y0 + h)
    if rotate == 180:
        return pymupdf.Rect(r2.x1 - L, r2.y1 - h, r2.x1, r2.y1)
    if rotate == 90:
        return pymupdf.Rect(r2.x0, r2.y1 - L, r2.x0 + h, r2.y1)
    return pymupdf.Rect(r2.x1 - h, r2.y0, r2.x1, r2.y0 + L)
