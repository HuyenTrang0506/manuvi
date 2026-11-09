"""Intake step of the worker: PDF + section list -> chunks.json, layout.json, jobspec.json, terms.json.

Chunks are the text blocks of the job's text pages. Geometry lives in layout.json (no text) so the
renderer can place the Vietnamese later. Nothing here logs or prints document text.
"""
from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path
from typing import Mapping, Optional

import pymupdf

from src.intake.jobspec import IntakeError, build_jobspec, write_jobspec
from src.intake.terms import extract_terms, load_patterns
from src.pipeline.schema import CHUNKS_FILE

LAYOUT_FILE = "layout.json"
JOBSPEC_FILE = "jobspec.json"
TERMS_FILE = "terms.json"
MAX_CHUNK_CHARS = 4000
DEFAULT_MAX_PAGES = 600
DEFAULT_TIMEOUT_S = 120.0


class PdfLimitError(ValueError):
    """The (untrusted) PDF is over a configured limit or cannot be read safely. `code` is the short job error code."""

    def __init__(self, message: str, code: str = "PDF_LIMIT"):
        super().__init__(message)
        self.code = code


def limits(env: Optional[Mapping[str, str]] = None) -> tuple[int, float]:
    """MANUVI_MAX_PAGES (default 600) and MANUVI_PREPARE_TIMEOUT in seconds (default 120). Bad values -> defaults."""
    env = os.environ if env is None else env

    def num(name, default, cast):
        try:
            v = cast(env.get(name, ""))
            return v if v > 0 else default
        except ValueError:
            return default
    return num("MANUVI_MAX_PAGES", DEFAULT_MAX_PAGES, int), num("MANUVI_PREPARE_TIMEOUT", DEFAULT_TIMEOUT_S, float)


def _child(conn, args: tuple) -> None:
    """Runs in a separate process: a hostile PDF can hang or crash the parser, never the worker."""
    try:
        conn.send(("ok", _prepare(*args)))
    except IntakeError as e:
        conn.send(("intake", str(e)))
    except PdfLimitError as e:
        conn.send(("limit", str(e), e.code))
    except Exception as e:  # message may quote the document: send only the class name
        conn.send(("error", type(e).__name__))
    finally:
        conn.close()


def run_limited(fn, args: tuple, timeout: float):
    """Run ``fn(*args)`` in a spawned child with a hard deadline. Returns its result or raises PdfLimitError."""
    ctx = multiprocessing.get_context("spawn")
    parent, child = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=fn, args=(child, args), daemon=True)
    proc.start()
    child.close()
    try:
        if not parent.poll(timeout):
            raise PdfLimitError(f"Preparing the PDF took longer than {timeout:g} seconds.", "PDF_TIMEOUT")
        try:
            return parent.recv()
        except EOFError:
            raise PdfLimitError("The PDF could not be read safely.", "PDF_UNREADABLE")
    finally:
        parent.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)


def prepare(work: Path, pdf: Path, sections: str, page_map: dict[str, int],
            priority_sections: list[str] | None = None, *, max_pages: Optional[int] = None,
            timeout: Optional[float] = None) -> dict:
    """Run intake on an untrusted PDF with a page cap and a hard time limit (child process, killed on timeout).

    Raises src.intake.jobspec.IntakeError (structural problems), PdfLimitError (too many pages, too slow,
    unreadable; ``.code`` is PDF_LIMIT / PDF_TIMEOUT / PDF_UNREADABLE) or ValueError.
    """
    env_pages, env_timeout = limits()
    max_pages = max_pages or env_pages
    timeout = timeout or env_timeout
    res = run_limited(_child, (work, pdf, sections, page_map, priority_sections, max_pages), timeout)
    kind = res[0]
    if kind == "ok":
        return res[1]
    if kind == "intake":
        raise IntakeError(res[1])
    if kind == "limit":
        raise PdfLimitError(res[1], res[2])
    raise ValueError(f"prepare failed: {res[1]}")


def _prepare(work: Path, pdf: Path, sections: str, page_map: dict[str, int],
             priority_sections: list[str] | None, max_pages: int) -> dict:
    try:
        with pymupdf.open(pdf) as doc:
            if doc.page_count > max_pages:
                raise PdfLimitError(f"The PDF has {doc.page_count} pages; the limit is {max_pages}.")
    except pymupdf.FileDataError:
        raise PdfLimitError("This file is not a readable PDF.", "PDF_UNREADABLE")
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
