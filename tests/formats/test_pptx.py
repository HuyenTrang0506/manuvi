import zipfile
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Pt

from src.formats import get_adapter

from .fixtures import make_pptx


@pytest.fixture
def deck(tmp_path):
    return make_pptx(tmp_path / "in.pptx")


def fingerprint(path: Path):
    prs = Presentation(str(path))
    out = []
    for s in prs.slides:
        shapes = []
        for sh in s.shapes:
            item = [sh.shape_type, sh.name, sh.left, sh.top, sh.width, sh.height]
            if sh.has_text_frame:
                item.append([[(r.font.bold, r.font.italic, r.font.size) for r in p.runs] for p in sh.text_frame.paragraphs])
            if sh.has_table:
                item.append([[c.text for c in row.cells] for row in sh.table.rows])
            shapes.append(item)
        out.append((s.slide_layout.name, shapes, s.has_notes_slide))
    return out


def test_identity_round_trip_keeps_structure(deck, tmp_path):
    a = get_adapter("pptx")
    segs = a.extract(deck)
    out = tmp_path / "o.pptx"
    a.rebuild(deck, {s.id: s.text for s in segs}, out, force=True)
    assert fingerprint(out) == fingerprint(deck)
    a1, a2 = Presentation(str(deck)), Presentation(str(out))
    assert a1.slides[0].notes_slide.notes_text_frame.text == a2.slides[0].notes_slide.notes_text_frame.text


def test_all_text_kinds_are_extracted(deck):
    segs = get_adapter("pptx").extract(deck)
    by = {s.text: s for s in segs}
    assert by["Relay box service"].kind == "title" and by["Relay box service"].page == 1
    assert by["Grouped shape label"].kind == "shape"                       # inside a group
    assert by["Speaker note: explain the relay."].kind == "note"
    assert by["Relay for the starter circuit"].kind == "table_cell"
    assert by["Second slide"].page == 2 and by["Bullet one is here"].context == "Second slide"


def test_run_formatting_and_notes_translated(deck, tmp_path):
    a = get_adapter("pptx")
    segs = a.extract(deck)
    s = next(x for x in segs if "battery" in x.text)
    tr = {x.id: "Đã dịch: " + x.text for x in segs}
    tr[s.id] = "<r1>Ngắt </r1><r2>ắc quy</r2><r3> trước, rồi </r3><r4>chờ</r4><r5> hai phút.</r5>"
    out = tmp_path / "o.pptx"
    a.rebuild(deck, tr, out)
    prs = Presentation(str(out))
    runs = [r for sh in prs.slides[0].shapes if sh.has_text_frame for p in sh.text_frame.paragraphs for r in p.runs
            if r.text in ("ắc quy", "chờ")]
    fm = {r.text: (r.font.bold, r.font.italic, r.font.size) for r in runs}
    assert fm["ắc quy"] == (True, None, Pt(18)) or fm["ắc quy"][0] is True
    assert fm["chờ"][1] is True
    assert prs.slides[0].notes_slide.notes_text_frame.text.startswith("Đã dịch: Speaker note")
    with zipfile.ZipFile(out) as z:
        assert 'lang="vi-VN"' in z.read("ppt/slides/slide1.xml").decode()


def test_overflow_shrinks_then_flags(deck, tmp_path):
    a = get_adapter("pptx")
    segs = a.extract(deck)
    small = next(x for x in segs if x.text.startswith("Remove the cover"))
    out = tmp_path / "o.pptx"
    # the shape (1.2in x 0.4in, 18pt) already overflows in English; the Vietnamese is far longer -> floor reached
    rep = a.rebuild(deck, {small.id: "Tháo nắp che và kiểm tra bó dây điện " * 4}, out)
    assert small.id in rep.needs_review and rep.needs_review[small.id].startswith("OVERFLOW")
    with zipfile.ZipFile(out) as z:
        assert 'fontScale="88000"' in z.read("ppt/slides/slide1.xml").decode()


def test_mild_overflow_is_fixed_by_step_down(tmp_path):
    from pptx.enum.text import MSO_AUTO_SIZE
    from pptx.util import Inches
    prs = Presentation()
    s = prs.slides.add_slide(prs.slide_layouts[6])
    tb = s.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(0.5))
    tb.text_frame.word_wrap = True
    tb.text_frame.auto_size = MSO_AUTO_SIZE.NONE
    tb.text_frame.text = "Short text here ok"
    tb.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    p = tmp_path / "m.pptx"
    prs.save(p)
    a = get_adapter("pptx")
    (seg,) = a.extract(p)
    out = tmp_path / "o.pptx"
    # 3in wide, 18pt: about 24 chars per line; 0.5in high holds one line (+ insets). 25..26 chars at 96% fit on one line.
    rep = a.rebuild(p, {seg.id: "Văn bản ngắn ở đây nè!!"}, out)
    assert not rep.needs_review and "shrunk shape to 96%" in rep.notes
    with zipfile.ZipFile(out) as z:
        assert 'fontScale="96000"' in z.read("ppt/slides/slide1.xml").decode()
    rep = a.rebuild(p, {seg.id: "Văn bản ngắn ở đây khá dài nhé các bạn"}, out)
    assert rep.needs_review[seg.id].startswith("OVERFLOW")
    rep = a.rebuild(p, {seg.id: "Văn bản"}, out)           # fits: untouched
    assert not rep.needs_review and not rep.notes


def test_untranslated_deck_untouched(deck, tmp_path):
    out = tmp_path / "o.pptx"
    get_adapter("pptx").rebuild(deck, {}, out)
    with zipfile.ZipFile(deck) as z0, zipfile.ZipFile(out) as z1:
        assert all(z0.read(n) == z1.read(n) for n in z0.namelist())
