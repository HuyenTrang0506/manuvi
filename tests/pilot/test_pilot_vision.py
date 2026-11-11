"""Pages without a usable text layer are read by the vision model and flow through translation and rendering."""
import json

import pymupdf
import pytest

from src.intake.vision_read import VisionConfig, to_visible
from src.pilot import run_pilot
from src.pilot.report import write_report
from tests.intake.vision_helpers import PROSE, image_only_page, raster_label, scripted_answer
from tests.pilot.conftest import Counting


def _label(page, text):
    page.insert_text((280, page.rect.height - 30), text, fontsize=9)


@pytest.fixture
def raster_manual(tmp_path):
    """43-1 plain text, 43-2 raster label inside a text page, 43-3 same on a /Rotate 90 page, 43-4 image only."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 72, 520, 150), PROSE, fontsize=11)
    _label(p, "43-1")
    truth = {}
    for n, rot, text in ((2, 0, "CAB WORK LIGHT"), (3, 90, "WIPER MOTOR")):
        p = doc.new_page()
        p.insert_textbox(pymupdf.Rect(72, 72, 520, 150), PROSE, fontsize=11)
        p.insert_image(pymupdf.Rect(300, 400, 530, 454), stream=raster_label(text))
        _label(p, f"43-{n}")
        r = pymupdf.Rect(p.get_image_info()[0]["bbox"])
        if rot:
            p.set_rotation(rot)
        truth[n] = [(text, to_visible(p, r))]
    first_scan = len(doc)
    scan = image_only_page(doc, ("Check the fuse box before service.", "Relay YT1234 is wired here."))
    p = doc[-1]
    _label(p, "43-4")
    truth[4] = [(t, to_visible(p, r)) for t, r in scan]
    path = tmp_path / "manual.pdf"
    doc.save(str(path))
    doc.close()
    return path, truth


class VisionAdapter(Counting):
    """Translator fake that also plays the vision model: `truth` per PDF page number, extras per page."""

    def __init__(self, truth, extra=None, conf=0.93, fail_pages=(), **kw):
        super().__init__(**kw)
        self.truth, self.extra, self.conf, self.fail = truth, extra or {}, conf, set(fail_pages)
        self.vision_requests = []

    def complete(self, req, model_id):
        if req.model_alias != "vision":
            return super().complete(req, model_id)
        self.vision_requests.append(req)
        page = int(req.target_id.split(":")[0][1:])
        if page in self.fail:
            raise ValueError("model exploded")
        text = scripted_answer(req.target_id, [(t, r) for t, r in self.truth.get(page, [])], self.extra.get(page, ()),
                               self.conf, kind="paragraph" if page == 4 else "label")
        return {"text": text, "tokens_in": 10, "tokens_out": 10}


def go(manual, out, adapter, pages=10, **kw):
    return run_pilot(manual, "43", pages, out, profile="dev", adapters={"fake": adapter}, **kw)


def by(res):
    return {o.label: o for o in res.outcomes}


def vi_text(out, i):
    with pymupdf.open(out / "section43-vi.pdf") as d:
        return d[i].get_text()


def test_image_only_page_is_read_translated_and_rendered(raster_manual, tmp_path):
    manual, truth = raster_manual
    out = tmp_path / "o"
    ad = VisionAdapter(truth)
    res = go(manual, out, ad)
    o = by(res)["43-4"]
    assert o.status == "ok" and o.vision == "page" and o.vision_blocks >= 2 and o.segments >= 1
    t = vi_text(out, 3)
    assert "VI: " in t and "fuse box" in t.lower()             # read by the VLM, translated, drawn in place
    assert "YT1234" in t                                         # protected code untouched
    assert "needs OCR" not in write_report(res, out / "r.md").read_text("utf-8")


def test_raster_label_in_a_text_page_reads_only_that_region(raster_manual, tmp_path):
    manual, truth = raster_manual
    out = tmp_path / "o"
    ad = VisionAdapter(truth)
    res = go(manual, out, ad)
    o = by(res)
    assert o["43-2"].vision == "regions" and o["43-2"].vision_calls == 1
    assert o["43-1"].vision == ""                                 # text-only pages never reach the model
    reqs = [r for r in ad.vision_requests if r.target_id.startswith("p2:")]   # pdf page 2 = 43-2
    assert len(reqs) == 1
    x0, y0, x1, y1 = (float(v) for v in reqs[0].target_id.split(":")[1].split(","))
    assert (x1 - x0) < 400 and (y1 - y0) < 200                    # a crop around the label, not the page
    t = vi_text(out, 1)
    assert "CAB WORK LIGHT" in t or "VI: CAB WORK LIGHT" in t     # kept-as-printed caps label, drawn in place


def test_rotated_page_label_is_placed(raster_manual, tmp_path):
    manual, truth = raster_manual
    res = go(manual, tmp_path / "o", VisionAdapter(truth))
    o = by(res)["43-3"]
    assert o.status == "ok" and o.vision == "regions" and o.vision_blocks == 1
    with pymupdf.open(tmp_path / "o" / "section43-vi.pdf") as d:
        assert d[2].search_for("WIPER MOTOR") or d[2].search_for("VI: WIPER MOTOR")


def test_low_confidence_blocks_are_marked_and_reported_never_silent(raster_manual, tmp_path):
    manual, truth = raster_manual
    out = tmp_path / "o"
    res = go(manual, out, VisionAdapter(truth, conf=0.2))
    o = by(res)["43-4"]
    assert o.vision_flagged and all("low_confidence" in f["reasons"] for f in o.vision_flagged)
    assert len(o.review) >= 2 and all("vision:low_confidence" in r["rules"] for r in o.review)
    assert "[!]" in vi_text(out, 3)
    rep = write_report(res, out / "r.md").read_text("utf-8")
    assert "low_confidence" in rep and "read by the vision model and flagged" in rep
    assert "fuse box" not in rep.lower()                         # no manual text in the report
    assert "vision blocks flagged" in res.summary_line()


def test_foreign_script_block_is_reported_but_not_translated_or_drawn(raster_manual, tmp_path):
    manual, truth = raster_manual
    out = tmp_path / "o"
    bad = {"text": "油圧ポンプ", "bbox": [0.1, 0.9, 0.5, 0.95], "kind": "label", "confidence": 0.99}
    ad = VisionAdapter(truth, extra={4: [bad]})
    res = go(manual, out, ad)
    o = by(res)["43-4"]
    assert any(not f["shown"] and "script" in f["reasons"] for f in o.vision_flagged)
    assert "油圧" not in vi_text(out, 3)
    assert not any("油圧" in t for t in ad.texts)                 # never sent to the translator
    assert "not translated, not drawn" in write_report(res, out / "r.md").read_text("utf-8")


def test_resume_makes_no_repeated_vision_or_translation_calls(raster_manual, tmp_path):
    manual, truth = raster_manual
    out = tmp_path / "o"
    first = VisionAdapter(truth)
    go(manual, out, first)
    assert len(first.vision_requests) == 3 and first.texts
    second = VisionAdapter(truth)
    res = go(manual, out, second)
    assert second.vision_requests == [] and second.texts == [] and res.succeeded == 4


def test_vision_failure_on_an_image_only_page_is_isolated(raster_manual, tmp_path, capsys):
    manual, truth = raster_manual
    out = tmp_path / "o"
    res = go(manual, out, VisionAdapter(truth, fail_pages=[4]))
    o = by(res)
    assert o["43-4"].status == "failed" and o["43-4"].error == "VisionReadFailed"
    assert o["43-1"].status == "ok" and o["43-2"].status == "ok"
    assert "LỖI" in vi_text(out, 3)
    assert "secret" not in capsys.readouterr().err
    rep = write_report(res, out / "r.md").read_text("utf-8")
    assert "vision model could not read it" in rep


def test_vision_failure_in_a_region_keeps_the_text_layer_and_flags_the_page(raster_manual, tmp_path):
    manual, truth = raster_manual
    res = go(manual, tmp_path / "o", VisionAdapter(truth, fail_pages=[2]))
    o = by(res)["43-2"]
    assert o.status == "ok" and "vision_incomplete" in o.flags and "ValueError" in o.vision_note
    assert o.segments >= 1                                         # the prose still got translated


def test_job_call_cap_leaves_pages_flagged_not_silently_unread(raster_manual, tmp_path):
    manual, truth = raster_manual
    res = go(manual, tmp_path / "o", VisionAdapter(truth), vision_cfg=VisionConfig(max_calls_per_job=1))
    o = by(res)
    reads = [x for x in o.values() if x.vision]
    assert sum(x.vision_calls for x in reads) == 1
    capped = [x for x in reads if "budget_calls" in x.vision_note]
    assert capped and all("vision_incomplete" in x.flags or x.status == "failed" for x in capped)


def test_vision_model_must_be_local(raster_manual, tmp_path):
    from src.pilot import PilotError
    manual, truth = raster_manual
    with pytest.raises(PilotError):
        go(manual, tmp_path / "o", VisionAdapter(truth), vision_model="bedrock:some-model")
