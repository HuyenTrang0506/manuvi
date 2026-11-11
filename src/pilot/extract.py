"""Text units of one page with their geometry. Page labels and pure codes stay untouched (not translated)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from src.guard.masking import load_patterns
from src.intake.jobspec import LABEL_RX
from src.render.style import TextStyle, int_to_rgb

MAX_CHARS = 4000
_WORD = re.compile(r"(?<![\w])[A-Za-z]{3,}(?![\w])")
_LABEL_ONLY = re.compile(r"\d{1,2}-\d{1,3}")


@dataclass
class Unit:
    id: str
    kind: str                       # prose | table | label
    text: str
    bbox: tuple[float, float, float, float]
    rotate: int = 0
    max_size: float = 10.0
    style: TextStyle | None = None  # original font, size, colour, alignment, line pitch, span rectangles


def needs_translation(text: str) -> bool:
    """False for page labels, codes, numbers and short all-caps abbreviations (ECU, GND, ON)."""
    t = text.strip()
    if not t or _LABEL_ONLY.fullmatch(t):
        return False
    for _, rx in load_patterns():
        t = rx.sub(" ", t)
    return any(not (w.isupper() and len(w) <= 3) for w in _WORD.findall(t))


def _rotation(d) -> int:
    horizontal = abs(d[0]) >= abs(d[1])
    if horizontal:
        return 0 if d[0] >= 0 else 180
    return 90 if d[1] < 0 else 270


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
        lw = ref[0] - 0.0
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
        del lw
    return st


def _base_off(lines: list[dict], rect: pymupdf.Rect, rot: int) -> float:
    o = lines[0]["spans"][0]["origin"]
    top = {0: rect.y0, 180: -rect.y1, 90: rect.x0, 270: -rect.x1}[rot]
    return round(_across(o, rot) - top, 2)


def page_units(page: pymupdf.Page) -> tuple[list[Unit], int]:
    """(units to translate, count of text blocks kept as they are)."""
    units: list[Unit] = []
    kept = 0
    cells: list[pymupdf.Rect] = []
    tables: list[pymupdf.Rect] = []

    def add(kind, text, rect, rot=0, style: TextStyle | None = None):
        nonlocal kept
        text = " ".join(text.split())[:MAX_CHARS]
        if not needs_translation(text):
            kept += 1
            return
        units.append(Unit(f"C{len(units) + 1:03d}", kind, text, tuple(round(float(v), 2) for v in rect),
                          rot, style.size if style else 10.0, style))

    try:
        for tab in page.find_tables().tables:
            tables.append(pymupdf.Rect(tab.bbox))
            for row in tab.rows:
                for c in row.cells:
                    if c is None:
                        continue
                    r = pymupdf.Rect(c)
                    d = page.get_text("dict", clip=r)
                    lines = [ln for b in d["blocks"] if b.get("type") == 0 for ln in b["lines"]
                             if "".join(s["text"] for s in ln["spans"]).strip()]
                    if not lines:
                        continue
                    txt = " ".join("".join(s["text"] for s in ln["spans"]) for ln in lines)
                    tb = pymupdf.Rect(lines[0]["bbox"])
                    for ln in lines[1:]:
                        tb |= pymupdf.Rect(ln["bbox"])
                    cells.append(r)
                    rot = _rotation(lines[0]["dir"])
                    st = block_style(lines, r, rot)
                    if rot == 0:  # text may grow to the right edge of its cell (minus its own padding) and down to the cell floor
                        pad = min(max(tb.x0 - r.x0, 0.0), 4.0)
                        box = pymupdf.Rect(tb.x0, tb.y0, max(tb.x1, r.x1 - pad), max(tb.y1, r.y1 - 1.0))
                        if st.align == "center":
                            box = pymupdf.Rect(r.x0 + pad, tb.y0, r.x1 - pad, max(tb.y1, r.y1 - 1.0))
                        elif st.align == "right":
                            box = pymupdf.Rect(r.x0 + pad, tb.y0, tb.x1, max(tb.y1, r.y1 - 1.0))
                    else:
                        box = tb
                    st.base_off = _base_off(lines, box, rot)
                    add("table", txt, box, rot, st)
    except Exception:
        pass
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        r = pymupdf.Rect(b["bbox"])
        if any(r in c or (r & c).get_area() > 0.5 * r.get_area() for c in cells + tables):
            continue
        lines = [ln for ln in b["lines"] if ln["spans"]]
        txt = " ".join("".join(s["text"] for s in ln["spans"]) for ln in lines).strip()
        if not txt:
            continue
        rot = _rotation(lines[0]["dir"])
        st = block_style(lines, None, rot)
        st.base_off = _base_off(lines, r, rot)
        kind = "prose" if len(txt) > 40 and len(lines) > 1 else "label"
        add(kind, txt, r, rot, st)
    return units, kept
