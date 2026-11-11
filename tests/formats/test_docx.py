import zipfile
from pathlib import Path

import pytest
from docx import Document

from src.formats import get_adapter
from src.formats.ooxml import VI_LANG
from src.formats.tags import tag_names

from .fixtures import make_docx

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@pytest.fixture
def doc(tmp_path):
    return make_docx(tmp_path / "in.docx")


def fingerprint(path: Path):
    """Structure seen through python-docx: paragraph styles, merged run formatting, table shape, header/footer."""
    d = Document(str(path))

    def para(p):
        runs = []
        for r in p.runs:
            fmt = (bool(r.bold), bool(r.italic))
            if runs and runs[-1][0] == fmt:
                runs[-1][1] += r.text
            else:
                runs.append([fmt, r.text])
        return (p.style.name, [f for f, _ in runs])

    body = [para(p) for p in d.paragraphs]
    tables = [[[len(c.paragraphs) for c in row.cells] for row in t.rows] for t in d.tables]
    sec = d.sections[0]
    return body, tables, [para(p) for p in sec.header.paragraphs], [para(p) for p in sec.footer.paragraphs]


def test_identity_round_trip_keeps_structure(doc, tmp_path):
    a = get_adapter("docx")
    segs = a.extract(doc)
    out = tmp_path / "out.docx"
    a.rebuild(doc, {s.id: s.text for s in segs}, out, force=True)   # force: every paragraph rebuilt from tagged text
    assert fingerprint(out) == fingerprint(doc)
    # paragraph count, table shape (merged cell) and text identical
    d0, d1 = Document(str(doc)), Document(str(out))
    assert [p.text for p in d0.paragraphs] == [p.text for p in d1.paragraphs]
    assert len(d0.tables[0].rows) == len(d1.tables[0].rows) == 2
    assert d1.tables[0].rows[0].cells[0]._tc.grid_span == 2
    # parts that were not translated are byte-identical
    with zipfile.ZipFile(doc) as z0, zipfile.ZipFile(out) as z1:
        assert z0.namelist() == z1.namelist()
        for n in ("word/_rels/document.xml.rels", "[Content_Types].xml", "_rels/.rels"):
            assert z0.read(n) == z1.read(n)


def test_untranslated_file_is_untouched(doc, tmp_path):
    a = get_adapter("docx")
    out = tmp_path / "out.docx"
    rep = a.rebuild(doc, {}, out)
    assert rep.segments_applied == 0
    with zipfile.ZipFile(doc) as z0, zipfile.ZipFile(out) as z1:
        assert all(z0.read(n) == z1.read(n) for n in z0.namelist())


def seg_by_text(segs, needle):
    return next(s for s in segs if needle in s.text)


def test_inline_formatting_survives_reordering(doc, tmp_path):
    a = get_adapter("docx")
    segs = a.extract(doc)
    s = seg_by_text(segs, "battery")
    names = tag_names(s.text)
    assert names[:2] == ["r1", "/r1"] and len(names) == 10
    # Vietnamese order: service first, then battery; the part number stays outside any tag
    tr = "<r4>Bảo dưỡng</r4><r1> trước: ngắt </r1><r2>ắc quy</r2><r3> </r3><r5> theo chi tiết YN20-01234-A.</r5>"
    out = tmp_path / "o.docx"
    a.rebuild(doc, {s.id: tr}, out)
    d = Document(str(out))
    p = next(p for p in d.paragraphs if "ắc quy" in p.text)
    fm = {r.text: (bool(r.bold), bool(r.italic)) for r in p.runs}
    assert fm["ắc quy"] == (True, False) and fm["Bảo dưỡng"] == (False, True)
    assert "YN20-01234-A" in p.text


def test_hyperlink_field_tracked_changes_footnote_and_textbox_preserved(doc, tmp_path):
    a = get_adapter("docx")
    segs = a.extract(doc)
    tr = {s.id: "Đã dịch: " + s.text for s in segs}
    out = tmp_path / "o.docx"
    rep = a.rebuild(doc, tr, out)
    assert rep.segments_applied == len(segs)
    with zipfile.ZipFile(out) as z:
        xml = z.read("word/document.xml").decode()
        assert 'r:id="rId5"' in xml and "<w:hyperlink" in xml
        assert "<w:instrText" in xml and "PAGE" in xml and xml.count("w:fldCharType") == 3
        assert "<w:ins " in xml and "deleted words" in xml and "<w:delText" in xml     # markup kept, deletion untouched
        assert "<w:footnoteReference" in xml and "<w:tab/>" in xml and "<w:br/>" in xml
        assert "Đã dịch: Text inside a box" in xml and "gridSpan" in xml
        assert "Đã dịch: Service manual header" in z.read("word/header1.xml").decode()
        assert "Đã dịch: Footer text" in z.read("word/footer1.xml").decode()
        fn = z.read("word/footnotes.xml").decode()
        assert "Đã dịch" in fn and "<w:separator/>" in fn and "<w:footnoteRef/>" in fn


def test_language_and_font_policy(doc, tmp_path):
    a = get_adapter("docx")
    segs = a.extract(doc)
    out = tmp_path / "o.docx"
    rep = a.rebuild(doc, {s.id: "Đã dịch " + s.text for s in segs}, out)
    with zipfile.ZipFile(out) as z:
        styles = z.read("word/styles.xml").decode()
        xml = z.read("word/document.xml").decode()
    assert f'w:val="{VI_LANG}"' in styles and 'w:val="en-US"' not in styles
    assert f'<w:lang w:val="{VI_LANG}"/>' in xml
    assert "Bebas Neue" not in xml and 'w:ascii="Arial"' in xml          # display font lacked Vietnamese -> Arial
    assert "font_substitutions=1" in rep.notes


def test_broken_tags_keep_paragraph(doc, tmp_path):
    a = get_adapter("docx")
    segs = a.extract(doc)
    s = seg_by_text(segs, "battery")
    out = tmp_path / "o.docx"
    rep = a.rebuild(doc, {s.id: "Ngắt ắc quy trước khi bảo dưỡng."}, out)
    assert s.id in rep.needs_review and rep.segments_applied == 0
    assert any("battery" in p.text for p in Document(str(out)).paragraphs)


def test_extract_kinds_and_parts(doc):
    segs = get_adapter("docx").extract(doc)
    kinds = {s.text: s.kind for s in segs}
    assert kinds["Remove the relay box"] == "heading" and kinds["First list item"] == "list_item"
    assert kinds["Left cell text"] == "table_cell" and kinds["Text inside a box"] == "paragraph"
    assert {s.section for s in segs} == {"document", "header1", "footer1", "footnotes"}
    assert all(s.context in ("", "Remove the relay box") for s in segs)
    assert not any("deleted words" in s.text or "PAGE" in s.text for s in segs)
