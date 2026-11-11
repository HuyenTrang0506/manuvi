"""Final deliverables from the (human-reviewed) segments: VI PDF, interleaved PDF, segments and terms Excel."""
from __future__ import annotations

import json
from pathlib import Path

from openpyxl import Workbook

from src.render.interleave import interleave
import pymupdf

from src.render.page import Segment, render_document
from src.render.style import style_for_box
from src.terms.excel_io import _safe, write_terms_xlsx
from src.terms.store import TermRecord

from .prepare import JOBSPEC_FILE, LAYOUT_FILE, TERMS_FILE

OUT = "out"
COLS = ["id", "page", "section", "kind", "status", "decision", "issues", "source", "target"]


def segments_xlsx(segs: list[dict], path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "segments"
    ws.append(COLS)
    for s in segs:
        row = [s.get("id"), s.get("page"), s.get("section"), s.get("kind"), s.get("status"), s.get("decision"),
               "; ".join(map(str, s.get("issues") or [])), s.get("source"), s.get("target")]
        ws.append([_safe(v) for v in row])
        for c in ws[ws.max_row]:
            if isinstance(c.value, str):
                c.data_type = "s"
    ws.freeze_panes = "A2"
    for col, w in zip("ABCDEFGHI", (8, 6, 9, 9, 13, 10, 36, 60, 60)):
        ws.column_dimensions[col].width = w
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def build_outputs(work: Path, pdf: Path, segs: list[dict]) -> tuple[list[str], str | None]:
    """Write files under work/out. Returns (names relative to work, error_code or None).
    A missing Vietnamese font degrades to Excel-only outputs instead of failing the job."""
    out = work / OUT
    out.mkdir(exist_ok=True)
    names = []
    segments_xlsx(segs, out / "segments.xlsx")
    names.append("out/segments.xlsx")
    tp = work / TERMS_FILE
    terms = json.loads(tp.read_text("utf-8")) if tp.exists() else []
    recs = [TermRecord(term_en=t["en"], term_vi=t.get("vi", ""), protected=bool(t.get("protected")),
                       status="approved" if t.get("decision") == "approved" and t.get("vi") else "proposed")
            for t in terms]
    write_terms_xlsx(recs, out / "terms.xlsx")
    names.append("out/terms.xlsx")
    layout = json.loads((work / LAYOUT_FILE).read_text("utf-8"))
    spec = json.loads((work / JOBSPEC_FILE).read_text("utf-8"))
    pages0 = sorted({layout[s["id"]]["page0"] for s in segs if s["id"] in layout})
    render_segs = []
    src_doc = pymupdf.open(str(pdf))
    for s in segs:
        lay = layout.get(s["id"])
        if not lay or not (s.get("target") or "").strip() or s.get("decision") == "rejected" \
                or s.get("status") == "error":
            continue
        kind = s.get("kind") if s.get("kind") in ("table", "label") else "prose"
        seg = Segment(lay["page0"], tuple(lay["bbox"]), kind, s["source"], s["target"])
        try:  # original font, size, colour, alignment and rotation: the page is redrawn like the original
            got = style_for_box(src_doc[lay["page0"]], seg.bbox)
        except Exception:
            got = None
        if got:
            seg.style, seg.rotate, seg.max_size = got[0], got[1], got[0].size
        render_segs.append(seg)
    src_doc.close()
    if not pages0:
        return names, "NO_TEXT_PAGES"
    try:
        for attempt in (0, 1):
            try:
                render_document(pdf, render_segs, out / "vi.pdf", pages=pages0)
                break
            except RuntimeError:
                raise
            except Exception:  # renderer limitation on a page: retry once with plain overlays
                if attempt:
                    raise
                for r in render_segs:
                    r.kind, r.style = "prose", None
        interleave(pdf, out / "vi.pdf", out / "interleaved.pdf", src_pages=pages0)
    except RuntimeError:  # no Vietnamese font
        return names, "NO_VI_FONT"
    except Exception:
        return names, "RENDER_FAILED"
    return ["out/vi.pdf", "out/interleaved.pdf"] + names, None
