import random

import pytest

from src.guard import (QAContext, Severity, check_placeholders, diff_numbers, fix_loop,
                       load_signal_table, mask, qa_check, signal_mismatches, unmask)

CODES = ["YT12-AB3C", "YT 12-AB3C", "YT12-\nAB3C", "YT12.AB3C", "AB12-3456X", "pin 7", "Terminal No. 12",
         "24 V", "1,5 mm²", "3.5 kPa", "Fig. 4-2", "page 15", "20 A", "50%"]
WORDS = ["the", "connect", "to", "harness", "and", "check", "WARNING", "NOTE", "ground", "-", ".", ",", "\n", "é", "ố", "  "]


def rand_text(rng):
    parts = [rng.choice(CODES) if rng.random() < 0.35 else rng.choice(WORDS) for _ in range(rng.randint(1, 40))]
    return " ".join(parts)


@pytest.mark.parametrize("seed", range(200))
def test_roundtrip_byte_identical(seed):
    text = rand_text(random.Random(seed))
    masked, mapping = mask(text)
    assert unmask(masked, mapping).encode() == text.encode()
    for orig in mapping.values():
        assert orig not in masked


def test_codes_masked_and_stable():
    t = "Connect YT12-AB3C to pin 7, 24 V (Fig. 4-2)."
    m1, map1 = mask(t)
    m2, map2 = mask(t)
    assert (m1, map1) == (m2, map2)
    assert "⟦P001⟧" in m1 and "YT12" not in m1
    assert list(map1.values())[0] == "YT12-AB3C"


def test_mask_logs(caplog):
    caplog.set_level("INFO", logger="manuvi.guard.mask")
    mask("pin 7 and 24 V")
    assert len([r for r in caplog.records if r.message.startswith("mask ")]) == 2


def test_placeholder_dropped_duplicated_reordered():
    masked, mp = mask("A pin 7 and 24 V then YT12-AB3C")
    p = list(mp)
    assert check_placeholders(masked, mp).ok
    assert check_placeholders(masked.replace(p[0], ""), mp).dropped == [p[0]]
    assert check_placeholders(masked + p[1], mp).duplicated == [p[1]]
    swapped = f"{p[2]} {p[1]} {p[0]}"
    assert check_placeholders(swapped, mp).reordered
    assert check_placeholders(masked + " ⟦P099⟧", mp).unknown == ["⟦P099⟧"]


def test_numbers_keep_source():
    assert diff_numbers("Torque 12 N·m, gap 1,5 mm and 3 bolts", "Mô-men 12 N·m, khe 1,5 mm và 3 bu lông") == ([], [])
    assert diff_numbers("clearance 1,5 turns", "khe hở 1.5 vòng") == (["1,5"], ["1.5"])
    assert any(i.rule_id == "PROT001" for i in qa_check("gap 1,5 mm", "khe 1.5 mm"))
    issues = qa_check("tolerance 0,2 and 3 bolts", "dung sai 0.2 và 3 bu lông")
    assert any(i.rule_id == "NUM001" and i.severity == Severity.error for i in issues)


def test_signal_words():
    tbl = load_signal_table()
    assert tbl["WARNING"] == "CẢNH BÁO"
    assert signal_mismatches("WARNING: hot", "CẢNH BÁO: nóng", tbl) == []
    assert signal_mismatches("WARNING: hot", "LƯU Ý: nóng", tbl)
    issues = qa_check("CAUTION: sharp edge", "Cẩn thận: cạnh sắc")
    sig = [i for i in issues if i.rule_id == "SIG001"]
    assert sig and sig[0].severity == Severity.error and "THẬN TRỌNG" in sig[0].fix_hint


def test_protected_code_altered():
    issues = qa_check("Use YT12-AB3C at 24 V", "Dùng YT12-AB3D ở 24 V")
    assert any(i.rule_id == "PROT001" for i in issues)
    ok = qa_check("Use YT12-AB3C at 24 V", "Dùng YT12-AB3C ở 24 V")
    assert [i.severity for i in ok] == [Severity.ok]


def test_qa_with_mapping():
    masked, mp = mask("Use YT12-AB3C at 24 V")
    ctx = QAContext(mapping=mp)
    assert qa_check("Use YT12-AB3C at 24 V", "Dùng ⟦P001⟧ ở ⟦P002⟧", ctx)[0].severity == Severity.ok
    ids = [i.rule_id for i in qa_check("Use YT12-AB3C at 24 V", "Dùng ⟦P001⟧", ctx)]
    assert "PH001" in ids


def test_fix_loop_cap_and_success():
    src, bad = "Use YT12-AB3C", "Dùng YT12-ZZZZ"
    calls = []

    def never(t, issues):
        calls.append(1)
        return t

    r = fix_loop(src, bad, None, never, max_attempts=10)
    assert len(calls) == 2 and r.attempts == 2 and r.needs_review

    r = fix_loop(src, bad, None, lambda t, i: "Dùng YT12-AB3C")
    assert r.attempts == 1 and not r.needs_review

    calls.clear()
    r = fix_loop(src, "Dùng YT12-AB3C", None, never)
    assert r.attempts == 0 and not calls
