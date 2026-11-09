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


def wrap(text: str, font: pymupdf.Font, size: float, width: float, factor: float = 1.0) -> list[str]:
    def w(s: str) -> float:
        return font.text_length(s, fontsize=size) * factor

    lines: list[str] = []
    cur = ""
    for word in _WS_RE.split(text.strip(_ASCII_WS)):
        if not word:
            continue
        trial = word if not cur else cur + " " + word
        if w(trial) <= width or not cur:
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
