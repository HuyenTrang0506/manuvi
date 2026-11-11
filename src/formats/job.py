"""Glue between format adapters and the job directory / pipeline / worker.

    prepare_file(work, src)        file -> chunks.json (+ format.json); same pipeline input as PDF intake
    build_file_output(work, src)   segments.json (human-reviewed targets) -> out/<name>.vi.<ext>
    translate_file(src, out_dir)   the whole thing for the CLI, offline with the fake model

segments.json has exactly the PDF shape (id, page, section, source, target, status, issues, risk, kind, decision), so
the review app shows EN/VI side by side for any format. `source` and `target` carry the inline tags (`<b1>..</b1>`).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

from src.common.filelock import file_lock
from src.pipeline.schema import CHUNKS_FILE, SEGMENTS_FILE

from . import get_adapter, out_name
from .base import FormatError, RebuildReport
from .detect import detect_format

FORMAT_FILE = "format.json"
MAX_SEGMENTS = 20000
MAX_SEGMENT_CHARS = 8000


def input_name(fmt: str) -> str:
    """Name of the uploaded source inside a job (store-safe, one fixed name per format)."""
    return f"input.{fmt}"


def prepare_file(work: Path, src: Path, *, original_name: str = "") -> dict:
    """Extract segments of an untrusted file into `work/chunks.json`. Raises FormatError(code)."""
    fmt = detect_format(src)
    segs = get_adapter(fmt).extract(src)
    if len(segs) > MAX_SEGMENTS:
        raise FormatError("TOO_MANY_SEGMENTS", f"{len(segs)} segments; limit {MAX_SEGMENTS}")
    chunks = [{"id": s.id, "page": s.page, "section": s.section, "source": s.text[:MAX_SEGMENT_CHARS],
               "kind": s.pipeline_kind, "protect": s.protected, "fmt": fmt, "ctx": s.context[:200]}
              for s in segs if s.text.strip()]
    work.mkdir(parents=True, exist_ok=True)
    (work / CHUNKS_FILE).write_text(json.dumps(chunks, ensure_ascii=False), "utf-8")
    digest = hashlib.sha256(Path(src).read_bytes()).hexdigest()
    (work / FORMAT_FILE).write_text(json.dumps(
        {"format": fmt, "name": original_name or Path(src).name, "sha256": digest, "segments": len(chunks)}), "utf-8")
    pages = max((c["page"] for c in chunks), default=1)
    return {"format": fmt, "pages": pages, "chunks": len(chunks)}


def read_format(work: Path) -> Optional[dict]:
    p = work / FORMAT_FILE
    return json.loads(p.read_text("utf-8")) if p.exists() else None


def translations_from_segments(segs: list[dict]) -> dict[str, str]:
    """Reviewed targets; a rejected, failed or empty segment keeps the source text (never ships a blank)."""
    out = {}
    for s in segs:
        t = s.get("target") or ""
        if t.strip() and s.get("decision") != "rejected" and s.get("status") != "error":
            out[s["id"]] = t
    return out


def build_file_output(work: Path, src: Path, out_dir: Optional[Path] = None, *, name: str = "") -> tuple[Path, RebuildReport]:
    fmt = detect_format(src)
    sp = work / SEGMENTS_FILE
    segs = json.loads(sp.read_text("utf-8")) if sp.exists() else []
    out_dir = out_dir or work / "out"
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / out_name(name or (read_format(work) or {}).get("name") or src.name, fmt)
    rep = get_adapter(fmt).rebuild(src, translations_from_segments(segs), dest)
    if rep.needs_review and sp.exists():       # e.g. text overflow in a slide: show it in the review app
        with file_lock(sp):
            cur = json.loads(sp.read_text("utf-8"))
            for s in cur:
                why = rep.needs_review.get(s["id"])
                if why and why not in s.get("issues", []):
                    s.setdefault("issues", []).append(why)
                    if s.get("status") in ("ok", "warn"):
                        s["status"] = "needs_review"
            sp.write_text(json.dumps(cur, ensure_ascii=False, indent=1), "utf-8")
    return dest, rep


def translate_file(src: Path, out_dir: Path, *, model: str = "fake", job_dir: Optional[Path] = None,
                   budget_usd: Optional[float] = None, emit=lambda s: None) -> dict:
    """Whole-file translation for the CLI. `model`: `fake` (offline demo translator) or `<adapter>:<model_id>`
    (e.g. `ollama:qwen2.5:7b`, local profile `qa`). Safety (signal) gates are passed so the file completes; those
    segments stay `needs_review` in segments.json for a human."""
    from src.pipeline.demo import DemoTranslator
    from src.pipeline.runner import make_gateway, run_job

    src, out_dir = Path(src), Path(out_dir)
    job_dir = Path(job_dir) if job_dir else out_dir / f".manuvi-{src.stem}"
    info = prepare_file(job_dir, src)
    if model == "fake":
        gw = make_gateway(job_dir, "dev", adapters={"fake": DemoTranslator()})
    else:
        gw = make_gateway(job_dir, "qa", env={"MANUVI_MODEL_BULK": model})
    res = run_job(job_dir, gateway=gw, approve_batches=True, approve_safety=True, budget_usd=budget_usd,
                  batch_size=1000, emit=emit)
    if res.state != "done":
        return {"state": res.state, "message": res.message, **info}
    dest, rep = build_file_output(job_dir, src, out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    seg_copy = out_dir / (dest.name + ".segments.json")
    seg_copy.write_text((job_dir / SEGMENTS_FILE).read_text("utf-8"), "utf-8")
    return {"state": "done", "output": str(dest), "segments": str(seg_copy), "needs_review": rep.needs_review,
            "applied": rep.segments_applied, "notes": rep.notes, "status": res.status, **info}
