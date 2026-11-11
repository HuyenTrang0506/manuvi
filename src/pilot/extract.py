"""Text units of one page with their geometry. Page labels and pure codes stay untouched (not translated)."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

from src.guard.masking import load_patterns
from src.intake.jobspec import LABEL_RX
from src.render.style import TextStyle, base_off as _base_off, block_style

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
