"""Glossary variants, Vietnamese matching, keep-English names, leakage limits, timing."""
import json

from src.eval import metrics as M
from src.eval.cases import EvalCase, GlossaryTerm, load_cases, load_glossary, set_hash
from src.eval.report import Output, build_report, gate

G = load_glossary()
CASES = load_cases()


def case(source, terms=(), protected=(), keep=(), priority="p1"):
    return EvalCase(id="t", source=source, domains=["electrical"], priority=priority, expected_terms=list(terms),
                    protected=list(protected), keep=list(keep))


# --- Vietnamese matching ---------------------------------------------------------------------------
def test_fold_vi_ignores_case_hyphen_space_nfd_and_tone_placement():
    import unicodedata
    assert M.fold_vi("Rơ-le") == M.fold_vi("rơ le") == M.fold_vi("RƠLE")
    assert M.fold_vi("điều hoà") == M.fold_vi("Điều hòa")
    assert M.fold_vi("thuỷ lực") == M.fold_vi("thủy lực")
    assert M.fold_vi(unicodedata.normalize("NFD", "cầu chì")) == M.fold_vi("cầu chì")
    assert M.fold_vi("quý") == M.fold_vi("quý")    # 'qu' + y keeps its tone on y


def test_glossary_hit_accepts_listed_variants_and_reports_them():
    c = case("Check the starter and the connector.", ["starter", "connector"])
    r = M.glossary_hit(c, "Kiểm tra củ đề và giắc cắm.", G)
    assert r.passed and r.detail["variant_hits"] == ["starter", "connector"]
    r = M.glossary_hit(c, "Kiểm tra máy khởi động và giắc nối.", G)
    assert r.passed and r.detail["variant_hits"] == []


def test_glossary_hit_rejects_unlisted_and_english_forms():
    c = case("Check the connector.", ["connector"])
    r = M.glossary_hit(c, "Kiểm tra connector.", G)
    assert not r.passed and r.detail["misses"] == ["connector"]


def test_relay_spellings_all_hit():
    c = case("Replace the relay.", ["relay"])
    for vi in ("Thay rơ le.", "Thay rơ-le.", "Thay RƠ LE.", "Thay rơle."):
        assert M.glossary_hit(c, vi, G).passed, vi


def test_forbidden_variant_still_fails_but_not_on_pin_code():
    c = case("Replace the battery.", ["battery"])
    assert not M.glossary_hit(c, "Thay pin.", G).passed                    # dry cell: forbidden
    c = case("Pin 1 is battery power.", ["battery"], protected=["Pin 1"])
    assert M.glossary_hit(c, "Pin 1 là nguồn ắc quy.", G).passed            # the code 'Pin 1' is not the word pin
    c = case("Replace the fuse.", ["fuse"])
    assert not M.glossary_hit(c, "Thay cầu dao.", G).passed


def test_consistency_accepts_variants_and_reports_mixed_forms():
    cs = [EvalCase(id="a", source="Check the connector.", domains=["electrical"]),
          EvalCase(id="b", source="Clean the connector.", domains=["electrical"])]
    out = M.consistency(cs, {"a": "Kiểm tra giắc nối.", "b": "Làm sạch giắc cắm."}, G)
    assert out["inconsistent"] == [] and out["mixed_forms"][0]["term"] == "connector"
    out = M.consistency(cs, {"a": "Kiểm tra giắc nối.", "b": "Làm sạch connector."}, G)
    assert [d["term"] for d in out["inconsistent"]] == ["connector"]


# --- keep-English names -----------------------------------------------------------------------------
def test_kept_names_pass_and_fail():
    c = case("Pin 5: ECU signal, wire W/R, ground GND, B+.", keep=["ECU", "W/R", "GND", "B+"])
    ok = "Pin 5: tín hiệu ECU, dây W/R, nối mát GND, B+."
    assert M.kept_names(c, ok).passed
    r = M.kept_names(c, "Pin 5: tín hiệu bộ điều khiển, dây W/R, nối mát GND, B+.")
    assert not r.passed and r.detail["missing"] == ["ECU"]
    assert not M.kept_names(c, ok.replace("W/R", "T/Đ")).passed
    assert not M.kept_names(c, ok.replace("ECU", "ecu")).passed         # case-sensitive: a name is a name
    assert M.kept_names(case("no names"), "x").score is None


def test_kept_names_is_a_hard_metric_in_the_gate():
    assert "kept_names" in M.HARD
    outs = {c.id: Output(id=c.id, output=c.reference_vi) for c in CASES}
    base = build_report(CASES, outs, G, model="ref")
    outs["EL-056"] = Output(id="EL-056", output=outs["EL-056"].output.replace("ECU", "bộ điều khiển"))
    g = gate(build_report(CASES, outs, G, model="x"), base)
    assert not g.ok and any("kept_names" in f for f in g.failures)


# --- leakage --------------------------------------------------------------------------------------
def test_leakage_lists_english_words_and_spares_names():
    c = case("Replace the harness and the ECU of the Kobelco machine.", ["harness"], keep=["ECU"])
    r = M.leakage(c, "Thay thế harness và ECU của máy Kobelco.", G)
    assert r.detail["leaked"] == ["harness"] and not r.passed
    assert M.leakage(c, "Thay bó dây điện và ECU của máy Kobelco.", G).passed


def test_leakage_gate_zero_in_p0_and_half_percent_overall():
    outs = {c.id: Output(id=c.id, output=c.reference_vi) for c in CASES}
    base = build_report(CASES, outs, G, model="ref")
    assert base["metrics"]["leakage"]["kind"] == "near-hard"
    # p0 case with one leaked English word
    p0 = next(c for c in CASES if c.priority == "p0" and "harness" in c.expected_terms)
    bad = dict(outs)
    bad[p0.id] = Output(id=p0.id, output=outs[p0.id].output.replace("Bó dây điện", "harness").replace("bó dây điện", "harness"))
    g = gate(build_report(CASES, bad, G, model="x"), base, soft_tolerance_pct=100)
    assert any(p0.id in f and "p0" in f for f in g.failures)
    # many leaked words in p1/p2 cases: overall limit fires even with a huge soft tolerance
    bad = dict(outs)
    for c in CASES:
        if c.priority != "p0" and "connector" in c.expected_terms:
            bad[c.id] = Output(id=c.id, output=outs[c.id].output.replace("giắc nối", "connector"))
    g = gate(build_report(CASES, bad, G, model="x"), base, soft_tolerance_pct=100)
    assert any("leakage" in f and "limit" in f for f in g.failures)


def test_english_in_parentheses_counts_as_leakage():
    c = case("Check the relay box.", ["relay box"])
    r = M.leakage(c, "Kiểm tra hộp rơ le (relay box).", G)
    assert r.detail["leaked"] == ["relay"] or "relay" in r.detail["leaked"]


# --- the v2 set ---------------------------------------------------------------------------------------
def test_v2_set_shape():
    assert len(CASES) == 75 and CASES[0].id == "EL-001"
    new = [c for c in CASES if int(c.id[3:]) >= 56]
    assert len(new) == 20
    tags = {t for c in new for t in c.domains}
    assert tags >= {"diagram_legend", "pin_table", "fuse_relay", "wire_color", "harness", "location",
                    "abbreviation", "leakage_trap"}
    assert {k for c in new for k in c.keep} >= {"ECU", "SOL", "SW", "GND", "B+", "W/R", "LgY", "B"}
    assert all(c.reference_vi for c in new)


def test_keep_field_does_not_change_the_hash_of_sets_without_it():
    from src.eval.cases import read_cases
    from pathlib import Path
    v1 = read_cases(Path(__file__).resolve().parents[2] / "src/eval/data/electrical_v1.jsonl")
    m = json.loads((Path(__file__).resolve().parents[2] / "src/eval/data/electrical_v1.manifest.json").read_text())
    assert set_hash(v1) == m["sha256"]


def test_references_use_canonical_forms_only():
    # the reference translations model the canonical wording: no variants, no English words except names
    g = {k: t for k, t in G.items() if not t.keep_english}
    for c in CASES:
        for en in c.expected_terms:
            t = g.get(en.casefold())
            if t:
                assert M.fold_vi(t.term_vi) in M.fold_vi(c.reference_vi), (c.id, en)


# --- timing ---------------------------------------------------------------------------------------------
def test_report_has_timing():
    outs = {c.id: Output(id=c.id, output=c.reference_vi, seconds=2.0) for c in CASES}
    r = build_report(CASES, outs, G, model="x")
    assert r["timing"] == {"total_seconds": 150.0, "seconds_per_segment": 2.0}
