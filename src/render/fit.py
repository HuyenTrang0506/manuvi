"""Fit text into a box by shrinking the font size down to a floor."""
from __future__ import annotations

from dataclasses import dataclass

import pymupdf

ASC = 0.9    # first baseline offset, in units of font size
DESC = 0.25  # descender allowance below last baseline


@dataclass
class Fit:
    size: float
    lines: list[str]
    fits: bool
    width: float  # widest line at `size` (including width_factor)
    height: float


def wrap(text: str, font: pymupdf.Font, size: float, width: float, factor: float = 1.0) -> list[str]:
    def w(s: str) -> float:
        return font.text_length(s, fontsize=size) * factor

    lines: list[str] = []
    cur = ""
    for word in text.split():
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
            return Fit(size, lines, True, widest, h)
        if size - step < floor - 1e-9:
            return Fit(floor, lines, False, widest, h)
        size = max(size - step, floor)
