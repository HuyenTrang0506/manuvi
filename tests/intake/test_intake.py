import json

from typer.testing import CliRunner

from src.intake.classify import classify_pdf, load_overrides
from src.intake.jobspec import build_jobspec, parse_sections
from src.intake.terms import extract_terms
from src.cli import app

from .conftest import CODES


def test_parse_sections_consecutive():
    s = parse_sections("23:1-42,43:1-22", 64)
    assert (s[0].pdf_start, s[0].pdf_end) == (1, 42)
    assert (s[1].pdf_start, s[1].pdf_end) == (43, 64)


def test_scanned_detected_before_cost(pdf):
    spec = build_jobspec(pdf, "10:1-3,11:1-2")
    assert spec.scanned_pages == [4]
    assert spec.needs_review and spec.warnings
    assert spec.pages[3].section == "11" and spec.pages[3].section_page == 1


def test_classification_categories(pdf):
    got = {c.pdf_page: c.auto for c in classify_pdf(pdf)}
    assert got[1] == "text" and got[4] == "scanned" and got[5] == "text"
    assert got[2] in ("table", "mixed") and got[3] == "diagram"


def test_override_wins_and_is_recorded(pdf, tmp_path):
    ov = tmp_path / "overrides.yaml"
    ov.write_text("pages:\n  1: diagram\n  4: text\n")
    got = {c.pdf_page: c for c in classify_pdf(pdf, load_overrides(ov))}
    assert got[1].final == "diagram" and got[1].auto == "text" and got[1].overridden
    assert got[4].final == "text" and got[4].auto == "scanned" and got[4].override == "text"
    assert got[5].override is None and not got[5].overridden


def test_protected_codes_preserved_and_excluded(pdf):
    terms = extract_terms(pdf)
    prot = {t.term for t in terms if t.protected}
    assert set(CODES) <= prot  # exact strings, byte-for-byte
    for t in terms:
        if t.protected:
            assert t.translate is False
        else:
            assert not any(c.lower() in t.term for c in CODES)
    yt = next(t for t in terms if t.term == "YT12-AB3C")
    assert yt.count == 2 and yt.pages == [1, 5] and 1 <= len(yt.contexts) <= 2
    relay = next(t for t in terms if t.term == "relay box")
    assert relay.count >= 3 and relay.protected is False and relay.contexts


def test_deterministic(pdf, tmp_path):
    runner = CliRunner()
    outs = []
    for n in ("a", "b"):
        d = tmp_path / n
        r = runner.invoke(app, ["intake", str(pdf), "--sections", "10:1-5", "--out-dir", str(d)])
        assert r.exit_code == 0, r.output
        outs.append({f: (d / f).read_bytes() for f in ("jobspec.json", "page_classes.json", "terms.json")})
    assert outs[0] == outs[1]
    assert json.loads(outs[0]["jobspec.json"])["scanned_pages"] == [4]
