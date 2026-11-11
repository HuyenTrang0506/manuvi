"""Faithful renderer on synthetic PDFs: font, size, colour, alignment, rotation, line art under text, no overlap."""
from __future__ import annotations

import pymupdf
import pytest

from src.pilot.extract import page_units
from src.render.fidelity import expand_vi, simulate_page, summarize
from src.render.fit import fit_faithful
from src.render.fonts import FontSet, classify_font, has_cjk
from src.render.page import Segment, render_document

EN = "Check the voltage between the connector pin and the machine body before starting the engine"


def make_pdf(path, build, rotate=0):
    d = pymupdf.open()
    p = d.new_page(width=420, height=300)
    build(p)
    if rotate:
        p.set_rotation(rotate)
    d.save(str(path))
    d.close()
    return path


def render(src, engine="faithful", expand=True):
    with pymupdf.open(str(src)) as d:
        units, _ = page_units(d[0])
    segs = [Segment(0, u.bbox, u.kind, u.text, expand_vi(u.text) if expand else u.text, rotate=u.rotate,
                    max_size=u.max_size, style=u.style if engine == "faithful" else None) for u in units]
    out = src.with_name(src.stem + "-vi.pdf")
    res = render_document(src, segs, out, fontfile=FontSet(), pages=[0])
    return units, segs, res[0], out


def spans(pdf):
    with pymupdf.open(str(pdf)) as d:
        return [s for b in d[0].get_text("dict")["blocks"] if b.get("type") == 0 for ln in b["lines"] for s in ln["spans"]
                if s["text"].strip()]


def test_font_choice():
    f = FontSet()
    assert "Sans-Regular" in f.face("ABCDEF+ArialMT").path
    assert "Sans-Bold" in f.face("Arial-BoldMT").path and classify_font("Arial-BoldMT")[1]
    assert "Sans-Italic" in f.face("Arial-ItalicMT").path
    assert "Sans-Regular" in f.face("MSGothic", 0, "Kiểm tra").path      # Vietnamese text: Arial-like, no CJK font
    assert "Sans-Regular" in f.face("Helvetica").path and "Sans-Regular" in f.face("SymbolMT").path
    assert not has_cjk("Kiểm tra điện áp") and has_cjk("電圧")


def test_expand_is_deterministic_and_keeps_codes():
    t = "Connect YT12-A3 to pin 5 and 24 V now with ECU"
    a, b = expand_vi(t), expand_vi(t)
    assert a == b and "YT12-A3" in a and "24 V" in a and "ECU" in a and " 5 " in a
    long = expand_vi(EN)
    assert 1.1 <= len(long) / len(EN) <= 1.45
    assert expand_vi(EN) != EN


def test_style_survives(tmp_path):
    def b(p):
        p.insert_text((40, 60), "Safety warning text here", fontsize=12, fontname="hebo", color=(0.8, 0, 0))
        p.insert_text((40, 120), "Plain body text in regular", fontsize=9, fontname="helv", color=(0, 0, 1))
    units, segs, res, out = render(make_pdf(tmp_path / "a.pdf", b), expand=False)
    sp = {round(s["size"], 1): s for s in spans(out)}
    assert any("Bold" in s["font"] for s in sp.values())
    bold = [s for s in spans(out) if "Bold" in s["font"]][0]
    assert (bold["color"] >> 16) & 255 > 180 and bold["color"] & 255 < 40
    reg = [s for s in spans(out) if "Bold" not in s["font"]][0]
    assert reg["color"] & 255 > 180 and (reg["color"] >> 16) & 255 < 40
    assert all(r.ratio >= 0.88 or r.stage == "needs_review" for r in res.segs)
    short = [r for r in res.segs if r.size0 == 12][0]
    assert short.size == 12 and short.stage == "ok"   # fits: original size kept


def test_text_fits_keeps_size_and_position(tmp_path):
    def b(p):
        p.insert_text((50, 80), "Short label", fontsize=10, fontname="helv")
    units, segs, res, out = render(make_pdf(tmp_path / "a.pdf", b), expand=False)
    r = res.segs[0]
    assert r.size == r.size0 == 10 and r.drift < 0.6 and r.lines == r.lines0 == 1
    s = spans(out)[0]
    assert abs(s["bbox"][0] - units[0].bbox[0]) < 1.0 and abs(s["origin"][1] - 80.0) < 0.6   # same baseline


def test_line_art_under_text_survives_redaction(tmp_path):
    def b(p):
        p.draw_rect(pymupdf.Rect(30, 40, 300, 130), fill=(0.85, 0.92, 1), color=None)        # shading under text
        p.draw_line((30, 66), (300, 66), color=(1, 0, 0), width=1.5)                            # rule under text
        p.draw_line((60, 40), (60, 130), color=(0, 0.5, 0), width=1.5)                          # vertical line through text
        p.insert_text((40, 62), "Engine speed sensor harness", fontsize=10, fontname="helv")
        p.insert_text((40, 100), "Neighbour line stays intact", fontsize=10, fontname="helv")
    src = make_pdf(tmp_path / "a.pdf", b)
    with pymupdf.open(str(src)) as d:
        before = len(d[0].get_drawings())
    d = pymupdf.open(str(src))
    units, _ = page_units(d[0])
    d.close()
    # translate only the first block: the second must stay byte-identical text
    segs = [Segment(0, u.bbox, u.kind, u.text, expand_vi(u.text), rotate=u.rotate, max_size=u.max_size, style=u.style)
            for u in units if u.text.startswith("Engine")]
    out = tmp_path / "o.pdf"
    render_document(src, segs, out, fontfile=FontSet(), pages=[0])
    with pymupdf.open(str(out)) as o:
        assert len(o[0].get_drawings()) == before
        text = o[0].get_text()
        assert "Neighbour line stays intact" in text and "Engine speed" not in text
        fills = [x for x in o[0].get_drawings() if x.get("fill")]
        assert fills and fills[0]["fill"][2] > 0.95


def test_rotated_text_keeps_direction(tmp_path):
    def b(p):
        p.insert_text((200, 250), "Vertical label text", fontsize=9, fontname="helv", rotate=90)
    src = make_pdf(tmp_path / "a.pdf", b)
    units, segs, res, out = render(src)
    assert units[0].rotate == 90
    with pymupdf.open(str(out)) as o:
        ln = [ln for bl in o[0].get_text("dict")["blocks"] if bl.get("type") == 0 for ln in bl["lines"]][0]
    assert abs(ln["dir"][0]) < 0.1 and ln["dir"][1] < -0.9


def test_rotated_page_runs(tmp_path):
    def b(p):
        p.insert_text((50, 80), "Text on a rotated page", fontsize=10, fontname="helv")
    src = make_pdf(tmp_path / "a.pdf", b, rotate=90)
    units, segs, res, out = render(src)
    assert res.segs and not res.segs[0].overflow
    assert "Text" not in pymupdf.open(str(out))[0].get_text()


def test_center_alignment_kept(tmp_path):
    def b(p):
        for i, t in enumerate(["Short title", "A somewhat longer centred line", "Mid line here"]):
            w = pymupdf.get_text_length(t, "helv", 10)
            p.insert_text((210 - w / 2, 60 + 12 * i), t, fontsize=10, fontname="helv")
    src = make_pdf(tmp_path / "a.pdf", b)
    with pymupdf.open(str(src)) as d:
        units, _ = page_units(d[0])
    assert units[0].style.align == "center"
    units, segs, res, out = render(src, expand=False)
    r = pymupdf.Rect(res.segs[0].placed)
    assert abs((r.x0 + r.x1) / 2 - 210) < 3


def test_no_new_overlaps_with_crowded_labels(tmp_path):
    def b(p):
        for i, t in enumerate(["Battery relay", "Fuse box", "Starter motor", "Glow plug"]):
            p.insert_text((20 + 95 * i, 100), t, fontsize=8, fontname="helv")
            p.insert_text((20 + 95 * i, 140), t + " lead", fontsize=8, fontname="helv")
    src = make_pdf(tmp_path / "a.pdf", b)
    r = simulate_page(src, 0, "faithful", FontSet(), tmp_path / "x.pdf")
    assert r["overlaps"] == 0
    s = summarize(r["blocks"])
    assert s["below90_pct"] >= 0 and s["blocks"] == 8
    # every drawn box stays on the page
    for bl in r["blocks"]:
        assert bl["ratio"] >= 0.88 - 1e-6 or bl["stage"] in ("legend", "needs_review")


def test_table_cells_and_grid_intact(tmp_path):
    def b(p):
        x = [30, 130, 250, 390]
        y = [50, 80, 110, 140]
        for xv in x:
            p.draw_line((xv, y[0]), (xv, y[-1]), width=0.8)
        for yv in y:
            p.draw_line((x[0], yv), (x[-1], yv), width=0.8)
        cells = [["Part name", "Check item", "Standard value"], ["Fuse", "Continuity between terminals", "Continuity"],
                 ["Relay", "Coil resistance", "about 80 ohm"]]
        for r, row in enumerate(cells):
            for c, t in enumerate(row):
                p.insert_text((x[c] + 4, y[r] + 18), t, fontsize=8, fontname="helv")
    src = make_pdf(tmp_path / "t.pdf", b)
    with pymupdf.open(str(src)) as d:
        before = len(d[0].get_drawings())
    units, segs, res, out = render(src)
    assert any(u.kind == "table" for u in units)
    with pymupdf.open(str(out)) as o:
        assert len(o[0].get_drawings()) == before
    assert not any(r.overflow for r in res.segs if r.kind == "table") or any(r.needs_review for r in res.segs)
    placed = [pymupdf.Rect(r.placed) for r in res.segs]
    for i in range(len(placed)):
        for j in range(i + 1, len(placed)):
            assert (placed[i] & placed[j]).get_area() <= 1.0


def test_cascade_order():
    f = pymupdf.Font(fontfile=FontSet().face().path)
    t = "Kiểm tra điện áp giữa các chân của đầu nối và thân máy"
    w = f.text_length(t, fontsize=10)
    ok = fit_faithful(t, f, w + 5, 12, 10, base_off=9.05)
    assert ok.stage == "ok" and ok.size == 10
    sh = fit_faithful(t, f, w * 0.95, 12, 10, base_off=9.05)
    assert sh.stage == "shrunk" and 8.8 <= sh.size < 10
    wd = fit_faithful(t, f, w * 0.7, 12, 10, base_off=9.05, free_end=w * 0.4)
    assert wd.stage == "widened" and wd.end_ext > 0 and wd.start_ext == 0
    cw = fit_faithful(t, f, w * 0.7, 12, 10, base_off=9.05, free_start=w, free_end=w, align="center")
    assert cw.stage == "widened" and abs(cw.start_ext - cw.end_ext) < 1e-6
    tight = fit_faithful(t, f, w * 0.55, 21.5, 10, base_off=9.05, pitch=11.5)
    assert tight.stage in ("tight", "ok", "shrunk") and tight.lh <= 1.15
    bad = fit_faithful(t, f, w * 0.3, 12, 10, base_off=9.05)
    assert bad.stage == "needs_review" and bad.overflow and bad.size == pytest.approx(8.8)
    assert "".join(bad.lines).replace(" ", "") == t.replace(" ", "")   # never truncated
