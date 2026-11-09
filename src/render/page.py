"""Render Vietnamese text onto source pages (vector): prose, table cells, diagram labels."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

from ..guard.masking import load_patterns
from ..guard.numbers import diff_numbers
from .fit import CascadeFit, fit_cascade, fit_text, wrap
from .fonts import find_vi_font
from .overlay import FONTNAME, box_extent, draw_fit, mask_rects, register_font

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


def render_page(page: pymupdf.Page, segs: list[Segment], fontfile: str, soft_floor: float = 7.0,
                hard_floor: float = 5.0, index_offset: int = 0) -> PageResult:
    """Render segments in place. Returns per-segment results and legend info."""
    res = PageResult(page=page.number, fig=figure_ref(segs))
    if not segs:
        return res
    rects = [pymupdf.Rect(s.bbox) for s in segs]
    obstacles = _page_obstacles(page, rects)
    # candidate extension limits: other segments below
    mask_rects(page, rects)
    font = register_font(page, fontfile)
    taken: list[pymupdf.Rect] = []
    pending_legend: list[tuple[int, str]] = []
    n_legend = 0
    content_bottom = max([r.y1 for r in rects] + [o.y1 for o in obstacles] + [0.0])

    for i, (s, r) in enumerate(zip(segs, rects)):
        idx = index_offset + i
        text = glue_protected(s.target)
        L, T = box_extent(r, s.rotate)
        if s.kind == "label":
            fit = fit_text(text, font, L, T, min(s.max_size, 8.0), soft_floor)
            if fit.fits:
                draw_fit(page, r, s.rotate, fit)
                res.segs.append(SegResult(idx, s.kind, "overlay", fit.size))
                continue
            n_legend += 1
            pending_legend.append((n_legend, text))
            mk = fit_text(f"[{n_legend}]", font, L, T, min(s.max_size, 8.0), hard_floor, allow_wrap=False)
            draw_fit(page, r, s.rotate, mk)
            res.segs.append(SegResult(idx, s.kind, "legend", mk.size, overflow=not mk.fits, legend_n=n_legend))
            continue
        if s.kind == "table":
            c = fit_cascade(text, font, L, T, s.max_size, soft_floor, hard_floor, 0.0)
            miss, extra = diff_numbers(s.source, s.target, patterns())
            stage = "cell" if c.stage == "ok" else "cell_" + c.stage
            draw_fit(page, r, s.rotate, c.fit)
            res.segs.append(SegResult(idx, s.kind, stage, c.fit.size, 0.0, c.overflow,
                                      numbers_missing=miss, numbers_extra=extra))
            continue
        free = 0.0
        if s.rotate == 0:
            free = _free_below(page, r, obstacles + [x for j, x in enumerate(rects) if j != i] + taken)
        c = fit_cascade(text, font, L, T, s.max_size, soft_floor, hard_floor, free)
        draw_fit(page, r, s.rotate, c.fit)
        if c.extension > 0:
            taken.append(pymupdf.Rect(r.x0, r.y1, r.x1, r.y1 + c.extension))
            content_bottom = max(content_bottom, r.y1 + c.extension)
        res.segs.append(SegResult(idx, s.kind, c.stage, c.fit.size, c.extension, c.overflow))

    if pending_legend:
        _draw_legend(page, font, pending_legend, res, content_bottom, soft_floor)
    for r_, s_ in zip(res.segs, segs):
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
        page.set_cropbox(page.rect)
        res.grown = grow
    res.legend = True
    for lines, bold in laid:
        for ln in lines:
            y += size * lh
            page.insert_text((margin, y), ln, fontsize=size, fontname=FONTNAME)
        if bold:
            y += 2.0


def render_document(src: str | Path, segments: list[Segment], out: str | Path,
                    fontfile: str | None = None, pages: list[int] | None = None,
                    **kw) -> list[PageResult]:
    """Render all segments into a copy of `src`; keep only `pages` (0-based) if given."""
    ff = fontfile or find_vi_font()
    if ff is None:
        raise RuntimeError("no Vietnamese font found (set MANUVI_VI_FONT)")
    doc = pymupdf.open(str(src))
    by_page: dict[int, list[Segment]] = {}
    for s in segments:
        by_page.setdefault(s.page, []).append(s)
    results = []
    for pno in (pages if pages is not None else range(len(doc))):
        results.append(render_page(doc[pno], by_page.get(pno, []), str(ff), **kw))
    if pages is not None:
        doc.select(pages)
    doc.subset_fonts()
    doc.save(str(out), garbage=4, deflate=True)
    doc.close()
    return results
