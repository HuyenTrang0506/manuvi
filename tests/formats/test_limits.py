import zipfile

import pytest

from src.formats import FormatError, detect_format
from src.formats.docx import DocxAdapter

from .fixtures import CT_DOCX, make_docx, make_pptx


def code(fn):
    with pytest.raises(FormatError) as e:
        fn()
    return e.value.code


def test_valid_files_detected(tmp_path):
    assert detect_format(make_docx(tmp_path / "a.docx")) == "docx"
    assert detect_format(make_pptx(tmp_path / "a.pptx")) == "pptx"
    (tmp_path / "a.md").write_text("# hi\n")
    assert detect_format(tmp_path / "a.md") == "md"
    (tmp_path / "b.markdown").write_text("# hi\n")
    assert detect_format(tmp_path / "b.markdown") == "md"


def test_oversized_file_rejected(tmp_path):
    p = make_docx(tmp_path / "a.docx")
    assert code(lambda: detect_format(p, {"MANUVI_MAX_FILE_MB": "0.0001"})) == "FILE_TOO_LARGE"
    m = tmp_path / "big.md"
    m.write_text("x" * 2_000_000)
    assert code(lambda: detect_format(m, {"MANUVI_MAX_MD_MB": "1"})) == "FILE_TOO_LARGE"


def test_zip_bomb_rejected(tmp_path):
    bomb = make_docx(tmp_path / "bomb.docx", extra_entries={"word/media/zeros.bin": b"\0" * 20_000_000})
    assert code(lambda: detect_format(bomb)) == "ZIP_BOMB"                       # ratio far above the limit
    big = make_docx(tmp_path / "big.docx", extra_entries={"word/media/a.bin": b"\0" * 3_000_000})
    assert code(lambda: detect_format(big, {"MANUVI_MAX_UNZIPPED_MB": "2", "MANUVI_MAX_ZIP_RATIO": "100000"})) == "ZIP_BOMB"
    many = make_docx(tmp_path / "many.docx", extra_entries={f"word/media/{i}.txt": b"x" for i in range(60)})
    assert code(lambda: detect_format(many, {"MANUVI_MAX_ZIP_ENTRIES": "50"})) == "ZIP_BOMB"


def test_macros_rejected(tmp_path):
    p = make_docx(tmp_path / "a.docx")
    assert code(lambda: detect_format(p.rename(tmp_path / "a.docm"))) == "MACRO_REJECTED"
    (tmp_path / "x.pptm").write_bytes(b"PK\x03\x04")
    assert code(lambda: detect_format(tmp_path / "x.pptm")) == "MACRO_REJECTED"
    vba = make_docx(tmp_path / "vba.docx", extra_entries={"word/vbaProject.bin": b"\xd0\xcf\x11\xe0 macro"})
    assert code(lambda: detect_format(vba)) == "MACRO_REJECTED"
    ct = CT_DOCX.replace("wordprocessingml.document.main+xml", "wordprocessingml.document.macroEnabled.main+xml")
    assert code(lambda: detect_format(make_docx(tmp_path / "ct.docx", content_types=ct))) == "MACRO_REJECTED"
    (tmp_path / "old.docx").write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 100)
    assert code(lambda: detect_format(tmp_path / "old.docx")) == "MACRO_REJECTED"


def test_bad_inputs_rejected(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    assert code(lambda: detect_format(tmp_path / "a.txt")) == "UNSUPPORTED_FORMAT"
    (tmp_path / "fake.docx").write_text("not a zip")
    assert code(lambda: detect_format(tmp_path / "fake.docx")) == "BAD_FORMAT"
    pk = tmp_path / "pk.pptx"
    with zipfile.ZipFile(pk, "w") as z:
        z.writestr("hello.txt", "x")
    assert code(lambda: detect_format(pk)) == "BAD_FORMAT"
    swapped = make_pptx(tmp_path / "s.pptx").rename(tmp_path / "s.docx")        # extension lies about the content
    assert code(lambda: detect_format(swapped)) == "BAD_FORMAT"
    (tmp_path / "bin.md").write_bytes(b"PK\x03\x04binary")
    assert code(lambda: detect_format(tmp_path / "bin.md")) == "BAD_FORMAT"
    (tmp_path / "nul.md").write_bytes(b"abc\0def")
    assert code(lambda: detect_format(tmp_path / "nul.md")) == "BAD_FORMAT"
    (tmp_path / "latin.md").write_bytes("caf\xe9".encode("latin-1"))
    assert code(lambda: detect_format(tmp_path / "latin.md")) == "BAD_FORMAT"
    slip = tmp_path / "slip.docx"
    make_docx(slip, extra_entries={"../evil.txt": b"x"})
    assert code(lambda: detect_format(slip)) == "BAD_FORMAT"


def test_entities_are_not_resolved(tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP-SECRET-CONTENT")
    evil = (f'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x SYSTEM "file://{secret}">]>'
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
            '<w:p><w:r><w:t>Hello &x; world</w:t></w:r></w:p></w:body></w:document>')
    p = make_docx(tmp_path / "xxe.docx", extra_entries={"word/document.xml": evil})
    segs = DocxAdapter().extract(p)
    assert all("TOP-SECRET" not in s.text for s in segs)
