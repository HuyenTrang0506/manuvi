"""Fit text into a box by shrinking the font size down to a floor."""
from __future__ import annotations

import re
from dataclasses import dataclass

import pymupdf

ASC = 0.9    # first baseline offset, in units of font size
DESC = 0.25  # descender allowance below last baseline


_ASCII_WS = " \t\n\r\f\v"
_WS_RE = re.compile(r"[ \t\n\r\f\v]+")  # ASCII only: NBSP glues protected tokens


@dataclass
class Fit:
    size: float
    lines: list[str]
    fits: bool
    width: float  # widest line at `size` (including width_factor)
    height: float
    lh: float = 1.15


def wrap(text: str, font: pymupdf.Font, size: float, width: float, factor: float = 1.0, tol: float = 0.0) -> list[str]:
    def w(s: str) -> float:
        return font.text_length(s, fontsize=size) * factor

    lines: list[str] = []
    cur = ""
    for word in _WS_RE.split(text.strip(_ASCII_WS)):
        if not word:
            continue
        trial = word if not cur else cur + " " + word
        if w(trial) <= width + tol or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [""]


def need_height(n_lines: int, size: float, lh: float) -> float:
    return size * (ASC + DESC + (n_lines - 1) * lh)


def fit_text(text: str, font: pymupdf.Font, box_w: float, box_h: float, max_size: float,
             floor: float = 5.0, step: float = 0.25, lh: float = 1.15,
             allow_wrap: bool = True, width_factor: float = 1.0) -> Fit:
    """Largest size in [floor, max_size] whose layout fits; else report floor with fits=False."""
    size = max(max_size, floor)
    while True:
        if allow_wrap:
            lines = wrap(text, font, size, box_w, width_factor)
        else:
            lines = [text]
        widest = max(font.text_length(s, fontsize=size) * width_factor for s in lines)
        h = need_height(len(lines), size, lh)
        if widest <= box_w + 1e-6 and h <= box_h + 1e-6:
            return Fit(size, lines, True, widest, h, lh)
        if size - step < floor - 1e-9:
            return Fit(floor, lines, False, widest, h, lh)
        size = max(size - step, floor)


@dataclass
class CascadeFit:
    fit: Fit
    stage: str        # ok | tight_lh | extended | hard_floor | overflow
    extension: float  # extra height used below the box (pt)
    overflow: bool


def fit_cascade(text: str, font: pymupdf.Font, box_w: float, box_h: float, max_size: float,
                soft_floor: float = 7.0, hard_floor: float = 5.0, free_below: float = 0.0,
                lh: float = 1.15, tight_lh: float = 1.0, step: float = 0.25,
                allow_wrap: bool = True, width_factor: float = 1.0) -> CascadeFit:
    """Shrink to soft floor -> tighter line height -> extend into free space -> hard floor -> overflow.

    Never truncates: on overflow the full text is laid out at the hard floor and flagged.
    """
    soft = min(soft_floor, max_size)
    hard = min(hard_floor, soft)
    kw = dict(step=step, allow_wrap=allow_wrap, width_factor=width_factor)
    f = fit_text(text, font, box_w, box_h, max_size, soft, lh=lh, **kw)
    if f.fits:
        return CascadeFit(f, "ok", 0.0, False)
    f = fit_text(text, font, box_w, box_h, max_size, soft, lh=tight_lh, **kw)
    if f.fits:
        return CascadeFit(f, "tight_lh", 0.0, False)
    ext_h = box_h + max(free_below, 0.0)
    if free_below > 0:
        f = fit_text(text, font, box_w, ext_h, max_size, soft, lh=tight_lh, **kw)
        if f.fits:
            return CascadeFit(f, "extended", max(0.0, f.height - box_h), False)
    f = fit_text(text, font, box_w, ext_h, max_size, hard, lh=tight_lh, **kw)
    if f.fits:
        return CascadeFit(f, "hard_floor", max(0.0, f.height - box_h), False)
    return CascadeFit(f, "overflow", 0.0, True)  # nothing extended; text drawn in full, flagged


# ---- faithful fit: keep the original size where possible, never go below floor_ratio of it ------------------------
DESC_F = 0.212   # Arial / Liberation Sans descender, in font-size units
FLOOR_RATIO = 0.88
TIGHT_PITCH = 0.92


@dataclass
class Faithful:
    size: float
    lines: list[str]
    stage: str            # ok | widened | shrunk | tight | condensed | extended | needs_review
    width: float          # box width used (>= original width when widened)
    start_ext: float      # widening towards the start of the line (pt)
    end_ext: float        # widening towards the end of the line (pt)
    lh: float             # line pitch in font-size units
    base: float           # first baseline distance from the box edge, in font-size units
    extension: float      # extra height used below the box (pt)
    overflow: bool
    height: float
    desc: float = DESC_F
    xscale: float = 1.0   # horizontal scale of the glyphs (condensed stage)


def _sizes(size0: float, floor_ratio: float, step: float) -> list[float]:
    floor = round(size0 * floor_ratio, 3)
    out, s = [], size0
    while s > floor + 1e-6:
        out.append(round(s, 3))
        s -= step
    out.append(floor)
    return out


XSCALES = (0.92, 0.88, 0.84, 0.80)   # condensed stage: horizontal glyph scale, mild first
SUB_FLOOR = 0.6                      # last resort (needs_review) may go down to this share of the original size


def fit_faithful(text: str, font: pymupdf.Font, box_w: float, box_h: float, size0: float, *, base_off: float = 0.0,
                 pitch: float = 0.0, free_start: float = 0.0, free_end: float = 0.0, align: str = "left",
                 free_below: float = 0.0, floor_ratio: float = FLOOR_RATIO, step: float = 0.25,
                 tight: float = TIGHT_PITCH, lines0: int = 1, condense: bool = True) -> Faithful:
    """Keep the original size; when the text is longer, in this order:
    widen the box into free space (original size) -> shrink in small steps to floor_ratio (88 %) -> shrink and widen ->
    tighten the line pitch (x`tight`) -> condense the glyphs horizontally (down to 80 %) -> extend downward into free
    space -> needs_review: smaller than the floor (never below 60 %) so that nothing overlaps; the full text is always
    drawn, overflow is reported only when even that does not fit."""
    base = (base_off / size0) if base_off > 0 else ASC
    base = min(max(base, 0.6), 1.2)
    lh0 = min(max(pitch / size0, 1.0), 1.6) if pitch > 0 else 1.15
    lh_t = lh0 * tight
    desc = DESC_F
    if base_off > 0 and box_h > 0:  # the original box tells how much room below the last baseline its font reserved
        desc = min(max((box_h - base_off - (max(lines0, 1) - 1) * lh0 * size0) / size0, 0.1), 0.4)
    sizes = _sizes(size0, floor_ratio, step)
    room = {"center": 2 * min(free_start, free_end), "right": free_start}.get(align, free_end)
    room = max(room, 0.0)

    def split(extra: float) -> tuple[float, float]:
        if align == "center":
            return extra / 2, extra / 2
        return (extra, 0.0) if align == "right" else (0.0, extra)

    def attempt(s: float, w: float, lh: float, h: float, xs: float = 1.0):
        lines = wrap(text, font, s, w, xs, 0.4)
        widest = max(font.text_length(x, fontsize=s) * xs for x in lines)
        need = s * (base + (len(lines) - 1) * lh + desc)
        return widest <= w + 0.4 and need <= h + 0.3, lines, need

    def done(s, lines, need, w, lh, stage, ext=0.0, over=False, xs=1.0):
        a, b = split(max(w - box_w, 0.0))
        return Faithful(s, lines, stage, w, a, b, lh, base, ext, over, need, desc, xs)

    widths = [box_w + room * k / 8 for k in range(1, 9)] if room > 0.5 else []
    all_w = [box_w] + widths
    wmax = all_w[-1]
    for w in all_w:                                   # original size, then widen
        ok, lines, need = attempt(sizes[0], w, lh0, box_h)
        if ok:
            return done(sizes[0], lines, need, w, lh0, "ok" if w == box_w else "widened")
    for s in sizes[1:]:                               # shrink, box as it was
        ok, lines, need = attempt(s, box_w, lh0, box_h)
        if ok:
            return done(s, lines, need, box_w, lh0, "shrunk")
    for s in sizes[1:]:                               # shrink and widen
        for w in widths:
            ok, lines, need = attempt(s, w, lh0, box_h)
            if ok:
                return done(s, lines, need, w, lh0, "widened")
    for s in sizes:                                   # tighter line pitch
        for w in all_w:
            ok, lines, need = attempt(s, w, lh_t, box_h)
            if ok:
                return done(s, lines, need, w, lh_t, "tight")
    if condense:
        for s in sizes:
            for xs in XSCALES:
                for w in all_w:
                    ok, lines, need = attempt(s, w, lh_t, box_h, xs)
                    if ok:
                        return done(s, lines, need, w, lh_t, "condensed", xs=xs)
    if free_below > 0.5:
        for s in sizes:
            ok, lines, need = attempt(s, wmax, lh_t, box_h + free_below)
            if ok:
                return done(s, lines, need, wmax, lh_t, "extended", ext=max(0.0, need - box_h))
    low = max(5.0, size0 * SUB_FLOOR)
    s = sizes[-1] - step
    xs = XSCALES[-1] if condense else 1.0
    while s >= low - 1e-9:
        ok, lines, need = attempt(s, wmax, lh_t, box_h + max(free_below, 0.0), xs)
        if ok:
            return done(s, lines, need, wmax, lh_t, "needs_review", ext=max(0.0, need - box_h), xs=xs)
        s -= step
    _, lines, need = attempt(max(low, 1.0), wmax, lh_t, box_h, xs)
    return done(max(low, 1.0), lines, need, wmax, lh_t, "needs_review", over=True, xs=xs)
