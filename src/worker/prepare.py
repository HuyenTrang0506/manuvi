"""Intake step of the worker: PDF + section list -> chunks.json, layout.json, jobspec.json, terms.json.

Chunks are the text blocks of the job's text pages. Geometry lives in layout.json (no text) so the
renderer can place the Vietnamese later. Nothing here logs or prints document text.
"""
from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from src.intake.jobspec import build_jobspec, write_jobspec
from src.intake.terms import extract_terms, load_patterns
from src.pipeline.schema import CHUNKS_FILE

LAYOUT_FILE = "layout.json"
JOBSPEC_FILE = "jobspec.json"
TERMS_FILE = "terms.json"
MAX_CHUNK_CHARS = 4000


def prepare(work: Path, pdf: Path, sections: str, page_map: dict[str, int],
            priority_sections: list[str] | None = None) -> dict:
    """Run intake. Raises src.intake.jobspec.IntakeError (structural problems) or ValueError."""
    pm_path = None
    if page_map:
        import yaml
        pm_path = work / "page_map.yaml"
        pm_path.write_text(yaml.safe_dump({k: int(v) for k, v in page_map.items()}), "utf-8")
    spec = build_jobspec(pdf, sections, pm_path)
    prio = {str(x) for x in (priority_sections or [])}
    spec.pages.sort(key=lambda p: p.section not in prio)  # page strip shows priority pages first
    write_jobspec(spec, work / JOBSPEC_FILE)
    chunks, layout = [], {}
    # priority sections first (stable inside each group): chunk ids follow processing order
    text_pages = sorted((p for p in spec.pages if not p.scanned), key=lambda p: p.section not in prio)
    with pymupdf.open(pdf) as doc:
        for p in text_pages:
            page = doc[p.pdf_page - 1]
            blocks = sorted((b for b in page.get_text("blocks") if b[6] == 0 and b[4].strip()),
                            key=lambda b: (round(b[1] / 4), b[0]))
            for b in blocks:
                cid = f"C{len(chunks) + 1:04d}"
                text = " ".join(b[4].split())[:MAX_CHUNK_CHARS]
                chunks.append({"id": cid, "page": p.pdf_page, "section": p.section, "source": text})
                layout[cid] = {"page0": p.pdf_page - 1, "bbox": [round(float(x), 2) for x in b[:4]]}
    (work / CHUNKS_FILE).write_text(json.dumps(chunks, ensure_ascii=False), "utf-8")
    (work / LAYOUT_FILE).write_text(json.dumps(layout), "utf-8")
    cands = extract_terms(pdf, load_patterns(), pages=[p.pdf_page for p in text_pages])
    terms = [{"id": f"T{i + 1:04d}", "en": t.term, "vi": "", "decision": "pending", "protected": t.protected,
              "count": t.count, "pages": t.pages[:20]} for i, t in enumerate(cands) if t.translate]
    (work / TERMS_FILE).write_text(json.dumps(terms, ensure_ascii=False), "utf-8")
    return {"pages": spec.page_count, "chunks": len(chunks), "scanned": len(spec.scanned_pages),
            "job_pages": [p.pdf_page for p in spec.pages]}
