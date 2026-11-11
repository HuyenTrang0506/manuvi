"""Pilot orchestration: sample pages, translate page by page (isolated, resumable), render, report inputs."""
from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import pymupdf

from src.eval.cases import EvalCase
from src.eval.metrics import leakage
from src.intake.vision_detect import analyze_page
from src.intake.vision_read import VisionConfig, VisionReader, load_vision_config
from src.llm_gateway.config import resolve_config
from src.pipeline.demo import write_chunks
from src.pipeline.runner import make_gateway, run_job
from src.pipeline.schema import SEGMENTS_FILE, TRACE_FILE
from src.render.fonts import find_vi_font
from src.render.interleave import interleave
from src.render.page import Segment

from . import render as R
from .extract import page_units, vision_units
from .sample import SectionPage, choose_sample, classify_all, section_pages

DEFAULT_KEEP = ("Kobelco", "ACERA", "SK70SR-1ES", "SK70SR")  # names kept in English, never counted as leaks
REVIEW = ("needs_review", "error")
_MODEL = re.compile(r"^(fake|ollama):[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


class PilotError(Exception):
    """Setup problem the founder can fix (message is safe to print)."""


class VisionReadFailed(Exception):
    """A page without a usable text layer could not be read by the vision model."""


@dataclass
class PageOutcome:
    label: str
    pdf_page: int
    kind: str
    status: str = "pending"            # ok | failed | render_failed
    segments: int = 0                  # units sent to the model
    kept: int = 0                      # blocks left as printed (labels, codes, abbreviations)
    repaired: int = 0
    review: list[dict] = field(default_factory=list)   # {id, rules}
    leaked: int = 0
    flags: list[str] = field(default_factory=list)
    qa_status: str = ""
    seconds: float = 0.0
    error: str = ""
    vision: str = ""                   # "" | page | regions: how much of the page the vision model read
    vision_blocks: int = 0
    vision_calls: int = 0
    vision_flagged: list[dict] = field(default_factory=list)   # {id, reasons, shown}: shown=False is report-only
    vision_note: str = ""              # skipped/failed reads, error classes only


@dataclass
class PilotResult:
    section: str
    model: str
    outcomes: list[PageOutcome]
    out: Path
    files: dict[str, Path]
    warnings: list[str]

    @property
    def succeeded(self) -> int:
        return sum(o.status == "ok" for o in self.outcomes)

    def summary_line(self) -> str:
        n = len(self.outcomes)
        vis = sum(bool(o.vision) and o.status == "ok" for o in self.outcomes)
        bad = sum(o.status in ("failed", "render_failed") for o in self.outcomes)
        rev = sum(len(o.review) for o in self.outcomes)
        flagged = sum(len(o.vision_flagged) for o in self.outcomes)
        return (f"SUMMARY: {self.succeeded} of {n} pages succeeded ({vis} read by vision, {bad} failed); "
                f"{rev} segments routed to review, {flagged} vision blocks flagged")


def resolve_model(profile: str, model: Optional[str]) -> str:
    if model:
        if not _MODEL.match(model):
            raise PilotError(f"--model must look like ollama:<tag> (local models only), got {model!r}")
        return model
    return resolve_config({"MANUVI_PROFILE": profile}).models["bulk"]


def _log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


def _repaired(page_dir: Path) -> int:
    p = page_dir / TRACE_FILE
    if not p.exists():
        return 0
    ids = set()
    for line in p.read_text("utf-8").splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if str(r.get("stage", "")).startswith("fix"):
            ids.add(r.get("chunk_id"))
    return len(ids)


def _meta_seconds(page_dir: Path) -> float:
    try:
        return float(json.loads((page_dir / "pilot-meta.json").read_text("utf-8"))["seconds"])
    except (OSError, ValueError, KeyError):
        return 0.0


def _rules(issues: list[str]) -> str:
    return ",".join(sorted({i.split(":", 1)[0] for i in issues})) or "-"


def _read_with_vision(page, reader: VisionReader, vcfg: VisionConfig, o: PageOutcome, units: list) -> list:
    """Vision-read what the text layer lacks, add the blocks as units, fill the outcome. Returns reported-only blocks."""
    decision, text_blocks = analyze_page(page, vcfg)
    if decision.mode == "none":
        return []
    o.vision = decision.mode
    vr = reader.read_page(page, decision.regions if decision.mode == "regions" else None, text_blocks,
                          dense=decision.dense)
    o.vision_calls, o.vision_blocks = vr.calls, len(vr.blocks)
    vunits, vkept, reported = vision_units(page, vr.blocks)
    o.kept += vkept
    units.extend(vunits)
    notes = sorted(set(vr.errors) | set(vr.skipped)) + (["regions_truncated"] if decision.truncated else [])
    o.vision_note = ", ".join(notes)
    if vr.failed or decision.truncated:
        o.flags.append("vision_incomplete")
    if vr.failed and decision.mode == "page" and not units:
        raise VisionReadFailed(o.vision_note or "no blocks")
    return reported


def _page(pdf: Path, sp: SectionPage, section: str, work: Path, vi_dir: Path, model_env: dict, profile: str,
          adapters, fontfile: str, keep: tuple[str, ...], min_size: float, reader: VisionReader,
          vcfg: VisionConfig) -> PageOutcome:
    o = PageOutcome(sp.label, sp.pdf_page, sp.kind)
    p0 = sp.pdf_page - 1
    out_pdf = vi_dir / f"p{sp.pdf_page:04d}.pdf"
    with pymupdf.open(str(pdf)) as doc:
        units, o.kept = page_units(doc[p0])
        reported = _read_with_vision(doc[p0], reader, vcfg, o, units)
    for b in reported:
        o.vision_flagged.append({"id": "-", "reasons": ",".join(b.reasons), "shown": False})
    o.segments = len(units)
    page_dir = work / f"p{sp.pdf_page:04d}"
    t0 = time.time()
    segs_by_id: dict[str, dict] = {}
    if units:
        write_chunks(page_dir, [{"id": u.id, "page": sp.pdf_page, "section": section, "source": u.text,
                                 "kind": u.kind} for u in units])
        gw = make_gateway(page_dir, profile, adapters=adapters, env=model_env)
        res = run_job(page_dir, gateway=gw, job_id=f"p{sp.pdf_page}", batch_size=1000,
                      approve_batches=True, approve_safety=True)  # safety segments stay needs_review (marked)
        o.seconds = _meta_seconds(page_dir) + time.time() - t0
        (page_dir / "pilot-meta.json").write_text(json.dumps({"seconds": round(o.seconds, 2)}), "utf-8")
        if res.state != "done":
            raise RuntimeError(f"pipeline stopped: {res.state}")
        segs_by_id = {s["id"]: s for s in json.loads((page_dir / SEGMENTS_FILE).read_text("utf-8"))}
        o.repaired = _repaired(page_dir)
    render_segs: list[Segment] = []
    boxes = []
    for u in units:
        s = segs_by_id.get(u.id)
        if s is None:
            s = {"status": "error", "target": "", "issues": ["MISSING: no result"]}
        routed = s["status"] in REVIEW or bool(u.reasons)
        target = (s.get("target") or "").strip()
        if u.reasons:
            o.vision_flagged.append({"id": u.id, "reasons": ",".join(u.reasons), "shown": True})
        if routed:
            rules = ",".join(sorted({*_rules(s.get("issues", [])).split(","), *(f"vision:{r}" for r in u.reasons)} - {"-"}))
            o.review.append({"id": u.id, "rules": rules or "-", "status": s["status"] if s["status"] in REVIEW
                             else "needs_review"})
        if s["status"] == "error" or not target:
            text = R.MARK + "CHƯA DỊCH: " + u.text      # English stays visible but flagged, never passed off as VI
        else:
            text = (R.MARK if routed else "") + target
            ec = EvalCase(id=u.id, source=u.text, domains=["pilot"], protected=list(keep))
            o.leaked += len(leakage(ec, target).detail.get("leaked", []))
        render_segs.append(Segment(p0, u.bbox, u.kind, u.text, text, rotate=u.rotate, max_size=u.max_size))
        if routed:
            boxes.append(u.bbox)
    pr, qa = R.render_page(pdf, p0, render_segs, boxes, out_pdf, fontfile)
    o.qa_status = qa["status"]
    if "overflow" in qa["errors"] or any(r.overflow for r in pr.segs):
        o.flags.append("overflow")
    if any(r.floor or r.stage.endswith("hard_floor") or r.size < min_size for r in pr.segs):
        o.flags.append("font_below_min")
    if pr.legend:
        o.flags.append("legend_fallback")
    for e in qa["errors"]:
        if e != "overflow":
            o.flags.append(e)
    o.status = "ok"
    return o


def run_pilot(pdf: Path, section: str, pages: int, out: Path, *, model: Optional[str] = None,
              profile: str = "qa", adapters=None, fontfile: Optional[str] = None,
              keep: tuple[str, ...] = (), min_size: float = 7.0,
              progress: Callable[[str], None] = _log, vision_model: Optional[str] = None,
              vision_cfg: Optional[VisionConfig] = None) -> PilotResult:
    pdf, out = Path(pdf), Path(out)
    if profile not in ("dev", "qa"):
        raise PilotError("the pilot runs local models only (profile dev or qa)")
    model_name = resolve_model(profile, model)
    font = fontfile or (str(find_vi_font()) if find_vi_font() else None)
    if not font:
        raise PilotError("no Vietnamese font found; set MANUVI_VI_FONT to a .ttf file")
    model_env = {"MANUVI_MODEL_BULK": model_name, "MANUVI_MODEL_STRONG": model_name} if model else {}
    vision_env = {}
    if vision_model:
        if not _MODEL.match(vision_model):
            raise PilotError(f"--vision-model must look like ollama:<tag> (local models only), got {vision_model!r}")
        vision_env = {"MANUVI_MODEL_VISION": vision_model}
    try:
        doc = pymupdf.open(str(pdf))
    except Exception as e:
        raise PilotError(f"cannot open the PDF ({type(e).__name__})")
    with doc:
        found, warns = section_pages(doc, section)
        if not found:
            raise PilotError(f"no pages with printed label {section}-<n> found in the PDF")
        found = classify_all(doc, found)
    sample = choose_sample(found, pages)
    progress(f"section {section}: {len(found)} pages found, sampling {len(sample)}: "
             + ", ".join(p.label for p in sample))
    work, vi_dir = out / "work", out / "work" / "vi-pages"
    vi_dir.mkdir(parents=True, exist_ok=True)
    vcfg = vision_cfg or load_vision_config()
    (work / "vision").mkdir(parents=True, exist_ok=True)
    reader = VisionReader(make_gateway(work / "vision", profile, adapters=adapters, env=vision_env),
                          "pilot-vision", vcfg)
    outcomes: list[PageOutcome] = []
    keep_all = tuple(dict.fromkeys(DEFAULT_KEEP + tuple(keep)))
    for sp in sample:
        t0 = time.time()
        try:
            o = _page(pdf, sp, section, work, vi_dir, model_env, profile, adapters, font, keep_all, min_size, reader, vcfg)
        except Exception as e:  # never log source text: class name only
            o = PageOutcome(sp.label, sp.pdf_page, sp.kind, status="failed", error=type(e).__name__,
                            seconds=time.time() - t0)
            progress(f"page {sp.label}: failed ({o.error})")
            try:
                R.unprocessed_page(pdf, sp.pdf_page - 1,
                                   "LỖI: không đọc được chữ trên ảnh, chưa dịch" if o.error == "VisionReadFailed"
                                   else "LỖI: trang chưa được dịch", vi_dir / f"p{sp.pdf_page:04d}.pdf", font)
            except Exception as e2:
                o.status, o.error = "render_failed", type(e2).__name__
                progress(f"page {sp.label}: banner failed ({o.error})")
        else:
            progress(f"page {sp.label}: {o.status}, {o.segments} segments, {len(o.review)} to review")
        outcomes.append(o)
    files = _assemble(pdf, section, sample, outcomes, vi_dir, out)
    return PilotResult(section, model_name, outcomes, out, files, warns)


def _assemble(pdf: Path, section: str, sample: list[SectionPage], outcomes: list[PageOutcome], vi_dir: Path,
              out: Path) -> dict[str, Path]:
    vi = pymupdf.open()
    used = []
    for sp in sample:
        p = vi_dir / f"p{sp.pdf_page:04d}.pdf"
        if p.exists():
            with pymupdf.open(str(p)) as d:
                vi.insert_pdf(d)
            used.append(sp.pdf_page - 1)
    vi_path = out / f"section{section}-vi.pdf"
    vi.save(str(vi_path), garbage=3, deflate=True)
    vi.close()
    files = {"vi": vi_path}
    if used:
        il = out / f"section{section}-interleaved-review.pdf"
        interleave(pdf, vi_path, il, src_pages=used)
        files["interleaved"] = il
    return files
