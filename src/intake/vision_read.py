"""Read text that has no usable text layer with a vision-language model (VLM). No traditional OCR.

The page (or an image region of it) is rendered to PNG and sent through the LLM gateway alias `vision` with
data_class=customer, so only local/private models can see it (the gateway refuses anything else). The model
returns text blocks with boxes normalised to 0..1; this module maps them back to PDF coordinates, validates
them and merges them with the text-layer blocks. A block that fails a check is flagged `needs_review`; it is
never translated or shown as if it were certain.

Coordinate convention ("pdf space"): the space of `page.get_text()` and `page.get_image_info()`, i.e.
unrotated, relative to the CropBox, origin top-left. The PNG shows the *visible* page (after /Rotate), so a
box goes visible-space -> pdf space with `page.derotation_matrix`.
"""
from __future__ import annotations

import base64
import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional

import pymupdf
import yaml
from pydantic import BaseModel, ValidationError, field_validator

from src.guard.masking import load_patterns
from src.guard.script import _blocked
from src.llm_gateway.errors import (BudgetExceeded, DataClassViolation, NotConfigured, RecordedMiss)
from src.llm_gateway.models import LLMRequest

PROMPT_VERSION = "vision-read-v1"
KINDS = ("label", "paragraph", "table_cell", "legend")
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "vision.yaml"

SYSTEM_PROMPT = (
    "You read text in images of technical manual pages. You are a reader, not a translator or an editor.\n"
    "Rules:\n"
    "1. Return only text that is really visible in the image, exactly as printed (same language, same spelling, "
    "same capitals). Never translate, correct, complete or invent text.\n"
    "2. Keep every code, part number, wire colour code, pin number and value with its unit character for character.\n"
    "3. One block per label, paragraph, table cell or legend entry, in reading order.\n"
    "4. bbox is [x0, y0, x1, y1], each between 0 and 1, relative to the image width and height, origin at the "
    "top-left corner, tightly around the text.\n"
    "5. kind is one of: label, paragraph, table_cell, legend.\n"
    "6. confidence is between 0 and 1: your certainty that the text is read correctly.\n"
    "7. If the image has no readable text, return {\"blocks\": []}.\n"
    "Return JSON only, no markdown, in the form "
    "{\"blocks\": [{\"text\": \"...\", \"bbox\": [0.1, 0.2, 0.3, 0.25], \"kind\": \"label\", \"confidence\": 0.9}]}."
)
USER_PROMPT = "Read all text in this image and return the JSON."

RESPONSE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {
        "name": "page_text_blocks",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {"blocks": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "bbox": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4},
                    "kind": {"type": "string", "enum": list(KINDS)},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["text", "bbox", "kind", "confidence"],
                "additionalProperties": False,
            }}},
            "required": ["blocks"],
            "additionalProperties": False,
        },
    },
}


# --------------------------------------------------------------------------- config
@dataclass(frozen=True)
class VisionConfig:
    target_long_px: int = 1800        # long side of a whole-page render
    dpi_min: float = 110.0
    dpi_max: float = 250.0
    dense_dpi: float = 220.0          # dense pages are rendered sharper, which makes tiles
    max_tile_px: int = 2048           # longest allowed image side; larger areas are cut into tiles
    tile_overlap: float = 0.08
    min_confidence: float = 0.5
    bbox_tolerance: float = 0.01      # share of the region a box may stick out before it is flagged
    bbox_scale: float = 1.0           # 1.0 = boxes in 0..1; some models answer in 0..1000 (set 1000)
    max_calls_per_job: int = 40       # model calls (cache hits are free)
    max_seconds_per_job: float = 900.0
    max_regions_per_page: int = 12
    max_tokens: int = 4096
    min_density_pt2_per_char: float = 3.0   # a box with less area per character cannot hold the text
    # detection (see vision_detect)
    min_page_chars: int = 10
    min_image_area_frac: float = 0.004
    min_image_px: int = 48
    sparse_chars_per_10k_pt2: float = 3.0


def load_vision_config(path: Optional[Path] = None, env: Optional[dict] = None, **overrides) -> VisionConfig:
    """config/vision.yaml, then MANUVI_VISION_<FIELD> env vars, then keyword overrides."""
    import os
    env = os.environ if env is None else env
    p = Path(path) if path else DEFAULT_CONFIG
    data = (yaml.safe_load(p.read_text("utf-8")) or {}) if p.exists() else {}
    fields = VisionConfig.__dataclass_fields__
    vals = {k: v for k, v in data.items() if k in fields}
    for k, f in fields.items():
        e = env.get(f"MANUVI_VISION_{k.upper()}")
        if e not in (None, ""):
            vals[k] = type(f.default)(e)
    vals.update({k: v for k, v in overrides.items() if k in fields})
    return VisionConfig(**vals)


# --------------------------------------------------------------------------- geometry
def pdf_bounds(page: pymupdf.Page) -> pymupdf.Rect:
    """The visible page area expressed in pdf space."""
    return (page.rect * page.derotation_matrix).normalize()


def to_visible(page: pymupdf.Page, r: pymupdf.Rect) -> pymupdf.Rect:
    return ((pymupdf.Rect(r) * page.rotation_matrix).normalize() & page.rect)


def text_rotation(page: pymupdf.Page) -> int:
    """Rotation (0/90/180/270, same convention as pilot Unit.rotate) of text that reads horizontally on the
    visible page, expressed in pdf space."""
    m = page.derotation_matrix
    v = pymupdf.Point(1, 0) * pymupdf.Matrix(m.a, m.b, m.c, m.d, 0, 0)
    if abs(v.x) >= abs(v.y):
        return 0 if v.x >= 0 else 180
    return 90 if v.y < 0 else 270


def choose_dpi(rect: pymupdf.Rect, cfg: VisionConfig, dense: bool = False) -> float:
    long_pt = max(rect.width, rect.height, 1.0)
    dpi = min(max(cfg.target_long_px / (long_pt / 72.0), cfg.dpi_min), cfg.dpi_max)
    return max(dpi, cfg.dense_dpi) if dense else dpi


def plan_tiles(rect: pymupdf.Rect, cfg: VisionConfig, dense: bool = False) -> tuple[list[pymupdf.Rect], float]:
    """(tiles in visible space, dpi). One tile when the render fits max_tile_px, else a grid with overlap."""
    dpi = choose_dpi(rect, cfg, dense)
    cols = max(1, math.ceil(rect.width / 72.0 * dpi / cfg.max_tile_px))
    rows = max(1, math.ceil(rect.height / 72.0 * dpi / cfg.max_tile_px))
    if cols == rows == 1:
        return [pymupdf.Rect(rect)], dpi
    tw, th = rect.width / cols, rect.height / rows
    ox, oy = tw * cfg.tile_overlap, th * cfg.tile_overlap
    tiles = []
    for r in range(rows):
        for c in range(cols):
            tiles.append(pymupdf.Rect(max(rect.x0, rect.x0 + c * tw - ox), max(rect.y0, rect.y0 + r * th - oy),
                                      min(rect.x1, rect.x0 + (c + 1) * tw + ox),
                                      min(rect.y1, rect.y0 + (r + 1) * th + oy)))
    return tiles, dpi


def render_clip(page: pymupdf.Page, clip: pymupdf.Rect, dpi: float) -> tuple[bytes, bool]:
    """(PNG bytes, blank) for a visible-space clip. Annotations are not drawn; text layer is drawn too, which
    is what a reader sees."""
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(z, z), clip=clip, alpha=False)
    blank = pix.color_topusage()[0] >= 0.999995
    return pix.tobytes("png"), blank


def map_bbox(nbox: Iterable[float], clip: pymupdf.Rect, page: pymupdf.Page, scale: float = 1.0) -> pymupdf.Rect:
    """Normalised box in the image of `clip` (visible space) -> pdf space."""
    x0, y0, x1, y1 = (float(v) / scale for v in nbox)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    vis = pymupdf.Rect(clip.x0 + x0 * clip.width, clip.y0 + y0 * clip.height,
                       clip.x0 + x1 * clip.width, clip.y0 + y1 * clip.height)
    return (vis * page.derotation_matrix).normalize()


def iou(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    inter = (a & b)
    ia = inter.get_area() if not inter.is_empty else 0.0
    u = a.get_area() + b.get_area() - ia
    return ia / u if u > 0 else 0.0


def overlap_small(a: pymupdf.Rect, b: pymupdf.Rect) -> float:
    """Intersection over the smaller area."""
    inter = a & b
    ia = inter.get_area() if not inter.is_empty else 0.0
    m = min(a.get_area(), b.get_area())
    return ia / m if m > 0 else 0.0


# --------------------------------------------------------------------------- response parsing
class RawBlock(BaseModel):
    text: str
    bbox: list[float]
    kind: str = "label"
    confidence: Optional[float] = None

    @field_validator("bbox")
    @classmethod
    def _four(cls, v):
        if len(v) != 4 or not all(math.isfinite(x) for x in v):
            raise ValueError("bbox needs 4 finite numbers")
        return v


class VisionParseError(ValueError):
    pass


def parse_response(text: str) -> tuple[list[RawBlock], int]:
    """(valid blocks, count of malformed block entries). Raises VisionParseError when the JSON itself is bad."""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        data = json.loads(t)
    except ValueError as e:
        raise VisionParseError("response is not JSON") from e
    rows = data.get("blocks") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise VisionParseError("response has no blocks list")
    out, bad = [], 0
    for r in rows:
        try:
            out.append(RawBlock.model_validate(r))
        except (ValidationError, TypeError):
            bad += 1
    return out, bad


# --------------------------------------------------------------------------- blocks and validation
@dataclass
class TextBlock:
    """A block read from the text layer (input of the merge)."""
    text: str
    bbox: tuple[float, float, float, float]


@dataclass
class VisionBlock:
    text: str
    bbox: tuple[float, float, float, float]      # pdf space
    kind: str = "label"
    confidence: float = 0.0
    needs_review: bool = False
    reasons: list[str] = field(default_factory=list)
    source: str = "vision"

    @property
    def blocking(self) -> bool:
        """Never translated or drawn: only reported."""
        return bool(set(self.reasons) & BLOCKING)

    def flag(self, reason: str) -> None:
        self.needs_review = True
        if reason not in self.reasons:
            self.reasons.append(reason)


BLOCKING = {"script", "bbox_degenerate"}
_WS = re.compile(r"\s+")
_CONFUSABLE = str.maketrans({"O": "0", "o": "0", "I": "1", "l": "1", "S": "5", "B": "8", "Z": "2"})
_TOKEN = re.compile(r"[^\s,;:()\[\]]+")
_CODE_SHAPE = re.compile(r"^[A-Za-z]{1,3}[0-9OIlSBZ]{2,}[-.A-Za-z0-9]*$")


def norm_text(s: str) -> str:
    return _WS.sub(" ", s).strip().casefold()


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"\w+", norm_text(s)))


def similar(a: str, b: str) -> bool:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return False
    if na in nb or nb in na:
        return True
    ta, tb = _tokens(a), _tokens(b)
    return bool(ta and tb) and len(ta & tb) / len(ta | tb) >= 0.5


def has_japanese(text: str) -> bool:
    return any(0x3040 <= ord(c) <= 0x30FF or 0x3400 <= ord(c) <= 0x9FFF or 0xFF66 <= ord(c) <= 0xFF9F for c in text)


def _edit1(a: str, b: str) -> bool:
    """Levenshtein distance exactly 1."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        return sum(x != y for x, y in zip(a, b)) == 1
    s, l = (a, b) if len(a) < len(b) else (b, a)
    return any(l[:i] + l[i + 1:] == s for i in range(len(l)))


def code_issues(text: str, known_codes: set[str]) -> list[str]:
    """Plausibility of codes and numbers found by the VLM, using the guard patterns.
    - near-miss: a token that matches a protected pattern once O/0, l/1, S/5, B/8 are normalised but not as read
    - a code one edit away from a different code of the page text layer (misread or invented variant)"""
    out: list[str] = []
    pats = load_patterns()
    for _, rx in pats:
        for m in rx.finditer(text):
            tok = m.group(0)
            if any(0 < len(k) and _edit1(tok.strip(), k) for k in known_codes):
                out.append("code_vs_textlayer")
    for tok in _TOKEN.findall(text):
        if not _CODE_SHAPE.match(tok) or any(rx.fullmatch(tok) for _, rx in pats):
            continue
        fixed = tok.translate(_CONFUSABLE)
        if fixed != tok and any(rx.fullmatch(fixed) for _, rx in pats):
            out.append("code_confusable")
    return sorted(set(out))


def _repetitive(text: str) -> bool:
    w = norm_text(text).split()
    return len(w) >= 8 and len(set(w)) / len(w) < 0.3


def validate_blocks(blocks: list[VisionBlock], bounds: pymupdf.Rect, region: pymupdf.Rect, cfg: VisionConfig,
                    allow_cjk: bool, known_codes: set[str]) -> tuple[list[VisionBlock], dict[str, int]]:
    """Flag or drop blocks. Returns (kept blocks, dropped count by reason)."""
    dropped: dict[str, int] = {}

    def drop(reason):
        dropped[reason] = dropped.get(reason, 0) + 1

    keep: list[VisionBlock] = []
    tol_x, tol_y = cfg.bbox_tolerance * region.width, cfg.bbox_tolerance * region.height
    area = (region & bounds) if not (region & bounds).is_empty else bounds
    for b in blocks:
        b.text = _WS.sub(" ", b.text).strip()
        if not b.text:
            drop("empty")
            continue
        r = pymupdf.Rect(b.bbox)
        if r.is_empty or r.get_area() <= 0 or (r & area).is_empty:
            b.flag("bbox_degenerate")
            keep.append(b)  # reported only (blocking)
            continue
        if (r.x0 < area.x0 - tol_x or r.y0 < area.y0 - tol_y or r.x1 > area.x1 + tol_x or r.y1 > area.y1 + tol_y):
            b.flag("bbox_oob")
        r = r & area
        b.bbox = (r.x0, r.y0, r.x1, r.y1)
        if any(_blocked(c) for c in b.text) and not allow_cjk:
            b.flag("script")
        if b.confidence < cfg.min_confidence:
            b.flag("low_confidence")
        for why in code_issues(b.text, known_codes):
            b.flag(why)
        if r.get_area() / max(len(b.text), 1) < cfg.min_density_pt2_per_char:
            b.flag("text_box_mismatch")
        if _repetitive(b.text):
            b.flag("repetition")
        keep.append(b)
    # overlaps > 50% (intersection over the smaller box): exact repeats are merged, others flagged
    keep.sort(key=lambda x: -x.confidence)
    out: list[VisionBlock] = []
    for b in keep:
        if b.blocking:
            out.append(b)
            continue
        clash = None
        for o in out:
            if o.blocking:
                continue
            if overlap_small(pymupdf.Rect(b.bbox), pymupdf.Rect(o.bbox)) > 0.5:
                clash = o
                break
        if clash is None:
            out.append(b)
        elif norm_text(clash.text) == norm_text(b.text):
            drop("duplicate")
        else:
            b.flag("overlap")
            clash.flag("overlap")
            out.append(b)
    return out, dropped


def dedupe_tiles(blocks: list[VisionBlock]) -> list[VisionBlock]:
    """Tiles overlap, so the same text can be read twice: keep the more confident copy."""
    out: list[VisionBlock] = []
    for b in sorted(blocks, key=lambda x: -x.confidence):
        r = pymupdf.Rect(b.bbox)
        if any(overlap_small(r, pymupdf.Rect(o.bbox)) > 0.5 and similar(b.text, o.text) for o in out):
            continue
        out.append(b)
    return out


def merge_with_text_layer(vision: list[VisionBlock], text_blocks: list[TextBlock]) -> tuple[list[VisionBlock], int]:
    """Drop vision blocks that repeat a text-layer block (same place and similar text); flag those that sit on
    different text-layer text. Returns (kept vision blocks, duplicates dropped). Text-layer blocks always win."""
    keep, dups = [], 0
    for b in vision:
        r = pymupdf.Rect(b.bbox)
        hit = None
        for t in text_blocks:
            if overlap_small(r, pymupdf.Rect(t.bbox)) > 0.5 or iou(r, pymupdf.Rect(t.bbox)) > 0.3:
                hit = t
                if similar(b.text, t.text):
                    break
        if hit is None:
            keep.append(b)
        elif similar(b.text, hit.text):
            dups += 1
        else:
            b.flag("conflicts_text_layer")
            keep.append(b)
    return keep, dups


# --------------------------------------------------------------------------- reader
@dataclass
class PageRead:
    mode: str = ""                         # "", "page", "regions"
    blocks: list[VisionBlock] = field(default_factory=list)
    calls: int = 0
    cache_hits: int = 0
    regions: int = 0
    regions_read: int = 0
    duplicates: int = 0
    dropped: dict[str, int] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)   # budget_calls | budget_time | blank
    errors: list[str] = field(default_factory=list)    # error class names, never page text

    @property
    def failed(self) -> bool:
        """The page needed the VLM and did not get a full read."""
        return bool(self.errors) or any(s.startswith("budget") for s in self.skipped)

    @property
    def flagged(self) -> list[VisionBlock]:
        return [b for b in self.blocks if b.needs_review]


class VisionReader:
    """One per job: it owns the call and time budget. `gateway` is a src.llm_gateway Gateway."""

    def __init__(self, gateway, job_id: str, cfg: Optional[VisionConfig] = None,
                 clock: Callable[[], float] = time.monotonic):
        self.gw, self.job_id = gateway, job_id
        self.cfg = cfg or load_vision_config()
        self._clock = clock
        self.calls = 0
        self.seconds = 0.0

    def budget_left(self) -> Optional[str]:
        if self.calls >= self.cfg.max_calls_per_job:
            return "budget_calls"
        if self.seconds >= self.cfg.max_seconds_per_job:
            return "budget_time"
        return None

    def _ask(self, png: bytes, target_id: str):
        url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        req = LLMRequest(
            messages=[{"role": "system", "content": SYSTEM_PROMPT},
                      {"role": "user", "content": [{"type": "text", "text": USER_PROMPT},
                                                   {"type": "image_url", "image_url": {"url": url}}]}],
            response_format=RESPONSE_SCHEMA, model_alias="vision", data_class="customer",
            job_id=self.job_id, target_id=target_id, prompt_version=PROMPT_VERSION,
            source_hash=hashlib.sha256(png).hexdigest(), max_tokens=self.cfg.max_tokens)
        t0 = self._clock()
        try:
            return self.gw.complete(req)
        finally:
            self.seconds += self._clock() - t0

    def read_page(self, page: pymupdf.Page, regions_pdf: Optional[list[pymupdf.Rect]] = None,
                  text_blocks: Optional[list[TextBlock]] = None, dense: bool = False,
                  allow_cjk: Optional[bool] = None) -> PageRead:
        """Read the whole visible page (regions_pdf None) or only the given pdf-space regions.
        DataClassViolation, NotConfigured and BudgetExceeded from the gateway propagate (setup/policy
        problems must never be hidden); other model errors are recorded on the result."""
        text_blocks = text_blocks or []
        res = PageRead(mode="page" if regions_pdf is None else "regions")
        bounds = pdf_bounds(page)
        regions = [bounds] if regions_pdf is None else [pymupdf.Rect(r) & bounds for r in regions_pdf]
        regions = [r for r in regions if not r.is_empty]
        res.regions = len(regions)
        if allow_cjk is None:
            allow_cjk = any(has_japanese(t.text) for t in text_blocks)
        known = {m.group(0).strip() for t in text_blocks for _, rx in load_patterns() for m in rx.finditer(t.text)}
        for region in regions:
            raw: list[VisionBlock] = []
            tiles, dpi = plan_tiles(to_visible(page, region), self.cfg, dense)
            read_any = False
            for clip in tiles:
                why = self.budget_left()
                if why:
                    res.skipped.append(why)
                    break
                png, blank = render_clip(page, clip, dpi)
                if blank:
                    res.skipped.append("blank")
                    continue
                tid = f"p{page.number + 1}:{clip.x0:.2f},{clip.y0:.2f},{clip.x1:.2f},{clip.y1:.2f}"
                try:
                    out = self._ask(png, tid)
                except (DataClassViolation, NotConfigured, BudgetExceeded, RecordedMiss):
                    raise
                except Exception as e:  # model/transport error: class name only
                    res.errors.append(type(e).__name__)
                    continue
                if out.cache_hit:
                    res.cache_hits += 1
                else:
                    res.calls += 1
                    self.calls += 1
                try:
                    rows, bad = parse_response(out.text)
                except VisionParseError:
                    res.errors.append("VisionParseError")
                    continue
                if bad:
                    res.dropped["malformed"] = res.dropped.get("malformed", 0) + bad
                read_any = True
                for r in rows:
                    kind = r.kind if r.kind in KINDS else "label"
                    box = map_bbox(r.bbox, clip, page, self.cfg.bbox_scale)
                    b = VisionBlock(r.text, (box.x0, box.y0, box.x1, box.y1), kind, r.confidence or 0.0)
                    if r.confidence is None:
                        b.flag("low_confidence")
                    if r.kind not in KINDS:
                        b.flag("bad_kind")
                    raw.append(b)
            res.regions_read += int(read_any)
            # blocks of one region must lie inside it
            kept, dropped = validate_blocks(dedupe_tiles(raw), bounds, region, self.cfg, allow_cjk, known)
            for k, v in dropped.items():
                res.dropped[k] = res.dropped.get(k, 0) + v
            res.blocks += kept
        res.blocks, res.duplicates = merge_with_text_layer(res.blocks, text_blocks)
        res.blocks.sort(key=lambda b: (round(b.bbox[1] / 4), b.bbox[0]))
        return res
