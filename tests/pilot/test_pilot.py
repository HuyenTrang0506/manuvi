import json

import pymupdf
import pytest
from openpyxl import load_workbook

import src.pilot.run as run_mod
from src.pilot import run_pilot
from src.pilot.report import write_form, write_report
from src.pilot.sample import SectionPage, choose_sample, classify_all, section_pages
from tests.pilot.conftest import Counting


def go(manual, out, adapter=None, pages=10, **kw):
    adapter = adapter or Counting()
    res = run_pilot(manual, "43", pages, out, profile="dev", adapters={"fake": adapter}, **kw)
    return res, adapter


def by_label(res):
    return {o.label: o for o in res.outcomes}


def test_labels_are_found_non_consecutively(manual):
    with pymupdf.open(manual) as d:
        pages, _ = section_pages(d, "43")
        assert [p.label for p in pages] == ["43-1", "43-2", "43-5", "43-6", "43-12", "43-13", "43-20", "43-21"]
        kinds = {p.label: p.kind for p in classify_all(d, pages)}
    assert kinds["43-20"] == "scanned" and kinds["43-5"] in ("table", "mixed") and kinds["43-12"] == "diagram"


def test_sampling_is_deterministic_and_covers_types(manual):
    with pymupdf.open(manual) as d:
        pages = classify_all(d, section_pages(d, "43")[0])
    a, b = choose_sample(pages, 6), choose_sample(list(reversed(pages)), 6)
    assert a == b == choose_sample(pages, 6)
    assert len(a) == 6 and [p.pdf_page for p in a] == sorted(p.pdf_page for p in a)
    kinds = {p.kind for p in a}
    assert {"text", "diagram"} <= kinds and kinds & {"table", "mixed"}
    assert sum(p.kind == "scanned" for p in choose_sample(pages, 8)) == 1


def test_sampling_small_pool():
    pool = [SectionPage(i, f"43-{i}", i, "text") for i in range(1, 4)]
    assert len(choose_sample(pool, 10)) == 3


def test_end_to_end_outputs(manual, tmp_path):
    out = tmp_path / "o"
    res, ad = go(manual, out)
    assert (out / "section43-vi.pdf").exists() and (out / "section43-interleaved-review.pdf").exists()
    with pymupdf.open(out / "section43-vi.pdf") as vi, pymupdf.open(out / "section43-interleaved-review.pdf") as il:
        assert len(vi) == 8 and len(il) == 16
        assert "43-5" in vi[2].get_text()                    # printed page label kept in place
    assert res.summary_line().startswith("SUMMARY: 7 of 8 pages succeeded (1 need OCR, 0 failed)")
    o = by_label(res)
    assert o["43-20"].status == "needs_ocr" and o["43-12"].status == "ok" and o["43-12"].segments > 0
    # report and form
    rep = write_report(res, out / "pilot-report.md").read_text("utf-8")
    assert "needs OCR (skipped)" in rep and "Leftover English" in rep and "| 43-12 |" in rep
    assert "Fuse box" not in rep and "hydraulic" not in rep         # no manual text in the report
    wb = load_workbook(write_form(res, out / "review-form.xlsx"))
    ws = wb["review"]
    assert [c.value for c in ws[1]] == ["Page", "PDF page", "Type", "Segments to check", "Verdict", "Comment"]
    assert ws.max_row == 9 and ws["A2"].value == "43-1"
    assert "usable with fixes" in ws.data_validations.dataValidation[0].formula1


def test_resume_makes_no_repeated_model_calls(manual, tmp_path):
    out = tmp_path / "o"
    _, first = go(manual, out)
    assert len(first.texts) > 0
    res, second = go(manual, out)
    assert second.texts == []
    assert res.succeeded == 7


def test_image_only_page_is_skipped_not_called(manual, tmp_path):
    res, ad = go(manual, tmp_path / "o", pages=8)
    o = by_label(res)["43-20"]
    assert o.status == "needs_ocr" and o.segments == 0
    with pymupdf.open(tmp_path / "o" / "section43-vi.pdf") as vi:
        assert "CẦN OCR" in vi[6].get_text()


def test_exception_in_one_page_is_isolated_and_logged_without_text(manual, tmp_path, monkeypatch, capsys):
    real = run_mod.page_units

    def boom(page):
        if "43-5" in page.get_text():
            raise ValueError("secret Relay name text")
        return real(page)
    monkeypatch.setattr(run_mod, "page_units", boom)
    res, _ = go(manual, tmp_path / "o")
    o = by_label(res)
    assert o["43-5"].status == "failed" and o["43-5"].error == "ValueError"
    assert o["43-1"].status == "ok" and o["43-21"].status == "ok"
    assert res.summary_line().startswith("SUMMARY: 6 of 8 pages succeeded (1 need OCR, 1 failed)")
    err = capsys.readouterr().err
    assert "43-5: failed (ValueError)" in err and "secret" not in err
    with pymupdf.open(tmp_path / "o" / "section43-vi.pdf") as vi:
        assert len(vi) == 8 and "LỖI" in vi[2].get_text()


def test_review_markers_rendered_and_listed(manual, tmp_path):
    out = tmp_path / "o"
    res, _ = go(manual, out)
    o = by_label(res)
    assert o["43-2"].review, "the WARNING segment must be routed to a human"      # safety text
    assert any(r["status"] == "error" for r in o["43-12"].review)                 # adapter rejected 'Starter motor'
    with pymupdf.open(out / "section43-vi.pdf") as vi:
        txt = vi[1].get_text()                                                      # page 43-2
        assert "[!]" in txt
        t12 = vi[4].get_text()
        assert "CHƯA DỊCH" in t12 and "Starter motor" in t12                       # failed output flagged, not hidden
        assert any(d.get("dashes") for d in vi[1].get_drawings())                 # shape marker, not colour only
    rep = write_report(res, out / "pilot-report.md").read_text("utf-8")
    assert "Page 43-2, segment" in rep and "not translated" in rep


def test_report_has_leakage_and_layout_columns(manual, tmp_path):
    res, _ = go(manual, tmp_path / "o")
    assert all(isinstance(o.leaked, int) for o in res.outcomes)
    assert any(o.leaked > 0 for o in res.outcomes if o.status == "ok")             # fake 'VI:' output keeps English words
    rep = write_report(res, tmp_path / "r.md").read_text("utf-8")
    assert "Layout flags" in rep and "Leftover English" in rep


def test_cli_dev_profile(manual, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from src.cli import app
    r = CliRunner().invoke(app, ["pilot", "--pdf", str(manual), "--pages", "4", "--profile", "dev",
                                 "--out", str(tmp_path / "o")], catch_exceptions=False)
    assert "SUMMARY:" in r.output or r.exit_code in (0, 1)
    assert CliRunner().invoke(app, ["pilot", "--show-model"]).output.strip() == "ollama:qwen2.5:7b"
    assert CliRunner().invoke(app, ["pilot", "--pdf", "/nope.pdf"]).exit_code == 2
