import copy
import json

from src.eval.cases import load_cases, load_glossary
from src.eval.report import Output, build_report, gate, render_markdown

G = load_glossary()
CASES = load_cases()


def perfect():
    return {c.id: Output(id=c.id, output=c.reference_vi, cost_usd=0.001, tokens_in=10, tokens_out=12) for c in CASES}


def rep(outputs=None):
    return build_report(CASES, outputs or perfect(), G, model="ref")


def test_perfect_run_scores_full_on_hard_metrics():
    r = rep()
    for m in ("protected_codes", "number_unit", "safety_signals"):
        assert r["metrics"][m]["value"] == 1.0, m
    assert r["metrics"]["glossary_hit"]["value"] == 1.0
    assert r["consistency"]["score"] == 1.0
    assert all(c["hard_pass"] for c in r["cases"])
    assert r["cost"]["pages"] == len({c.page for c in CASES})
    assert abs(r["cost"]["cost_per_page_usd"] - r["cost"]["total_usd"] / r["cost"]["pages"]) < 1e-5


def test_domain_and_priority_sections():
    r = rep()
    assert {"electrical", "safety", "wire_color", "connector"} <= set(r["domains"])
    assert set(r["priorities"]) == {"p0", "p1", "p2"}
    assert r["domains"]["electrical"]["n"] == len(CASES)


def test_json_serialisable_and_markdown_contains_sections():
    r = rep()
    json.dumps(r, ensure_ascii=False)
    md = render_markdown(r, gate(r, r))
    for s in ("## Gate: PASS", "## Overall", "## By domain", "## By priority", "PROPOSED"):
        assert s in md


def degrade(outputs, cid, fn):
    o = copy.deepcopy(outputs)
    o[cid] = o[cid].model_copy(update={"output": fn(o[cid].output)})
    return o


def test_gate_passes_on_identical_run():
    r = rep()
    assert gate(r, r).ok


def test_gate_fails_on_one_lost_code():
    base = rep()
    cid = next(c.id for c in CASES if "F23" in c.protected)
    bad = rep(degrade(perfect(), cid, lambda t: t.replace("F23", "F 23")))
    g = gate(bad, base)
    assert not g.ok
    assert any("protected_codes" in f for f in g.failures) and any(cid in f for f in g.failures)


def test_gate_fails_on_number_and_safety_drops():
    base = rep()
    n = next(c.id for c in CASES if "24 V" in c.protected)
    s = next(c.id for c in CASES if c.source.startswith("WARNING"))
    g = gate(rep(degrade(degrade(perfect(), n, lambda t: t.replace("24 V", "12 V")), s,
                         lambda t: t.replace("CẢNH BÁO", "Lưu ý"))), base)
    assert not g.ok
    assert any("number_unit" in f for f in g.failures) and any("safety_signals" in f for f in g.failures)


def test_gate_hard_drop_in_a_domain_is_named():
    base = rep()
    cid = next(c.id for c in CASES if "wire_color" in c.domains and c.protected)
    g = gate(rep(degrade(perfect(), cid, lambda t: t.replace("B/W", "N/T"))), base)
    assert any("domain wire_color" in f for f in g.failures)


def test_soft_drop_within_tolerance_passes_and_beyond_fails():
    base = rep()
    outs = perfect()
    # break glossary terms in a few cases: one pair out of ~150 is under 3%, many are not
    one = next(c.id for c in CASES if c.expected_terms == ["fuse"] and "F23" in c.protected)
    small = gate(rep(degrade(outs, one, lambda t: t.replace("cầu chì", "xxx"))), base, soft_tolerance_pct=3.0)
    assert not [f for f in small.failures if "soft metric glossary_hit" in f and "overall" in f]
    many = perfect()
    for c in CASES[:20]:
        many = degrade(many, c.id, lambda t: t.replace("ắc quy", "xxx").replace("cầu chì", "yyy").replace("rơ-le", "zzz"))
    big = gate(rep(many), base, soft_tolerance_pct=3.0)
    assert any("soft metric glossary_hit" in f for f in big.failures)


def test_leakage_increase_beyond_tolerance_fails():
    base = rep()
    outs = perfect()
    for c in CASES:
        outs = degrade(outs, c.id, lambda t: t + " harness connector terminal wiring battery")
    g = gate(rep(outs), base)
    assert any("leakage" in f for f in g.failures)


def test_gate_refuses_a_changed_set():
    a, b = rep(), rep()
    b["set"]["sha256"] = "0" * 64
    assert not gate(a, b).ok


def test_missing_output_counts_as_failure():
    outs = perfect()
    del outs[CASES[0].id]
    r = rep(outs)
    assert not r["cases"][0]["hard_pass"] or r["cases"][0]["error"] == "missing output"
