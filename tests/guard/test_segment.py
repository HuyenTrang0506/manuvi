"""Shared guarded-translation path: deterministic signal words + mask + QA + capped fix loop."""
import json
from pathlib import Path

import pytest
import yaml

from src.guard import load_patterns, load_signal_table
from src.guard.segment import fix_messages, prepare, run_guarded, split_signal

TABLE = load_signal_table()
FX = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "q1_real_failures.json").read_text("utf-8"))
EL004 = "DANGER: The battery produces explosive gas. Keep sparks and flames away. Terminal voltage is 24 V."
EL005 = "CAUTION: Tighten the battery terminal bolts to 8 N·m. Do not overtighten."


def replay(key):
    calls = iter(FX[key]["calls"])
    log = []

    def complete(stage, messages):
        log.append((stage, messages))
        return {"text": next(calls), "tokens_in": 10, "tokens_out": 5, "cost_usd": 0.0}
    return complete, log


def build(masked):
    return [{"role": "system", "content": "translate"}, {"role": "user", "content": masked}]


@pytest.mark.parametrize("src,en,sep,body", [
    ("DANGER: Keep clear.", "DANGER", ": ", "Keep clear."),
    ("caution: hot", "CAUTION", ": ", "hot"),
    ("Warning:\nDisconnect it.", "WARNING", ":\n", "Disconnect it."),
    ("NOTE\nSee Fig. 3-2.", "NOTE", "\n", "See Fig. 3-2."),
    ("  Important : Do this first", "IMPORTANT", ": ", "Do this first"),
])
def test_split_signal(src, en, sep, body):
    assert split_signal(src, TABLE) == (en, sep, body)


@pytest.mark.parametrize("src", [
    "Do not ignore this WARNING in the text.", "NOTE that this is prose", "NOTES: x", "DANGEROUS: x", "Fuse F23", ""])
def test_split_signal_only_leading_with_colon_or_newline(src):
    assert split_signal(src, TABLE)[0] is None


def test_signal_word_never_reaches_the_model_and_is_reattached():
    complete, log = replay("EL-004")
    r = run_guarded(EL004, patterns=load_patterns(), table=TABLE, build_messages=build, complete=complete)
    first = log[0][1]
    assert "DANGER" not in json.dumps(first, ensure_ascii=False)
    assert "24 V" not in first[-1]["content"] and "⟦P001⟧" in first[-1]["content"]
    assert r.text.startswith("NGUY HIỂM: ") and r.text.endswith("là 24 V.")


def test_el004_chinese_suffix_and_dropped_placeholder_repaired_by_fix_loop():
    complete, log = replay("EL-004")
    r = run_guarded(EL004, patterns=load_patterns(), table=TABLE, build_messages=build, complete=complete)
    assert [s for s, _ in log] == ["translate", "fix1"]
    assert r.repaired and not r.routed_to_review and r.attempts == 1
    assert not any(ord(c) > 0x2E80 for c in r.text)
    fix_prompt = log[1][1][-1]["content"]
    assert fix_prompt.startswith("FIX") and "non-Vietnamese" in fix_prompt and "dropped" in fix_prompt
    assert r.calls and sum(c["tokens_in"] for c in r.calls) == 20


def test_el005_spurious_signal_and_wrong_verb_repaired():
    complete, log = replay("EL-005")
    r = run_guarded(EL005, patterns=load_patterns(), table=TABLE, build_messages=build, complete=complete)
    assert r.text == "THẬN TRỌNG: Siết bu lông cực ắc quy với mô-men 8 N·m. Không siết quá chặt."
    assert r.repaired and "CÔNG TY" not in r.text
    assert "SIG001" in log[1][1][-1]["content"] or "CẢNH BÁO" in log[1][1][-1]["content"]


def test_never_accept_output_that_failed_the_guard():
    complete, log = replay("EL-004-never-repaired")
    r = run_guarded(EL004, patterns=load_patterns(), table=TABLE, build_messages=build, complete=complete)
    assert len(log) == 3 and r.attempts == 2          # max 2 repairs
    assert r.routed_to_review and not r.repaired
    assert r.text == ""                               # script garbage is never shipped, not even truncated
    assert "SCR001" in r.rules


def test_signal_only_segment_needs_no_model_call():
    def boom(stage, messages):
        raise AssertionError("no LLM call expected")
    r = run_guarded("DANGER:", patterns=load_patterns(), table=TABLE, build_messages=build, complete=boom)
    assert r.text == "NGUY HIỂM" and not r.routed_to_review


def test_no_signal_segment_is_unchanged_path():
    p = prepare("Remove YT12-AB3C.", load_patterns(), TABLE)
    assert p.signal is None and p.prefix == "" and p.masked == "Remove ⟦P001⟧."


def test_fix_messages_shape():
    msgs = fix_messages(build("x"), "bad", [{"severity": "error", "message": "m", "fix_hint": "h"}], 1)
    assert msgs[-2] == {"role": "assistant", "content": "bad"} and msgs[-1]["content"].startswith("FIX (attempt 1)")


def test_signal_table_marked_proposed_pending_approval():
    cfg = yaml.safe_load((Path(__file__).resolve().parents[2] / "config" / "signal_words.yaml").read_text("utf-8"))
    assert cfg["status"] == "proposed" and "founder" in cfg["approval"] and not cfg.get("approved_by")
    assert cfg["signal_words"]["CAUTION"] == "THẬN TRỌNG"
