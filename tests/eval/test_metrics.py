import unicodedata

import pytest

from src.eval import metrics as M
from src.eval.cases import EvalCase, load_cases, load_glossary

G = load_glossary()


def case(source, protected=(), terms=(), ref=None):
    return EvalCase(id="t", source=source, domains=["electrical"], protected=list(protected),
                    expected_terms=list(terms), reference_vi=ref)


def nfd(s):
    return unicodedata.normalize("NFD", s)


# protected codes
def test_codes_kept():
    c = case("Connector C-12 and fuse F23 (15 A).", ["C-12", "F23", "15 A"])
    assert M.protected_codes(c, "Đầu nối C-12 và cầu chì F23 (15 A).").passed


def test_code_changed_or_lost():
    c = case("Connector C-12 and fuse F23.", ["C-12", "F23"])
    r = M.protected_codes(c, "Đầu nối C 12 và cầu chì F23.")
    assert not r.passed and r.detail["missing"] == ["C-12"]


def test_code_survives_nfd_target():
    c = case("See YT12-AB3C.", ["YT12-AB3C"])
    assert M.protected_codes(c, nfd("Xem bộ dây YT12-AB3C.")).passed


def test_invented_code_fails():
    c = case("Check the harness.", [])
    r = M.protected_codes(c, "Kiểm tra bộ dây YT12-AB3C.")
    assert not r.passed and r.detail["extra"]


def test_code_repeated_in_source_needs_both():
    c = case("R4 then R4 again.", ["R4"])
    assert not M.protected_codes(c, "R4 rồi lại").passed


def test_nbsp_inside_code_is_a_change():
    c = case("Torque 8 N·m.", ["8 N·m"])
    assert not M.protected_codes(c, "Mô-men 8 N·m.").passed


# numbers / units
def test_units_without_spaces_equal():
    assert M.number_unit(case("Supply 24 V and 15 A."), "Cấp 24V và 15A.").passed


def test_number_changed_fails():
    r = M.number_unit(case("Supply 24 V."), "Cấp 12 V.")
    assert not r.passed and r.detail["missing"] == ["24V"] and r.detail["extra"] == ["12V"]


def test_decimal_comma_is_flagged_keep_source():
    r = M.number_unit(case("Gap 0.5 mm."), "Khe hở 0,5 mm.")
    assert not r.passed and r.detail["decimal_separator_changed"] == ["0.5mm"]


def test_source_with_decimal_comma_kept():
    assert M.number_unit(case("Gap 0,5 mm."), "Khe hở 0,5 mm.").passed


def test_plus_minus_and_ohm_variants():
    c = case("Resistance 20 Ω ± 2 Ω.")
    assert M.number_unit(c, "Điện trở 20 Ω ±2 Ω.").passed
    assert not M.number_unit(c, "Điện trở 20 Ω 2 Ω.").passed  # lost the ±


def test_nm_variants_and_degree():
    c = case("Tighten to 8 N·m at 20 °C.")
    assert M.number_unit(c, "Siết 8 N•m ở 20℃.").passed
    assert M.number_unit(c, "Siết 8 N · m ở 20 ° C.").passed


def test_digits_of_codes_are_not_counted_as_measurements():
    c = case("Fuse F23 15A, connector C-12.", ["F23", "C-12"])
    assert M.number_tokens(c.source, c.protected) == ["15A"]


def test_extra_number_in_target_fails():
    assert not M.number_unit(case("Check the fuse."), "Kiểm tra cầu chì 10 A.").passed


def test_not_applicable_without_numbers():
    assert M.number_unit(case("Check the fuse."), "Kiểm tra cầu chì.").score is None


# glossary
def test_glossary_hit_nfd_and_case():
    c = case("Turn the key switch.", terms=["key switch"])
    assert M.glossary_hit(c, nfd("XOAY CÔNG TẮC KHÓA ĐIỆN."), G).passed


def test_glossary_miss_and_forbidden():
    c = case("Replace the fuse.", terms=["fuse"])
    r = M.glossary_hit(c, "Thay cầu dao.", G)
    assert not r.passed and r.detail["misses"] == ["fuse"] and r.detail["forbidden"] == ["cầu dao"]


def test_unknown_expected_term_is_an_error():
    with pytest.raises(KeyError):
        M.glossary_hit(case("x", terms=["nonexistent"]), "y", G)


def test_find_terms_longest_wins_and_plural():
    found = M.find_terms("Check the battery relay and two batteries.", G)
    assert "battery relay" in found and "battery" in found
    assert "relay" not in found  # consumed by 'battery relay'


# consistency
def test_consistency_detects_mixed_renderings():
    cs = [case("Check the fuse."), case("Replace the fuse.")]
    cs[0].id, cs[1].id = "a", "b"
    r = M.consistency(cs, {"a": "Kiểm tra cầu chì.", "b": "Thay cầu dao."}, G)
    assert [d["term"] for d in r["inconsistent"]] == ["fuse"] and r["score"] < 1


def test_consistency_clean():
    cs = [case("Check the fuse."), case("Replace the fuse.")]
    cs[0].id, cs[1].id = "a", "b"
    assert M.consistency(cs, {"a": "cầu chì", "b": "cầu chì"}, G)["score"] == 1


# safety
@pytest.mark.parametrize("src,tgt,ok", [
    ("WARNING: Disconnect the battery.", "CẢNH BÁO: Ngắt ắc quy.", True),
    ("WARNING: Disconnect the battery.", "cảnh báo: Ngắt ắc quy.", True),
    ("WARNING: Disconnect the battery.", nfd("CẢNH BÁO: Ngắt ắc quy."), True),
    ("WARNING: Disconnect the battery.", "Ngắt ắc quy.", False),
    ("WARNING: Disconnect the battery.", "WARNING: Ngắt ắc quy.", False),
    ("DANGER: High voltage.", "THẬN TRỌNG: Điện áp cao.", False),   # downgraded
    ("CAUTION: Hot.", "THẬN TRỌNG: Nóng.", True),
    ("NOTE: Colours differ.", "LƯU Ý: Màu khác nhau.", True),
])
def test_safety(src, tgt, ok):
    assert M.safety_signals(case(src), tgt).passed is ok


def test_safety_not_applicable_and_unexpected_signal():
    assert M.safety_signals(case("Check the fuse."), "Kiểm tra cầu chì.").score is None
    assert not M.safety_signals(case("Check the fuse."), "CẢNH BÁO: Kiểm tra.").passed


# length
def test_length_band():
    c = case("a" * 100)
    assert M.length_ratio(c, "b" * 120).passed
    assert not M.length_ratio(c, "b" * 30).passed
    assert not M.length_ratio(c, "b" * 400).passed


# leakage
def test_leakage_flags_english_words():
    c = case("Check the harness for damage.")
    r = M.leakage(c, "Kiểm tra harness xem có hư hỏng không.", G)
    assert not r.passed and r.detail["leaked"] == ["harness"]


def test_leakage_ignores_protected_and_caps():
    c = case("Turn to ON, terminal 3, Fig. 5-3.", ["terminal 3", "Fig. 5-3"])
    assert M.leakage(c, "Xoay sang ON, terminal 3, Fig. 5-3.", G).score == 0


# placeholders
def test_placeholder_roundtrip():
    m = {"⟦P001⟧": "C-12", "⟦P002⟧": "24 V"}
    assert M.placeholder_roundtrip("Nối ⟦P001⟧ ở ⟦P002⟧", m).passed
    assert not M.placeholder_roundtrip("Nối ⟦P001⟧", m).passed          # dropped
    assert not M.placeholder_roundtrip("⟦P001⟧ ⟦P001⟧ ⟦P002⟧", m).passed  # duplicated
    assert not M.placeholder_roundtrip("⟦P001⟧ ⟦P002⟧ ⟦P009⟧", m).passed  # invented
    assert M.placeholder_roundtrip("⟦P002⟧ ⟦P001⟧", m).passed            # reordered only
    assert M.placeholder_roundtrip(None, m).score is None


# the human references must satisfy their own cases (validates cases + glossary + metrics together)
def test_references_pass_every_hard_and_glossary_metric():
    sig = None
    bad = []
    for c in load_cases():
        t = c.reference_vi
        for name, r in (("codes", M.protected_codes(c, t)), ("num", M.number_unit(c, t)),
                        ("safety", M.safety_signals(c, t)), ("gloss", M.glossary_hit(c, t, G)),
                        ("leak", M.leakage(c, t, G)), ("len", M.length_ratio(c, t))):
            if r.passed is False:
                bad.append((c.id, name, r.detail))
    assert not bad, bad


def test_references_are_consistent_across_document():
    cs = load_cases()
    r = M.consistency(cs, {c.id: c.reference_vi for c in cs}, G)
    assert r["inconsistent"] == [], r["inconsistent"]
