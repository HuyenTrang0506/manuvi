"""Render synthetic-VI pages of a real manual and print AGGREGATE numbers only (no manual text).

usage: python scripts/render_real_pages.py manual.pdf --pages 187-190,661-664 --out DIR
Pages are 1-based. Outputs in DIR: vi.pdf, interleaved.pdf, qa.json (no source/target text,
no token strings). stdout carries counts only.
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.render.fonts import find_vi_font  # noqa: E402
from src.render.interleave import interleave  # noqa: E402
from src.render.page import Segment, render_document  # noqa: E402
from src.render.qa_report import build_report, to_json  # noqa: E402
from src.render.synth import synth_vi  # noqa: E402


def parse_pages(spec: str) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        a, _, b = part.strip().partition("-")
        out += list(range(int(a), int(b or a) + 1))
    return [p - 1 for p in out]


def page_segments(page: pymupdf.Page) -> list[Segment]:
    segs: list[Segment] = []
    cells: list[pymupdf.Rect] = []
    try:
        for tab in page.find_tables().tables:
            for row in tab.rows:
                for c in row.cells:
                    if c is None:
                        continue
                    r = pymupdf.Rect(c)
                    txt = " ".join(w[4] for w in page.get_text("words", clip=r))
                    if txt.strip():
                        cells.append(r)
                        segs.append(Segment(page.number, tuple(r), "table", txt, synth_vi(txt), max_size=8.0))
    except Exception:
        pass
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        r = pymupdf.Rect(b["bbox"])
        if any(r in c or (r & c).get_area() > 0.5 * r.get_area() for c in cells):
            continue
        lines = b["lines"]
        txt = " ".join("".join(s["text"] for s in ln["spans"]) for ln in lines).strip()
        if not txt:
            continue
        d = lines[0]["dir"]
        rot = 0 if abs(d[0]) >= abs(d[1]) and d[0] >= 0 else 180 if abs(d[0]) >= abs(d[1]) else (90 if d[1] < 0 else 270)
        size = max(s["size"] for ln in lines for s in ln["spans"])
        kind = "prose" if len(txt) > 40 and len(lines) > 1 else "label"
        segs.append(Segment(page.number, tuple(r), kind, txt, synth_vi(txt), rotate=rot, max_size=min(size, 11.0)))
    return segs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--pages", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    font = find_vi_font()
    if font is None:
        print("no Vietnamese font found; set MANUVI_VI_FONT", file=sys.stderr)
        return 2
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    pages = parse_pages(a.pages)
    t0 = time.time()
    src = pymupdf.open(a.pdf)
    segs = [s for p in pages for s in page_segments(src[p])]
    src.close()
    results = render_document(a.pdf, segs, out / "vi.pdf", fontfile=str(font), pages=pages)
    rep = build_report(out / "vi.pdf", segs, results, include_tokens=False)
    (out / "qa.json").write_text(to_json(rep), encoding="utf-8")
    n = interleave(a.pdf, out / "vi.pdf", out / "interleaved.pdf", pages)
    kinds = Counter(s.kind for s in segs)
    stages = Counter(r.stage for pr in results for r in pr.segs)
    sizes = [r.size for pr in results for r in pr.segs]
    print(f"pages={len(pages)} interleaved_pages={n} segments={len(segs)} kinds={dict(kinds)}")
    print(f"qa={rep['summary']}")
    print(f"stages={dict(stages)}")
    print(f"legend_pages={sum(pr.legend for pr in results)} grown_pages={sum(pr.grown > 0 for pr in results)}")
    if sizes:
        print(f"font_size min={min(sizes):.2f} median={statistics.median(sizes):.2f} max={max(sizes):.2f}")
    print(f"seconds={time.time() - t0:.1f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
