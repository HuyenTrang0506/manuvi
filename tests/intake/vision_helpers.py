"""Synthetic PDFs and a scripted fake VLM for the vision-read tests (offline, $0)."""
from __future__ import annotations

import json
from typing import Callable, Optional

import pymupdf

from src.intake.vision_read import to_visible
from src.llm_gateway import Gateway
from src.llm_gateway.adapters import FakeAdapter, estimate_tokens

PROSE = ("Check the hydraulic pump pressure before starting the engine. Inspect the hoses for cracks "
         "or leaks and replace the fuel filter every 500 hours.")


def raster_label(text: str, w: int = 230, h: int = 54, fontsize: float = 14) -> bytes:
    """PNG of printed text: what a diagram label embedded as a picture looks like."""
    d = pymupdf.open()
    p = d.new_page(width=w, height=h)
    p.insert_text((6, h * 0.62), text, fontsize=fontsize)
    return p.get_pixmap(dpi=144).tobytes("png")


def image_only_page(doc, texts=("FUSE BOX", "MAIN RELAY"), size=(595, 842)):
    """A page that is one big picture of text, no text layer. Returns [(text, pdf rect)]."""
    src = pymupdf.open()
    sp = src.new_page(width=size[0], height=size[1])
    truth = []
    y = 120
    for t in texts:
        sp.insert_text((80, y), t, fontsize=18)
        truth.append((t, pymupdf.Rect(80, y - 18, 80 + 11 * len(t), y + 5)))
        y += 120
    png = sp.get_pixmap(dpi=100).tobytes("png")
    p = doc.new_page(width=size[0], height=size[1])
    p.insert_image(p.rect, stream=png)
    return truth


def mixed_page(doc, label_text="CAB WORK LIGHT", rect=(300, 400, 530, 454), with_prose=True):
    """Text layer plus one raster label. Returns the label's pdf rect."""
    p = doc.new_page()
    if with_prose:
        p.insert_textbox(pymupdf.Rect(72, 72, 520, 160), PROSE, fontsize=11)
    r = pymupdf.Rect(*rect)
    p.insert_image(r, stream=raster_label(label_text))
    return r


def one_page_pdf(build: Callable, path):
    doc = pymupdf.open()
    out = build(doc)
    doc.save(str(path))
    doc.close()
    return out


def scripted_answer(target_id: str, truth, extra=(), conf: float = 0.93, kind: str = "label") -> str:
    """JSON answer of a perfect reader for the tile in target_id (p<n>:x0,y0,x1,y1, visible space).
    `truth` items are (text, visible rect) or (text, visible rect, confidence)."""
    x0, y0, x1, y1 = (float(v) for v in target_id.split(":")[1].split(","))
    w, h = x1 - x0, y1 - y0
    blocks = []
    for item in truth:
        t, r = item[0], item[1]
        c = pymupdf.Point((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2)
        if x0 <= c.x <= x1 and y0 <= c.y <= y1:
            blocks.append({"text": t, "kind": kind, "confidence": item[2] if len(item) > 2 else conf,
                           "bbox": [(r.x0 - x0) / w, (r.y0 - y0) / h, (r.x1 - x0) / w, (r.y1 - y0) / h]})
    return json.dumps({"blocks": blocks + list(extra)})


class ScriptedVLM(FakeAdapter):
    """Fake VLM: answers with the truth boxes that fall in the requested tile. `extra`: raw block dicts
    appended to every answer; `raw`: a fixed response text instead."""

    name = "fake"

    def __init__(self, truth_visible=(), extra=(), raw: Optional[str] = None, confidence: float = 0.93, **kw):
        super().__init__(**kw)
        self.truth, self.extra, self.raw, self.conf = list(truth_visible), list(extra), raw, confidence
        self.requests = []

    def complete(self, req, model_id):
        self.calls += 1
        self.requests.append(req)
        text = self.raw if self.raw is not None else scripted_answer(req.target_id, self.truth, self.extra, self.conf)
        return {"text": text, "tokens_in": estimate_tokens(req.messages), "tokens_out": estimate_tokens(text)}


def visible_truth(page, items):
    """[(text, pdf rect)] -> [(text, visible rect)] for ScriptedVLM."""
    return [(t, to_visible(page, pymupdf.Rect(r))) for t, r in items]


def make_gateway(tmp_path, adapter, **env):
    return Gateway(cache_path=tmp_path / "cache.sqlite", env={"MANUVI_PROFILE": "dev", **env},
                   adapters={"fake": adapter})
