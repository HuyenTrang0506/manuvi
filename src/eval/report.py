"""Scoring a run, per-case / per-domain / per-priority aggregation, JSON + Markdown, and the regression gate."""
from __future__ import annotations

import json
from collections import defaultdict
from typing import Optional

from pydantic import BaseModel

from . import metrics as M
from .cases import EvalCase, GlossaryTerm, glossary_hash, set_hash

DEFAULT_SOFT_TOLERANCE_PCT = 3.0
EPS = 1e-9


class Output(BaseModel):
    """One model output for one case. `output` is the unmasked VI text the metrics see."""
    id: str
    output: str = ""
    masked_output: Optional[str] = None
    mapping: Optional[dict[str, str]] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    error: str = ""
    attempts: int = 0                      # fix-loop repairs used (0..2)
    repaired: bool = False                 # failed the guard first, passed after the fix loop
    routed_to_review: bool = False         # still failing after the loop: NOT accepted, reported apart
    review_rules: list[str] = []           # guard rules still failing when routed
    seconds: float = 0.0                   # wall time for this case (all calls of the fix loop)


def score_case(case: EvalCase, out: Output, glossary: dict[str, GlossaryTerm]) -> dict[str, M.MetricResult]:
    t = out.output
    return {
        "protected_codes": M.protected_codes(case, t),
        "number_unit": M.number_unit(case, t),
        "safety_signals": M.safety_signals(case, t),
        "kept_names": M.kept_names(case, t),
        "glossary_hit": M.glossary_hit(case, t, glossary),
        "length_ratio": M.length_ratio(case, t),
        "leakage": M.leakage(case, t, glossary),
        "placeholder_roundtrip": M.placeholder_roundtrip(out.masked_output, out.mapping),
    }


def _aggregate(rows: list[dict]) -> dict[str, dict]:
    """rows: per-case entries. Returns metric -> {kind, value, n}. Value None when nothing applies."""
    agg: dict[str, dict] = {}
    for name in (*M.HARD, *M.SOFT):
        kind = "hard" if name in M.HARD else "near-hard" if name in M.NEAR_HARD else "soft"
        vals = [(r["metrics"][name]) for r in rows
                if r["metrics"][name]["score"] is not None and not r.get("routed_to_review")]
        if not vals:
            agg[name] = {"kind": kind, "value": None, "n": 0}
            continue
        if name == "glossary_hit":
            hit = sum(len(v["detail"]["hits"]) for v in vals)
            tot = sum(len(v["detail"]["hits"]) + len(v["detail"]["misses"]) for v in vals)
            value = hit / tot
        elif name == "leakage":
            value = sum(v["score"] for v in vals) / len(vals)
        else:
            value = sum(1 for v in vals if v["passed"]) / len(vals)
        agg[name] = {"kind": kind, "value": round(value, 6), "n": len(vals)}
    return agg


def _review(rows: list[dict]) -> dict:
    """Guard outcome per case. Hard metrics cover accepted outputs only; routed cases are listed here."""
    by_rule: dict[str, int] = defaultdict(int)
    for r in rows:
        for rule in r["review_rules"]:
            by_rule[rule] += 1
    return {"n_cases": len(rows),
            "repaired": sum(r["repaired"] for r in rows), "repaired_ids": [r["id"] for r in rows if r["repaired"]],
            "routed_to_review": sum(r["routed_to_review"] for r in rows),
            "routed_ids": [r["id"] for r in rows if r["routed_to_review"]],
            "by_rule": dict(sorted(by_rule.items()))}


def _glossary_status(g: dict[str, GlossaryTerm]) -> str:
    named = [t for t in g.values() if t.keep_english]
    terms = [t for t in g.values() if not t.keep_english]
    v = sum(t.status == "verified-usage" for t in terms)
    return (f"{v} verified-usage, {len(terms) - v} proposed (usage-checked against Vietnamese sources, no workshop "
            f"sign-off needed), {len(named)} keep-English names")


def _timing(outputs: dict[str, Output]) -> dict:
    secs = [o.seconds for o in outputs.values()]
    total = round(sum(secs), 3)
    return {"total_seconds": total, "seconds_per_segment": round(total / len(secs), 3) if secs else 0.0}


def build_report(cases: list[EvalCase], outputs: dict[str, Output], glossary: dict[str, GlossaryTerm], *,
                 model: str = "", meta: Optional[dict] = None) -> dict:
    rows = []
    for c in cases:
        out = outputs.get(c.id) or Output(id=c.id, error="missing output")
        res = score_case(c, out, glossary)
        m = {k: {"score": None if v.score is None else round(v.score, 6), "passed": v.passed, "detail": v.detail}
             for k, v in res.items()}
        rows.append({"id": c.id, "domains": c.domains, "priority": c.priority, "severity": c.severity,
                     # a routed-to-review case is neither pass nor fail of the hard metrics: it is not accepted
                     "hard_pass": None if out.routed_to_review else all(m[h]["passed"] is not False for h in M.HARD),
                     "error": out.error, "repaired": out.repaired, "attempts": out.attempts,
                     "routed_to_review": out.routed_to_review, "review_rules": out.review_rules, "metrics": m})
    accepted = {i: o for i, o in outputs.items() if not o.routed_to_review}
    cons = M.consistency(cases, {i: o.output for i, o in accepted.items()}, glossary)

    def group(key_fn) -> dict:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for r, c in zip(rows, cases):
            for k in key_fn(c):
                buckets[k].append(r)
        return {k: {"n": len(v), "metrics": _aggregate(v)} for k, v in sorted(buckets.items())}

    by_term: dict[str, dict] = {}
    for r in rows:
        if r["routed_to_review"]:
            continue
        d = r["metrics"]["glossary_hit"]["detail"]
        for t in d.get("hits", []):
            by_term.setdefault(t, {"hit": 0, "total": 0})["hit"] += 1
            by_term[t]["total"] += 1
        for t in d.get("misses", []):
            by_term.setdefault(t, {"hit": 0, "total": 0})["total"] += 1

    cost = outputs and {
        "total_usd": round(sum(o.cost_usd for o in outputs.values()), 6),
        "tokens_in": sum(o.tokens_in for o in outputs.values()),
        "tokens_out": sum(o.tokens_out for o in outputs.values()),
        "cache_hits": sum(1 for o in outputs.values() if o.cache_hit),
    } or {}
    if cost:
        pages: dict[int, float] = defaultdict(float)
        for c in cases:
            pages[c.page] += outputs[c.id].cost_usd if c.id in outputs else 0.0
        cost["pages"] = len(pages)
        cost["cost_per_page_usd"] = round(cost["total_usd"] / len(pages), 6) if pages else 0.0
        cost["by_page"] = {str(k): round(v, 6) for k, v in sorted(pages.items())}
    return {
        "schema": 1, "model": model, "set": {"sha256": set_hash(cases), "n_cases": len(cases)},
        "glossary": {"sha256": glossary_hash(glossary), "n_terms": len(glossary),
                     "status": _glossary_status(glossary)},
        "meta": meta or {},
        "metrics": _aggregate(rows), "consistency": cons, "review": _review(rows),
        "domains": group(lambda c: c.domains), "priorities": group(lambda c: [c.priority]),
        "terms": dict(sorted(by_term.items())), "cost": cost, "timing": _timing(outputs),
        "leakage": {"overall_limit": M.LEAKAGE_MAX_OVERALL,
                    "cases": {r["id"]: r["metrics"]["leakage"]["detail"]["leaked"] for r in rows
                              if r["metrics"]["leakage"]["detail"].get("leaked") and not r["routed_to_review"]}},
        "cases": rows,
    }


# --- gate ---------------------------------------------------------------------------------------------
class GateResult(BaseModel):
    ok: bool
    failures: list[str] = []
    warnings: list[str] = []


def _worse_soft(name: str, cur: float, base: float, tol_pct: float) -> bool:
    if name in M.LOWER_IS_BETTER:
        return cur > base + tol_pct / 100
    return cur < base * (1 - tol_pct / 100) - EPS


def gate(current: dict, baseline: dict, soft_tolerance_pct: float = DEFAULT_SOFT_TOLERANCE_PCT) -> GateResult:
    """Fail on any drop of a hard metric (overall, per domain, per priority, per case) and on a drop of a soft
    metric larger than `soft_tolerance_pct` (relative; leakage: absolute percentage points of increase)."""
    fails: list[str] = []
    warns: list[str] = []
    if current["set"]["sha256"] != baseline["set"]["sha256"]:
        return GateResult(ok=False, failures=["eval set differs from the baseline's set: re-baseline by PR"])
    if current["glossary"]["sha256"] != baseline["glossary"]["sha256"]:
        warns.append("glossary changed since the baseline")

    def compare(scope: str, cur: dict, base: dict) -> None:
        for name in (*M.HARD, *M.SOFT):
            c, b = cur.get(name, {}), base.get(name, {})
            if c.get("value") is None or b.get("value") is None:
                if b.get("value") is not None and c.get("value") is None:
                    fails.append(f"{scope}: {name} no longer measured")
                continue
            if name in M.HARD:
                if c["value"] < b["value"] - EPS:
                    fails.append(f"{scope}: hard metric {name} dropped {b['value']:.4f} -> {c['value']:.4f}")
            elif _worse_soft(name, c["value"], b["value"], soft_tolerance_pct):
                fails.append(f"{scope}: soft metric {name} {b['value']:.4f} -> {c['value']:.4f} "
                             f"(tolerance {soft_tolerance_pct}%)")

    compare("overall", current["metrics"], baseline["metrics"])
    for section in ("domains", "priorities"):
        for k, v in current[section].items():
            if k in baseline[section]:
                compare(f"{section[:-1]} {k}", v["metrics"], baseline[section][k]["metrics"])
    b_cons, c_cons = baseline["consistency"].get("score"), current["consistency"].get("score")
    if b_cons is not None and c_cons is not None and _worse_soft("consistency", c_cons, b_cons, soft_tolerance_pct):
        fails.append(f"overall: soft metric consistency {b_cons:.4f} -> {c_cons:.4f}")
    leak = current["metrics"].get("leakage", {}).get("value")
    if leak is not None and leak > M.LEAKAGE_MAX_OVERALL + EPS:
        fails.append(f"overall: leakage {leak * 100:.2f}% above the {M.LEAKAGE_MAX_OVERALL * 100:.1f}% limit "
                     "(English words left in the translation)")
    for r in current["cases"]:
        words = r["metrics"]["leakage"]["detail"].get("leaked")
        if r["priority"] == "p0" and words and not r.get("routed_to_review"):
            fails.append(f"case {r['id']} (p0) leaves English words: {', '.join(words)}")
    base_pass = {r["id"]: r["hard_pass"] for r in baseline["cases"]}
    for r in current["cases"]:
        if base_pass.get(r["id"]) and r.get("routed_to_review"):
            fails.append(f"case {r['id']} ({r['priority']}) newly routed to review "
                         f"({', '.join(r['review_rules']) or 'guard'}): not accepted")
        elif base_pass.get(r["id"]) and r["hard_pass"] is False:
            bad = [h for h in M.HARD if r["metrics"][h]["passed"] is False]
            fails.append(f"case {r['id']} ({r['priority']}) newly fails hard metric(s): {', '.join(bad)}")
    return GateResult(ok=not fails, failures=fails, warnings=warns)


# --- markdown -------------------------------------------------------------------------------------------
def _fmt(m: dict) -> str:
    v = m["value"]
    return "n/a" if v is None else f"{v * 100:.1f}%"


def render_markdown(rep: dict, gate_result: Optional[GateResult] = None) -> str:
    L = [f"# Eval report: {rep['model'] or 'unknown model'}", "",
         f"Set `{rep['set']['sha256'][:12]}` ({rep['set']['n_cases']} cases). "
         f"Glossary `{rep['glossary']['sha256'][:12]}`: {rep['glossary']['status']}.", ""]
    if gate_result is not None:
        L += [f"## Gate: {'PASS' if gate_result.ok else 'FAIL'}", ""]
        L += [f"- FAIL {f}" for f in gate_result.failures] + [f"- warn {w}" for w in gate_result.warnings] + [""]
    L += ["## Overall", "", "| metric | kind | value | n |", "|---|---|---|---|"]
    for k, v in rep["metrics"].items():
        L.append(f"| {k} | {v['kind']} | {_fmt(v)} | {v['n']} |")
    c = rep["consistency"]
    L += ["", f"Consistency (same EN term -> same VI term): "
              f"{'n/a' if c['score'] is None else f'{c['score'] * 100:.1f}%'} "
              f"({len(c['inconsistent'])} inconsistent of {c['terms_seen']} terms)", ""]
    if c.get("mixed_forms"):
        L += ["Terms rendered with more than one accepted form (allowed, but pick one for the final manual): "
              + "; ".join(f"{m['term']} ({' / '.join(m['forms'])})" for m in c["mixed_forms"][:15]), ""]
    lk = rep.get("leakage") or {}
    if lk.get("cases"):
        L += ["English words left in the output (limit "
              f"{lk['overall_limit'] * 100:.1f}% overall, 0 in p0):", ""]
        L += [f"- {i}: {', '.join(w)}" for i, w in list(lk["cases"].items())[:30]] + [""]
    tm = rep.get("timing") or {}
    if tm.get("total_seconds"):
        L += [f"Time: {tm['total_seconds']:.1f} s total, {tm['seconds_per_segment']:.2f} s per segment.", ""]
    for section, title in (("priorities", "By priority"), ("domains", "By domain")):
        L += [f"## {title}", "", "| " + section[:-1] + " | n | " + " | ".join(M.HARD + M.SOFT) + " |",
              "|---|---|" + "---|" * len(M.HARD + M.SOFT)]
        for k, v in rep[section].items():
            L.append(f"| {k} | {v['n']} | " + " | ".join(_fmt(v["metrics"][m]) for m in M.HARD + M.SOFT) + " |")
        L.append("")
    worst = sorted(((v["hit"] / v["total"], k, v) for k, v in rep["terms"].items() if v["hit"] < v["total"]))[:15]
    if worst:
        L += ["## Glossary terms with misses (proposed VI terms)", "", "| term | hit | total |", "|---|---|---|"]
        L += [f"| {k} | {v['hit']} | {v['total']} |" for _, k, v in worst] + [""]
    rv = rep.get("review") or {}
    L += ["## Guard and fix loop", "",
          "Hard metrics above are computed on the FINAL ACCEPTED output (after the guard + fix loop shared with the "
          "pipeline). Cases routed to review are not accepted and are excluded from those denominators; they are "
          "listed here, never counted as a pass.", "",
          f"- Repaired by the fix loop: {rv.get('repaired', 0)}"
          + (f" ({', '.join(rv['repaired_ids'])})" if rv.get("repaired_ids") else ""),
          f"- Routed to review: {rv.get('routed_to_review', 0)} of {rv.get('n_cases', len(rep['cases']))}"
          + (f" ({', '.join(rv['routed_ids'][:30])}{' ...' if len(rv['routed_ids']) > 30 else ''})"
             if rv.get("routed_ids") else ""),
          ] + ([f"- Failing rules: {rv['by_rule']}"] if rv.get("by_rule") else []) + [""]
    failing = [r for r in rep["cases"] if r["hard_pass"] is False]
    L += [f"## Cases failing a hard metric ({len(failing)})", ""]
    for r in failing[:50]:
        bad = [f"{h}: {json.dumps(r['metrics'][h]['detail'], ensure_ascii=False)}" for h in M.HARD
               if r["metrics"][h]["passed"] is False]
        L.append(f"- {r['id']} [{r['priority']}/{r['severity']}] " + "; ".join(bad))
    if rep["cost"]:
        k = rep["cost"]
        L += ["", "## Cost", "", f"Total ${k['total_usd']:.4f}, {k['pages']} pages, "
              f"${k['cost_per_page_usd']:.4f} per page, tokens in/out {k['tokens_in']}/{k['tokens_out']}, "
              f"cache hits {k['cache_hits']}."]
    return "\n".join(L) + "\n"
