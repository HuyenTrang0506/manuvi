import pymupdf
import pytest

from src.render.fonts import find_vi_font
from src.render.interleave import interleave
from src.render.page import Segment, render_document
from src.render.qa_report import build_report, to_json
from src.render.synth import synth_vi

FONT = find_vi_font()
pytestmark = pytest.mark.skipif(FONT is None, reason="no Vietnamese font available")

PROSE = "Remove bolt YT12-AB3C and torque to 45 N·m as shown in Fig. 23-11 before the next step."


def make_pdf(path, n=2):
    doc = pymupdf.open()
    for i in range(n):
        p = doc.new_page(width=300, height=300)
        p.insert_text((40, 60), f"Page {i + 1} marker", fontsize=9)
    doc.save(path)
    return path


def seg(page, bbox, kind, src, tgt=None, **kw):
    return Segment(page, bbox, kind, src, synth_vi(src) if tgt is None else tgt, **kw)


def render(tmp_path, segs, n=2):
    src = make_pdf(str(tmp_path / "s.pdf"), n)
    out = str(tmp_path / "o.pdf")
    res = render_document(src, segs, out, fontfile=str(FONT))
    return src, out, res


def test_prose_ok_and_vector(tmp_path):
    s = seg(0, (40, 100, 260, 160), "prose", PROSE)
    _, out, res = render(tmp_path, [s])
    d = pymupdf.open(out)
    assert not d[0].get_images()
    assert "YT12-AB3C" in d[0].get_text()
    rep = build_report(out, [s], res)
    assert rep["pages"][0]["status"] in ("ok", "warn") and "overflow" not in rep["pages"][0]["errors"]


def test_overflow_flagged_and_text_complete(tmp_path):
    long = " ".join(["Kiểm tra bộ điều khiển động cơ"] * 30)
    s = Segment(0, (40, 270, 120, 280), "prose", "x", long)
    _, out, res = render(tmp_path, [s])
    assert res[0].segs[0].overflow
    rep = build_report(out, [s], res)
    assert rep["pages"][0]["status"] == "error" and "overflow" in rep["pages"][0]["errors"]
    words = pymupdf.open(out)[0].get_text().split()
    assert words.count("điều") == 30  # nothing truncated


def test_extension_into_free_space_only_if_empty(tmp_path):
    txt = "Kiểm tra bộ điều khiển động cơ được lắp " * 6
    free = Segment(0, (40, 100, 200, 112), "prose", "x", txt)
    _, _, res = render(tmp_path, [free])
    assert res[0].segs[0].stage in ("extended", "hard_floor") and res[0].segs[0].extension > 0
    blocked = Segment(0, (40, 100, 200, 112), "prose", "x", txt)
    other = Segment(0, (40, 114, 200, 126), "prose", "y", "ok")
    _, _, res = render(tmp_path, [blocked, other])
    assert res[0].segs[0].extension == 0


def test_label_overlay_vs_legend_keeps_fig(tmp_path):
    caption = seg(0, (40, 30, 260, 50), "prose", "Fig. 23-11 Engine controller")
    small = seg(0, (40, 200, 140, 212), "label", "Pump", "Bơm")
    tiny = seg(0, (200, 200, 218, 208), "label", "Engine controller unit", "Bộ điều khiển động cơ")
    src, out, res = render(tmp_path, [caption, small, tiny])
    r0 = res[0]
    assert r0.segs[1].stage == "overlay"
    assert r0.segs[2].stage == "legend" and r0.segs[2].legend_n == 1
    assert r0.legend and r0.fig
    text = pymupdf.open(out)[0].get_text().replace("\xa0", " ")
    assert "[1]" in text and "Chú giải Fig. 23-11" in text and "Bộ điều khiển động cơ" in text
    rep = build_report(out, [caption, small, tiny], res)
    assert "legend_fallback" in rep["pages"][0]["warnings"]
    assert rep["pages"][0]["status"] != "error"


def test_legend_grows_mediabox_when_page_full(tmp_path):
    segs = [seg(0, (40, 280, 50, 288), "label", f"Part {i}", f"Chi tiết số {i} của bộ điều khiển động cơ") for i in range(6)]
    src, out, res = render(tmp_path, segs)
    assert res[0].grown > 0
    assert pymupdf.open(out)[0].rect.height > 300


def test_table_numbers_byte_identical(tmp_path):
    ok = seg(0, (40, 100, 120, 120), "table", "Tighten 45 N·m", "Siết 45 N·m")
    _, out, res = render(tmp_path, [ok])
    assert res[0].segs[0].numbers_missing == [] and res[0].segs[0].numbers_extra == []
    assert build_report(out, [ok], res)["pages"][0]["status"] != "error"
    bad = Segment(0, (40, 100, 120, 120), "table", "Range 12.5 total", "Phạm vi 12,5 tổng")
    _, out, res = render(tmp_path, [bad])
    rep = build_report(out, [bad], res)
    assert rep["pages"][0]["status"] == "error" and "table_number_violation" in rep["pages"][0]["errors"]


def test_qa_detects_injected_protected_token_violation(tmp_path):
    bad = Segment(0, (40, 100, 260, 160), "prose", PROSE, PROSE.replace("YT12-AB3C", "YT12-AB3D"))
    _, out, res = render(tmp_path, [bad])
    rep = build_report(out, [bad], res)
    p = rep["pages"][0]
    assert p["status"] == "error" and "protected_token_violation" in p["errors"]
    assert "YT12-AB3C" in p["missing_tokens"]
    assert '"error": 1' in to_json(rep)
    assert "missing_tokens" not in build_report(out, [bad], res, include_tokens=False)["pages"][0]


def test_interleave_order(tmp_path):
    s = seg(1, (40, 100, 260, 160), "prose", PROSE)
    src, out, res = render(tmp_path, [s], n=3)
    both = str(tmp_path / "i.pdf")
    assert interleave(src, out, both) == 6
    d = pymupdf.open(both)
    texts = [p.get_text() for p in d]
    assert "Page 1 marker" in texts[0] and "Page 1 marker" in texts[1]
    assert "Page 2 marker" in texts[2] and "YT12-AB3C" in texts[3] and "Fig." in texts[3]
    assert "YT12-AB3C" not in texts[2]
    assert "Page 3 marker" in texts[4]


def test_subset_pages_interleave(tmp_path):
    src = make_pdf(str(tmp_path / "s.pdf"), 4)
    out = str(tmp_path / "o.pdf")
    render_document(src, [], out, fontfile=str(FONT), pages=[2, 3])
    both = str(tmp_path / "i.pdf")
    assert interleave(src, out, both, [2, 3]) == 4
    assert "Page 3 marker" in pymupdf.open(both)[0].get_text()


def test_synth_keeps_tokens_and_lengthens():
    t = synth_vi(PROSE)
    assert "YT12-AB3C" in t and "45 N·m" in t and "Fig. 23-11" in t
    assert 1.15 < len(t) / len(PROSE) < 1.5
