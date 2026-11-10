"""Script/language guard: CJK, Cyrillic, Thai... or mostly non-Vietnamese output is a QA error (SCR001/SCR002)."""
from src.guard import QAContext, fix_loop, qa_check
from src.guard.script import script_issues

SRC = "DANGER: The battery produces explosive gas. Keep sparks and flames away. Terminal voltage is 24 V."
# Recorded failure (first real-model run, qwen2.5:7b): Chinese suffix appended, placeholder dropped.
EL004_BAD = "Ắc quy sinh ra khí nổ. Giữ tia lửa tránh xa. Điện áp tại đầu cực là 24 V. 远离你的翻译区域…"
GOOD = "Ắc quy sinh ra khí nổ. Giữ tia lửa và ngọn lửa tránh xa. Điện áp tại đầu cực là 24 V."


def rules(issues):
    return {i.rule_id for i in issues}


def test_chinese_suffix_is_rejected():
    iss = script_issues(SRC, EL004_BAD)
    assert rules(iss) == {"SCR001"}
    assert "远" in iss[0].detail["chars"]


def test_good_vietnamese_passes():
    assert script_issues(SRC, GOOD) == []


def test_other_scripts_rejected():
    for bad in ("Ắc quy Привет", "Ắc quy สวัสดี", "Ắc quy 안녕", "Ắc quy こんにちは", "Ắc quy مرحبا", "Ắc quy 。"):
        assert rules(script_issues("Battery", bad)) == {"SCR001"}, bad


def test_symbols_used_in_units_are_allowed():
    assert script_issues("Resistance 0.5 Ω, 10 µF, 90 °C, 8 N·m", "Điện trở 0,5 Ω, 10 µF, 90 °C, 8 N·m") == []


def test_script_present_in_source_is_allowed():
    assert script_issues("Part 日本製 label", "Nhãn 日本製 của phụ tùng") == []


def test_mostly_english_output_is_rejected():
    out = "The battery produces explosive gas and you must keep sparks away from it."
    assert rules(script_issues(SRC, out)) == {"SCR002"}


def test_codes_and_placeholders_do_not_count_as_english():
    assert script_issues("Remove YT12-AB3C", "Tháo ⟦P001⟧ ra khỏi hộp rơ-le.") == []
    assert script_issues("Fuse F23 15 A", "Cầu chì F23 15 A") == []


def test_short_untranslated_label_is_not_flagged_as_mostly_english():
    assert script_issues("ECU", "ECU") == []


def test_qa_check_includes_script_rule():
    assert "SCR001" in rules(qa_check(SRC, "NGUY HIỂM: " + EL004_BAD, QAContext()))


def test_fix_loop_repairs_script_failure_with_repair_prompt():
    seen = []

    def fixer(target, issues):
        seen.append([i.rule_id for i in issues])
        assert any("Vietnamese" in i.fix_hint for i in issues)
        return "NGUY HIỂM: " + GOOD

    r = fix_loop(SRC, "NGUY HIỂM: " + EL004_BAD, QAContext(), fixer)
    assert r.attempts == 1 and not r.needs_review and seen == [["SCR001"]]


def test_fix_loop_gives_up_after_two_attempts():
    r = fix_loop(SRC, "NGUY HIỂM: " + EL004_BAD, QAContext(), lambda t, i: t)
    assert r.attempts == 2 and r.needs_review
