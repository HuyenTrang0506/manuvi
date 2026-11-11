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


PAGES_FILE = "out/pages.json"
NEEDS_TEXT_REVIEW = ("needs_review", "error")


def write_pages_json(work: Path, pdf: Path, vi_pdf: Path, spec: dict, pages0: list[int], segs: list[dict],
                     layout: dict, render_segs: list, results: list) -> None:
    """Page list for the web viewer: printed label, page index in the original and in the Vietnamese PDF, and the
    boxes (in points, on the Vietnamese page) of text that still needs a human look. No text of the manual."""
    marks: dict[int, list[dict]] = {}
    for sg in segs:
        lay = layout.get(sg["id"])
        if lay and (sg.get("status") in NEEDS_TEXT_REVIEW or (sg.get("kind") == "signal" and sg.get("decision") == "pending")):
            marks.setdefault(lay["page0"], []).append({"b": lay["bbox"], "t": "text"})
    by_page: dict[int, list] = {}
    for sg in render_segs:
        by_page.setdefault(sg.page, []).append(sg)
    for pr in results:
        for sg, r in zip(by_page.get(pr.page, []), pr.segs):
            if getattr(r, "needs_review", False) or r.overflow:
                marks.setdefault(pr.page, []).append({"b": list(sg.bbox), "t": "layout"})
    with pymupdf.open(str(pdf)) as src, pymupdf.open(str(vi_pdf)) as vi:
        pages = []
        for p in spec["pages"]:
            p0 = p["pdf_page"] - 1
            if not 0 <= p0 < len(src):
                continue
            sp = src[p0]
            idx = pages0.index(p0) if p0 in pages0 else None
            ms = []
            for m in marks.get(p0, []):
                r = pymupdf.Rect(m["b"]) * sp.rotation_matrix
                r.normalize()
                ms.append({"b": [round(v, 1) for v in r], "t": m["t"]})
            pages.append({"label": p.get("label") or f"{p['pdf_page']}", "pdf_page": p["pdf_page"], "vi_index": idx,
                          "src_size": [round(sp.rect.width, 1), round(sp.rect.height, 1)],
                          "vi_size": [round(vi[idx].rect.width, 1), round(vi[idx].rect.height, 1)] if idx is not None else None,
                          "review": any(m["t"] == "text" for m in ms), "layout_review": any(m["t"] == "layout" for m in ms),
                          "marks": ms, "scanned": bool(p.get("scanned"))})
    (work / PAGES_FILE).write_text(json.dumps({"pages": pages}), "utf-8")


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
    results = []
    try:
        for attempt in (0, 1):
            try:
                results = render_document(pdf, render_segs, out / "vi.pdf", pages=pages0)
                break
            except RuntimeError:
                raise
            except Exception:  # renderer limitation on a page: retry once with plain overlays
                if attempt:
                    raise
                for r in render_segs:
                    r.kind, r.style = "prose", None
        interleave(pdf, out / "vi.pdf", out / "interleaved.pdf", src_pages=pages0)
        try:
            write_pages_json(work, pdf, out / "vi.pdf", spec, pages0, segs, layout, render_segs, results)
            names.append("out/pages.json")
        except Exception:  # the page viewer is optional; the PDFs are the deliverable
            pass
    except RuntimeError:  # no Vietnamese font
        return names, "NO_VI_FONT"
    except Exception:
        return names, "RENDER_FAILED"
    return ["out/vi.pdf", "out/interleaved.pdf"] + names, None
