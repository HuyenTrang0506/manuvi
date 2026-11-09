"""US-25a: original page followed by its Vietnamese page."""
from __future__ import annotations

from pathlib import Path

import pymupdf


def interleave(original: str | Path, vi: str | Path, out: str | Path,
               src_pages: list[int] | None = None) -> int:
    """vi page k renders original page src_pages[k] (default: same index). Returns pages written."""
    o, v, new = pymupdf.open(str(original)), pymupdf.open(str(vi)), pymupdf.open()
    sp = src_pages if src_pages is not None else list(range(len(v)))
    for k, p in enumerate(sp):
        new.insert_pdf(o, from_page=p, to_page=p)
        new.insert_pdf(v, from_page=k, to_page=k)
    new.save(str(out), garbage=4, deflate=True)
    n = len(new)
    for d in (o, v, new):
        d.close()
    return n
