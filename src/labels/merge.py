"""Extract label spans from a PDF page and merge adjacent fragments."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import pymupdf

ALLOW_SHORT = {"ON", "OFF", "NO", "YES", "IN", "OUT", "UP", "OK", "R", "L", "H", "A"}


@dataclass
class Label:
    text: str
    bbox: tuple[float, float, float, float]
    size: float
    direction: tuple[float, float] = (1.0, 0.0)
    parts: int = 1


def extract_spans(page: pymupdf.Page) -> list[Label]:
    out: list[Label] = []
    for bl in page.get_text("dict")["blocks"]:
        if bl["type"] != 0:
            continue
        for ln in bl["lines"]:
            for sp in ln["spans"]:
                t = sp["text"].strip()
                if t:
                    out.append(Label(t, tuple(sp["bbox"]), sp["size"], tuple(ln["dir"])))
    return out


def _axis(d: tuple[float, float]) -> int:
    dx, dy = d
    if abs(dx) >= abs(dy):
        return 0 if dx >= 0 else 2
    return 1 if dy < 0 else 3  # 1: reads upward, 3: reads downward


def reading_box(b, axis: int):
    """Map a page bbox to a frame where text reads left-to-right, top-to-bottom: (u0, v0, u1, v1)."""
    x0, y0, x1, y1 = b
    if axis == 0:
        return (x0, y0, x1, y1)
    if axis == 1:
        return (-y1, x0, -y0, x1)
    if axis == 2:
        return (-x1, -y1, -x0, -y0)
    return (y0, -x1, y1, -x0)


def is_suspicious(text: str) -> bool:
    t = text.strip()
    if not t or t in ALLOW_SHORT:
        return False
    if len(t) == 1 and t.isalpha():
        return True
    if t[0].islower() and (" " not in t and len(t) <= 6):  # short lower-case continuation fragment
        return True
    if len(t) > 1 and (t.endswith("-") or t.startswith("-")):
        return True
    if len(t) <= 3 and not re.fullmatch(r"[\d\-.,/:%+()]+", t) and not t.isupper():
        return True                          # short mixed-case remnant, e.g. "Pr"
    if re.fullmatch(r"[A-Z]", t):
        return True
    return False


def count_suspicious(labels: list[Label]) -> int:
    return sum(is_suspicious(l.text) for l in labels)


def merge_adjacent(labels: list[Label], gap_factor: float = 0.6, line_gap_factor: float = 0.5,
                   size_tol: float = 0.2) -> list[Label]:
    """Merge same-line neighbours (gap <= gap_factor*size) and lower-case continuation lines."""
    n = len(labels)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    ax = [_axis(l.direction) for l in labels]
    rb = [reading_box(l.bbox, a) for l, a in zip(labels, ax)]
    for i in range(n):
        for j in range(n):
            if i == j or ax[i] != ax[j]:
                continue
            a, b = labels[i], labels[j]
            if abs(a.size - b.size) > size_tol * max(a.size, b.size):
                continue
            ai, bj = rb[i], rb[j]
            h = min(ai[3] - ai[1], bj[3] - bj[1]) or 1e-6
            vov = min(ai[3], bj[3]) - max(ai[1], bj[1])
            gap = bj[0] - ai[2]  # j to the right of i
            if vov >= 0.5 * h and -0.3 * a.size <= gap <= gap_factor * a.size:
                parent[find(j)] = find(i)
                continue
            # continuation: j directly below i, left aligned, starts lower-case
            vgap = bj[1] - ai[3]
            if (b.text[0].islower() and -0.2 * a.size <= vgap <= line_gap_factor * a.size
                    and abs(bj[0] - ai[0]) <= 1.0 * a.size):
                parent[find(j)] = find(i)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    out: list[Label] = []
    for idx in groups.values():
        ordered = sorted(idx, key=lambda k: (round(rb[k][1] / max(labels[k].size, 1)), rb[k][0]))
        text = ""
        for k in ordered:
            t = labels[k].text
            text = t if not text else (text[:-1] + t if text.endswith("-") and t[:1].islower()
                                       else text + " " + t)
        xs0 = min(labels[k].bbox[0] for k in idx); ys0 = min(labels[k].bbox[1] for k in idx)
        xs1 = max(labels[k].bbox[2] for k in idx); ys1 = max(labels[k].bbox[3] for k in idx)
        first = labels[ordered[0]]
        out.append(Label(text, (xs0, ys0, xs1, ys1), first.size, first.direction, len(idx)))
    return out
