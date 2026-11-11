"""Decide per page whether (and where) the vision model must read, from text-layer coverage.

Signals: characters per page area, share of the page covered by images, glyph-less spans (unmapped glyphs,
U+FFFD, private-use code points) and images that contain no text-layer text. All geometry is in pdf space
(see vision_read). Pure heuristics, no model call.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pymupdf

from .vision_read import TextBlock, VisionConfig, pdf_bounds

DENSE_DRAWINGS = 300


@dataclass
class PageDecision:
    mode: str = "none"                       # none | page | regions
    regions: list[pymupdf.Rect] = field(default_factory=list)   # pdf space, for mode == regions
    reasons: list[str] = field(default_factory=list)
    signals: dict = field(default_factory=dict)
    dense: bool = False
    truncated: bool = False


def _glyphless(ch: dict) -> bool:
    c = ch.get("c", "")
    return c in ("", "�", "\x00") or 0xE000 <= ord(c[0]) <= 0xF8FF


def text_layer(page: pymupdf.Page) -> tuple[list[TextBlock], int, int]:
    """(usable text blocks, usable chars, glyph-less chars). Blocks made only of glyph-less spans are not usable."""
    blocks: list[TextBlock] = []
    good = bad = 0
    for b in page.get_text("rawdict")["blocks"]:
        if b.get("type") != 0:
            continue
        txt, g, x = [], 0, 0
        for ln in b["lines"]:
            for sp in ln["spans"]:
                for ch in sp["chars"]:
                    if _glyphless(ch):
                        g += 1
                    else:
                        x += 1
                        txt.append(ch["c"])
                txt.append(" ")
        bad += g
        good += sum(not c.isspace() for c in txt)
        t = " ".join("".join(txt).split())
        if t and x >= g:
            blocks.append(TextBlock(t, tuple(float(v) for v in b["bbox"])))
    return blocks, good, bad


def _union(rects: list[pymupdf.Rect], gap: float = 6.0) -> list[pymupdf.Rect]:
    rects = [pymupdf.Rect(r) for r in rects]
    changed = True
    while changed:
        changed, out = False, []
        for r in rects:
            for i, o in enumerate(out):
                if not (pymupdf.Rect(r.x0 - gap, r.y0 - gap, r.x1 + gap, r.y1 + gap) & o).is_empty:
                    out[i] = o | r
                    changed = True
                    break
            else:
                out.append(r)
        rects = out
    return rects


def analyze_page(page: pymupdf.Page, cfg: VisionConfig) -> tuple[PageDecision, list[TextBlock]]:
    """Return the decision and the usable text-layer blocks (input of the merge)."""
    bounds = pdf_bounds(page)
    area = max(bounds.get_area(), 1.0)
    blocks, chars, bad = text_layer(page)
    words = page.get_text("words")
    try:
        infos = page.get_image_info()
    except Exception:
        infos = []
    imgs = []
    for i in infos:
        r = pymupdf.Rect(i["bbox"]) & bounds
        if r.is_empty or r.get_area() / area < cfg.min_image_area_frac:
            continue
        if min(i.get("width", 0), i.get("height", 0)) < cfg.min_image_px:
            continue
        imgs.append(r)
    covered = min(1.0, sum(r.get_area() for r in imgs) / area)
    try:
        drawings = len(page.get_drawings())
    except Exception:
        drawings = 0
    d = PageDecision(signals={"chars": chars, "glyphless_chars": bad, "images": len(imgs),
                              "image_cover": round(covered, 3), "chars_per_10k_pt2": round(chars / area * 1e4, 2),
                              "drawings": drawings},
                     dense=drawings >= DENSE_DRAWINGS)
    glyphless_share = bad / (bad + chars) if bad + chars else 0.0
    if chars < cfg.min_page_chars:
        d.mode, d.reasons = "page", ["no_text_layer"]
        return d, blocks
    if glyphless_share >= 0.3:
        d.mode, d.reasons = "page", ["glyphless_text_layer"]
        return d, blocks
    sparse = chars / area * 1e4 < cfg.sparse_chars_per_10k_pt2
    if covered >= 0.6 and sparse:
        d.mode, d.reasons = "page", ["image_page_sparse_text"]
        return d, blocks
    # image areas without any text-layer text, on a page that has text elsewhere
    empty = []
    for r in imgs:
        inside = sum(len(w[4]) for w in words if r.contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)))
        if inside < 3:
            empty.append(r)
    if bad:
        d.reasons.append("some_glyphless_spans")
    if empty:
        regions = [pymupdf.Rect(r.x0 - 4, r.y0 - 4, r.x1 + 4, r.y1 + 4) for r in _union(empty)]
        regions = [r & bounds for r in regions]
        regions.sort(key=lambda r: -r.get_area())
        if len(regions) > cfg.max_regions_per_page:
            regions, d.truncated = regions[:cfg.max_regions_per_page], True
        d.mode, d.regions = "regions", regions
        d.reasons.append("image_regions_without_text")
    return d, blocks
