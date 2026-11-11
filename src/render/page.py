"""Render Vietnamese text onto source pages (vector): prose, table cells, diagram labels."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..guard.masking import load_patterns
from ..guard.numbers import diff_numbers
from .fit import CascadeFit, fit_cascade, fit_faithful, fit_text, wrap
from .fonts import FontSet, find_vi_font
from .overlay import FONTNAME, box_extent, draw_faithful, draw_fit, mask_rects, mask_spans, placed_rect, register_font
from .style import TextStyle

NBSP = "\xa0"
FIG_RE = re.compile(r"Fig\.?\s*([0-9]+(?:-[0-9]+)*)")
KINDS = ("prose", "table", "label")
_PATTERNS = None


def patterns():
    global _PATTERNS
    if _PATTERNS is None:
        _PATTERNS = load_patterns()
    return _PATTERNS


@dataclass
class Segment:
    page: int  # 0-based page index in the source document
    bbox: tuple[float, float, float, float]
    kind: str  # prose | table | label
    source: str
    target: str
    rotate: int = 0
    max_size: float = 10.0
    style: TextStyle | None = None  # original style: faithful rendering when set, legacy cascade when None


@dataclass
class SegResult:
    index: int
    kind: str
    stage: str  # cascade stage, "cell*" for tables, "overlay" | "legend" for labels
    size: float
    extension: float = 0.0
    overflow: bool = False
    legend_n: int | None = None
    floor: bool = False  # shrunk to the soft floor or below
    numbers_missing: list[str] = field(default_factory=list)
    numbers_extra: list[str] = field(default_factory=list)
    size0: float = 0.0                 # original size (faithful path only)
    lines0: int = 0
    lines: int = 0
    drift: float = 0.0                 # distance of the text anchor from its original position (pt)
    placed: tuple | None = None        # page-space rectangle the text occupies
    font_from: str = ""
    font_to: str = ""
    needs_review: bool = False
    shrunk90: bool = False             # drawn below 90 % of the original size

    @property
    def ratio(self) -> float:
        return round(self.size / self.size0, 4) if self.size0 else 1.0


@dataclass
class PageResult:
    page: int
    segs: list[SegResult] = field(default_factory=list)
    legend: bool = False
    grown: float = 0.0
    fig: str | None = None


def glue_protected(text: str) -> str:
    """Replace whitespace inside protected tokens with NBSP so they never wrap apart."""
    spans = []
    for _, rx in patterns():
        spans += [m.span() for m in rx.finditer(text)]
    if not spans:
        return text
    chars = list(text)
    for a, b in spans:
        for i in range(a, b):
            if chars[i] in " \t\n\r":
                chars[i] = NBSP
    return "".join(chars)


def _hwords(page: pymupdf.Page) -> list[pymupdf.Rect]:
    rs = [pymupdf.Rect(w[:4]) for w in page.get_text("words")]
    rs += [d["rect"] for d in page.get_drawings() if d.get("rect") is not None]
    rs += [pymupdf.Rect(i["bbox"]) for i in page.get_image_info()]
    return rs


def _free_below(page, bbox: pymupdf.Rect, obstacles: list[pymupdf.Rect], margin: float = 18.0) -> float:
    limit = page.rect.y1 - margin
    for o in obstacles:
        if o.x0 < bbox.x1 and o.x1 > bbox.x0 and o.y0 >= bbox.y1 - 0.5:
            limit = min(limit, o.y0)
    return max(0.0, limit - 2.0 - bbox.y1)


def _page_obstacles(page, rects: list[pymupdf.Rect]) -> list[pymupdf.Rect]:
    """Static page content (not inside any segment box) that blocks extension."""
    out = []
    for r in _hwords(page):
        c = (r.tl + r.br) / 2
        if r.is_empty and r.width == 0 and r.height == 0:
            continue
        if any(c in sr for sr in rects):
            continue
        out.append(r)
    return out


def figure_ref(segs: list[Segment]) -> str | None:
    for s in segs:
        m = FIG_RE.search(s.source) or FIG_RE.search(s.target)
        if m:
            return f"Fig.{NBSP}{m.group(1)}"
    return None


def _free_dir(page, rect: pymupdf.Rect, obstacles: list[pymupdf.Rect], d: str, margin: float = 6.0,
              gap: float = 1.5) -> float:
    """Free distance from `rect` to the nearest obstacle (or page margin) in direction l, r, u or d."""
    if d in ("l", "r"):
        lim = page.rect.x1 - margin - rect.x1 if d == "r" else rect.x0 - (page.rect.x0 + margin)
        for o in obstacles:
            if o.y0 < rect.y1 - 0.3 and o.y1 > rect.y0 + 0.3:
                if d == "r" and o.x0 >= rect.x1 - 0.5:
                    lim = min(lim, o.x0 - gap - rect.x1)
                elif d == "l" and o.x1 <= rect.x0 + 0.5:
                    lim = min(lim, rect.x0 - gap - o.x1)
    else:
        lim = page.rect.y1 - margin - rect.y1 if d == "d" else rect.y0 - (page.rect.y0 + margin)
        for o in obstacles:
            if o.x0 < rect.x1 - 0.3 and o.x1 > rect.x0 + 0.3:
                if d == "d" and o.y0 >= rect.y1 - 0.5:
                    lim = min(lim, o.y0 - gap - rect.y1)
                elif d == "u" and o.y1 <= rect.y0 + 0.5:
                    lim = min(lim, rect.y0 - gap - o.y1)
    return max(0.0, lim)


_START_END = {0: ("l", "r"), 180: ("r", "l"), 90: ("d", "u"), 270: ("u", "d")}


def _frame(r: pymupdf.Rect, rot: int) -> tuple[float, float, float, float]:
    """(along0, along1, across0, across1) of a page rect in the reading frame."""
    if rot == 0:
        return r.x0, r.x1, r.y0, r.y1
    if rot == 180:
        return -r.x1, -r.x0, -r.y1, -r.y0
    if rot == 90:
        return -r.y1, -r.y0, r.x0, r.x1
    return r.y0, r.y1, -r.x1, -r.x0


def anchor_drift(orig: pymupdf.Rect, placed: pymupdf.Rect, rot: int, align: str) -> float:
    a0, a1, t0, _ = _frame(orig, rot)
    b0, b1, u0, _ = _frame(placed, rot)
    da = {"left": b0 - a0, "center": (b0 + b1) / 2 - (a0 + a1) / 2, "right": b1 - a1}[align]
    return round((da * da + (u0 - t0) ** 2) ** 0.5, 2)


def render_page(page: pymupdf.Page, segs: list[Segment], fontfile: "str | FontSet", soft_floor: float = 7.0,
                hard_floor: float = 5.0, index_offset: int = 0) -> PageResult:
    """Render segments in place. Returns per-segment results and legend info.

    Segments with `style` are redrawn faithfully (original font family, size, colour, alignment, rotation; shrink
    floor 88 %). Segments without it use the older fixed-floor cascade.
    """
    fs = fontfile if isinstance(fontfile, FontSet) else FontSet(str(fontfile))
    res = PageResult(page=page.number, fig=figure_ref(segs))
    if not segs:
        return res
    rects = [pymupdf.Rect(s.bbox) for s in segs]
    obstacles = _page_obstacles(page, rects)
    legacy = [r for s, r in zip(segs, rects) if s.style is None]
    if legacy:
        mask_rects(page, legacy)
    precise = [pymupdf.Rect(sp) for s in segs if s.style is not None for sp in (s.style.spans or [s.bbox])]
    if precise:
        mask_spans(page, precise)
    base_face = fs.face()
    font = fs.font(base_face)
    if any(x.style is None for x in segs):
        register_font(page, base_face.path)
    seen: set[str] = set()
    taken: list[pymupdf.Rect] = []
    pending_legend: list[tuple[int, str]] = []
    n_legend = 0
    content_bottom = max([r.y1 for r in rects] + [o.y1 for o in obstacles] + [0.0])

    for i, (s, r) in enumerate(zip(segs, rects)):
        idx = index_offset + i
        text = glue_protected(s.target)
        L, T = box_extent(r, s.rotate)
        if s.style is not None:
            st = s.style
            face = fs.face(st.font, (16 if st.bold else 0) | (2 if st.italic else 0), text)
            fnt = fs.font(face)
            others = obstacles + [x for j, x in enumerate(rects) if j != i] + taken
            sd, ed = _START_END[s.rotate]
            fstart, fend = _free_dir(page, r, others, sd), _free_dir(page, r, others, ed)
            below = _free_dir(page, r, others, "d") if s.rotate == 0 else 0.0
            ft = fit_faithful(text, fnt, L, T, st.size, base_off=st.base_off, pitch=st.pitch, free_start=fstart,
                              free_end=fend, align=st.align, free_below=below, lines0=st.lines)
            sr = SegResult(idx, s.kind, ft.stage, ft.size, ft.extension, ft.overflow, size0=st.size,
                           lines0=st.lines, lines=len(ft.lines), font_from=st.font,
                           font_to=Path(face.path).stem, needs_review=ft.stage == "needs_review")
            if s.kind == "label" and ft.stage == "needs_review":
                n_legend += 1
                pending_legend.append((n_legend, text))
                mk = fit_faithful(f"[{n_legend}]", fnt, L, T, st.size, base_off=st.base_off, pitch=st.pitch,
                                  free_start=fstart, free_end=fend, align=st.align)
                placed = draw_faithful(page, r, s.rotate, mk, face, fnt, st.align, st.color, seen)
                sr.stage, sr.size, sr.overflow, sr.legend_n = "legend", mk.size, mk.stage == "needs_review", n_legend
                sr.needs_review = False
                sr.lines = 1
            else:
                placed = draw_faithful(page, r, s.rotate, ft, face, fnt, st.align, st.color, seen)
                if ft.extension > 0:
                    taken.append(pymupdf.Rect(r.x0, r.y1, r.x1, r.y1 + ft.extension))
                    content_bottom = max(content_bottom, r.y1 + ft.extension)
            taken.append(placed)
            sr.placed = tuple(round(v, 2) for v in placed)
            sr.drift = anchor_drift(r, placed, s.rotate, st.align)
            sr.shrunk90 = sr.size < 0.9 * st.size - 1e-6
            sr.floor = ft.size <= st.size * 0.88 + 1e-3 and ft.stage != "ok"
            if s.kind == "table":
                miss, extra = diff_numbers(s.source, s.target, patterns())
                sr.numbers_missing, sr.numbers_extra = miss, extra
            res.segs.append(sr)
            continue
        if s.kind == "label":
            fit = fit_text(text, font, L, T, min(s.max_size, 8.0), soft_floor)
            if fit.fits:
                draw_fit(page, r, s.rotate, fit)
                res.segs.append(SegResult(idx, s.kind, "overlay", fit.size,
                                          placed=tuple(placed_rect(r, s.rotate, fit)), lines=len(fit.lines)))
                continue
            n_legend += 1
            pending_legend.append((n_legend, text))
            mk = fit_text(f"[{n_legend}]", font, L, T, min(s.max_size, 8.0), hard_floor, allow_wrap=False)
            draw_fit(page, r, s.rotate, mk)
            res.segs.append(SegResult(idx, s.kind, "legend", mk.size, overflow=not mk.fits, legend_n=n_legend,
                                      placed=tuple(placed_rect(r, s.rotate, mk)), lines=len(mk.lines)))
            continue
        if s.kind == "table":
            c = fit_cascade(text, font, L, T, s.max_size, soft_floor, hard_floor, 0.0)
            miss, extra = diff_numbers(s.source, s.target, patterns())
            stage = "cell" if c.stage == "ok" else "cell_" + c.stage
            draw_fit(page, r, s.rotate, c.fit)
            res.segs.append(SegResult(idx, s.kind, stage, c.fit.size, 0.0, c.overflow,
                                      numbers_missing=miss, numbers_extra=extra,
                                      placed=tuple(placed_rect(r, s.rotate, c.fit)), lines=len(c.fit.lines)))
            continue
        free = 0.0
        if s.rotate == 0:
            free = _free_below(page, r, obstacles + [x for j, x in enumerate(rects) if j != i] + taken)
        c = fit_cascade(text, font, L, T, s.max_size, soft_floor, hard_floor, free)
        draw_fit(page, r, s.rotate, c.fit)
        if c.extension > 0:
            taken.append(pymupdf.Rect(r.x0, r.y1, r.x1, r.y1 + c.extension))
            content_bottom = max(content_bottom, r.y1 + c.extension)
        res.segs.append(SegResult(idx, s.kind, c.stage, c.fit.size, c.extension, c.overflow,
                                  placed=tuple(placed_rect(r, s.rotate, c.fit)), lines=len(c.fit.lines)))

    if pending_legend:
        if all(x.style is not None for x in segs):
            register_font(page, base_face.path)
        _draw_legend(page, font, pending_legend, res, content_bottom, soft_floor)
    for r_, s_ in zip(res.segs, segs):
        if s_.style is None:
            r_.floor = r_.size <= soft_floor + 1e-6 and s_.max_size > soft_floor and r_.stage != "overlay"
    return res


def _draw_legend(page, font, entries, res: PageResult, content_bottom: float, soft_floor: float) -> None:
    margin, size, lh = 24.0, 7.5, 1.2
    width = page.rect.width - 2 * margin
    title = "Chú giải " + res.fig if res.fig else "Chú giải hình"
    blocks = [(title, True)] + [(f"[{n}] {t}", False) for n, t in entries]
    laid = [(wrap(t, font, size, width), bold) for t, bold in blocks]
    n_lines = sum(len(l) for l, _ in laid)
    need = n_lines * size * lh + 8.0 + size
    y = content_bottom + 10.0
    if y + need > page.rect.y1 - 6.0:
        grow = y + need + 6.0 - page.rect.y1
        mb = page.mediabox
        page.set_mediabox(pymupdf.Rect(mb.x0, mb.y0 - grow, mb.x1, mb.y1))
        # PyMuPDF already resizes the CropBox with the MediaBox; setting it again from page.rect (a different,
        # top-left coordinate system) raised "CropBox not in MediaBox" and failed the whole PDF.
        res.grown = grow
    res.legend = True
    for lines, bold in laid:
        for ln in lines:
            y += size * lh
            page.insert_text((margin, y), ln, fontsize=size, fontname=FONTNAME)
        if bold:
            y += 2.0


def render_document(src: str | Path, segments: list[Segment], out: str | Path,
                    fontfile: "str | FontSet | None" = None, pages: list[int] | None = None,
                    **kw) -> list[PageResult]:
    """Render all segments into a copy of `src`; keep only `pages` (0-based) if given."""
    ff = fontfile if isinstance(fontfile, FontSet) else FontSet(str(fontfile) if fontfile else None)
    ff.face()  # fails early when no Vietnamese font exists
    doc = pymupdf.open(str(src))
    by_page: dict[int, list[Segment]] = {}
    for s in segments:
        by_page.setdefault(s.page, []).append(s)
    results = []
    for pno in (pages if pages is not None else range(len(doc))):
        results.append(render_page(doc[pno], by_page.get(pno, []), ff, **kw))
    if pages is not None:
        doc.select(pages)
    doc.subset_fonts()
    doc.save(str(out), garbage=4, deflate=True)
    doc.close()
    return results
