"""Original text style of one block, read from the source PDF, used to redraw the Vietnamese text faithfully."""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

Rect4 = tuple[float, float, float, float]


@dataclass
class TextStyle:
    font: str = ""                 # original PDF font name as printed (subset prefix removed)
    size: float = 10.0             # dominant original size (pt)
    color: tuple[float, float, float] = (0.0, 0.0, 0.0)
    bold: bool = False
    italic: bool = False
    align: str = "left"            # left | center | right (in the reading frame)
    pitch: float = 0.0             # original distance between baselines (pt); 0 = unknown
    lines: int = 1                 # original line count
    base_off: float = 0.0          # reading-frame distance from the box edge to the first baseline; 0 = unknown
    spans: list[Rect4] = field(default_factory=list)  # page-space rectangles of the original spans (redaction)


def int_to_rgb(c: int) -> tuple[float, float, float]:
    return ((c >> 16) & 255) / 255.0, ((c >> 8) & 255) / 255.0, (c & 255) / 255.0


def _along(rect: pymupdf.Rect, rot: int) -> tuple[float, float]:
    """(start, end) of a page-space rect along the reading direction."""
    if rot == 0:
        return rect.x0, rect.x1
    if rot == 180:
        return -rect.x1, -rect.x0
    if rot == 90:
        return -rect.y1, -rect.y0
    return rect.y0, rect.y1


def _across(pt, rot: int) -> float:
    """Coordinate of a page point across the reading direction, increasing from the reading-frame top."""
    return {0: pt[1], 180: -pt[1], 90: pt[0], 270: -pt[0]}[rot]


def block_style(lines: list[dict], container: pymupdf.Rect | None = None, rot: int = 0) -> TextStyle:
    """Style of a text block from its `dict` lines (dominant span by character count)."""
    spans = [s for ln in lines for s in ln["spans"] if s["text"].strip()]
    if not spans:
        spans = [s for ln in lines for s in ln["spans"]]
    dom = max(spans, key=lambda s: len(s["text"].strip()))
    fl = int(dom.get("flags", 0))
    st = TextStyle(font=str(dom.get("font", "")).split("+")[-1], size=round(float(dom["size"]), 2),
                   color=int_to_rgb(int(dom.get("color", 0))), bold=bool(fl & 16), italic=bool(fl & 2),
                   lines=len(lines))
    st.spans = [tuple(round(float(v), 2) for v in s["bbox"]) for s in spans]
    rects = [pymupdf.Rect(ln["bbox"]) for ln in lines]
    bl = [_across(ln["spans"][0]["origin"], rot) for ln in lines if ln["spans"]]
    if len(bl) >= 2:
        d = sorted(b - a for a, b in zip(bl, bl[1:]) if b - a > 0.5)
        if d:
            st.pitch = round(d[len(d) // 2], 2)
    ext = [_along(r, rot) for r in rects]
    ref = _along(container, rot) if container is not None else None
    if len(lines) >= 2 and ref is None:
        ref = (min(a for a, _ in ext), max(b for _, b in ext))
    if ref is not None:
        tol = 1.5
        starts, ends = [a for a, _ in ext], [b for _, b in ext]

        if len(lines) >= 2 and max(starts) - min(starts) <= tol:
            st.align = "left"
        elif len(lines) >= 2 and max(ends) - min(ends) <= tol:
            st.align = "right"
        elif len(lines) >= 2 and max(abs((a + b) / 2 - (ref[0] + ref[1]) / 2) for a, b in ext) <= tol:
            st.align = "center"
        elif container is not None and len(lines) == 1:
            ls, le = ext[0][0] - ref[0], ref[1] - ext[0][1]
            if ls > 2.0 and abs(ls - le) <= tol:
                st.align = "center"
            elif ls > 2.0 and le <= tol:
                st.align = "right"

    return st


def base_off(lines: list[dict], rect: pymupdf.Rect, rot: int) -> float:
    o = lines[0]["spans"][0]["origin"]
    top = {0: rect.y0, 180: -rect.y1, 90: rect.x0, 270: -rect.x1}[rot]
    return round(_across(o, rot) - top, 2)




def rotation_of(d) -> int:
    """PyMuPDF `rotate` value for a text line direction vector."""
    if abs(d[0]) >= abs(d[1]):
        return 0 if d[0] >= 0 else 180
    return 90 if d[1] < 0 else 270


def style_for_box(page: pymupdf.Page, bbox) -> tuple[TextStyle, int] | None:
    """Original style (and reading rotation) of the text inside `bbox` on `page`, or None if there is no text layer there."""
    r = pymupdf.Rect(bbox)
    lines = []
    for b in page.get_text("dict", clip=r)["blocks"]:
        if b.get("type") != 0:
            continue
        for ln in b["lines"]:
            spans = [s for s in ln["spans"] if s["text"].strip() and pymupdf.Rect(s["bbox"]).get_area() > 0
                     and r.contains((pymupdf.Rect(s["bbox"]).tl + pymupdf.Rect(s["bbox"]).br) / 2)]
            if spans:
                lines.append({**ln, "spans": spans})
    if not lines:
        return None
    rot = rotation_of(lines[0]["dir"])
    st = block_style(lines, None, rot)
    st.base_off = base_off(lines, r, rot)
    return st, rot
