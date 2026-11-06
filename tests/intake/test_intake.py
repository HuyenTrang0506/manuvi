import json

import pytest
from typer.testing import CliRunner

from src.intake.classify import classify_pdf, load_overrides
from src.intake.jobspec import IntakeError, build_jobspec
from src.intake.terms import extract_terms, load_patterns
from src.cli import app

from .conftest import CODES, make_pdf


def test_label_map_resolves_with_gap_between_sections(tmp_path):
    # PDF: cover, 23-15, 23-16, unrelated pages, 43-1, 43-2
    f = make_pdf(tmp_path / "g.pdf", [None, "23-15", "23-16", None, None, "43-1", "43-2"])
    spec = build_jobspec(f, "23:15-16,43:1-2")
    assert spec.mapping == "labels" and not spec.needs_review
    assert [p.pdf_page for p in spec.pages] == [2, 3, 6, 7]
    assert [p.label for p in spec.pages] == ["23-15", "23-16", "43-1", "43-2"]
    gap = [w for w in spec.warnings if "not in this job" in w][0]
    assert "1" in gap and "4-5" in gap  # head and middle gaps both reported


def test_missing_label_aborts(tmp_path):
    f = make_pdf(tmp_path / "m.pdf", ["23-1", "23-3"])
    with pytest.raises(IntakeError, match="23-2"):
        build_jobspec(f, "23:1-3")


def test_duplicate_label_aborts(tmp_path):
    f = make_pdf(tmp_path / "d.pdf", ["23-1", "23-1", "23-2"])
    with pytest.raises(IntakeError, match="duplicated"):
        build_jobspec(f, "23:1-2")


def test_non_monotonic_aborts(tmp_path):
    f = make_pdf(tmp_path / "n.pdf", ["23-2", "23-1"])
    with pytest.raises(IntakeError, match="increasing"):
        build_jobspec(f, "23:1-2")


def test_explicit_page_map(tmp_path):
    f = make_pdf(tmp_path / "p.pdf", [None] * 8)
    pm = tmp_path / "pm.yaml"
    pm.write_text("'23': 2\n'43': 6\n")
    spec = build_jobspec(f, "23:1-3,43:1-2", pm)
    assert spec.mapping == "page_map" and not spec.needs_review
    assert [p.pdf_page for p in spec.pages] == [2, 3, 4, 6, 7]
    pm.write_text("'23': 2\n")
    with pytest.raises(IntakeError, match="43"):
        build_jobspec(f, "23:1-3,43:1-2", pm)
    pm.write_text("'23': 7\n")
    with pytest.raises(IntakeError, match="out of range"):
        build_jobspec(f, "23:1-3", pm)


def test_consecutive_fallback_needs_review(tmp_path):
    f = make_pdf(tmp_path / "c.pdf", [None] * 4)
    spec = build_jobspec(f, "23:1-2,43:1-2")
    assert spec.mapping == "consecutive" and spec.needs_review
    assert [p.pdf_page for p in spec.pages] == [1, 2, 3, 4]


def test_cli_aborts_without_output(tmp_path):
    f = make_pdf(tmp_path / "m.pdf", ["23-1", "23-3"])
    out = tmp_path / "o"
    r = CliRunner().invoke(app, ["intake", str(f), "--sections", "23:1-3", "--out-dir", str(out)])
    assert r.exit_code == 1 and not out.exists()


def test_only_job_pages_processed(pdf, tmp_path):
    out = tmp_path / "o"
    r = CliRunner().invoke(app, ["intake", str(pdf), "--sections", "10:1-1", "--out-dir", str(out)])
    assert r.exit_code == 0, r.output
    assert [c["pdf_page"] for c in json.loads((out / "page_classes.json").read_text())] == [1]
    terms = json.loads((out / "terms.json").read_text())
    assert all(t["pages"] == [1] for t in terms)  # page 5 (also has YT12-AB3C) excluded
    assert extract_terms(pdf, pages=[]) == []


def test_override_scanned_to_text_feeds_term_extraction(pdf, tmp_path):
    out = tmp_path / "o"
    ov = tmp_path / "ov.yaml"
    ov.write_text("pages:\n  4: text\n")
    r = CliRunner().invoke(app, ["intake", str(pdf), "--sections", "10:1-3,11:1-2",
                                 "--overrides", str(ov), "--out-dir", str(out)])
    assert r.exit_code == 0, r.output
    cls = {c["pdf_page"]: c for c in json.loads((out / "page_classes.json").read_text())}
    assert cls[4]["final"] == "text" and cls[4]["auto"] == "scanned"
    # scanned flag in the JobSpec stays (auto detection), the override only steers extraction
    assert json.loads((out / "jobspec.json").read_text())["scanned_pages"] == [4]


@pytest.mark.parametrize("text,expected", [
    ("harness YT12-\nAB3C end", "YT12-\nAB3C"),          # wrapped after hyphen
    ("harness YT 12-AB3C end", "YT 12-AB3C"),            # space after prefix
    ("harness YT12.AB3C end", "YT12.AB3C"),              # dotted
    ("harness YT12-AB3C. The relay", "YT12-AB3C"),       # sentence dot not swallowed
    ("see Pin No. 12 here", "Pin No. 12"),               # pin number
    ("terminal 5 here", "terminal 5"),
    ("wire 0.85 mm2 here", "0.85 mm2"),                  # value + unit
    ("supply 24 V and 5 A", "24 V"),
    ("torque 49 N·m", "49 N·m"),
    ("see Fig. 23-15 and page 43-2", "Fig. 23-15"),
])
def test_protected_regexes(text, expected):
    hits = [m.group(0) for _, rx in load_patterns() for m in rx.finditer(text)]
    assert expected in hits  # exact bytes, including wrapped newline/space/dot


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
        r = runner.invoke(app, ["intake", str(pdf), "--sections", "10:1-3,11:1-2", "--out-dir", str(d)])
        assert r.exit_code == 0, r.output
        outs.append({f: (d / f).read_bytes() for f in ("jobspec.json", "page_classes.json", "terms.json")})
    assert outs[0] == outs[1]
    assert json.loads(outs[0]["jobspec.json"])["scanned_pages"] == [4]
