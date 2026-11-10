"""`manuvi eval` runs the same guard + fix loop as the pipeline; repaired / routed-to-review are reported apart."""
import json
from pathlib import Path

import pytest

from src.eval import metrics as M
from src.eval.cases import load_cases, load_glossary
from src.eval.runner import make_recorded_fixture, run_eval
from src.guard.masking import load_patterns
from src.guard.segment import prepare
from src.guard import load_signal_table
from src.llm_gateway import Gateway
from src.llm_gateway.adapters import Adapter

CASES = {c.id: c for c in load_cases()}
G = load_glossary()
FX = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "q1_real_failures.json").read_text("utf-8"))


class Replay(Adapter):
    """Answers each case with its (masked, signal-stripped) human reference, except the scripted failing cases,
    which replay the recorded real-model failures call by call."""
    name = "fake"
    data_classes = frozenset({"public", "customer"})

    def __init__(self, scripted: dict[str, str]):
        self.scripted = {cid: iter(FX[key]["calls"]) for cid, key in scripted.items()}
        self.calls = 0

    def complete(self, req, model_id):
        self.calls += 1
        if req.target_id in self.scripted:
            return {"text": next(self.scripted[req.target_id]), "tokens_in": 1, "tokens_out": 1}
        case = CASES[req.target_id]
        prep = prepare(case.source, load_patterns(), load_signal_table())
        ref = case.reference_vi
        if prep.prefix:
            ref = ref[len(prep.prefix):]
        for ph, val in prep.mapping.items():
            ref = ref.replace(val, ph, 1)
        return {"text": ref, "tokens_in": 1, "tokens_out": 1}


def gw(tmp_path, scripted):
    return Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "dev"},
                   adapters={"fake": Replay(scripted)})


@pytest.fixture(scope="module")
def baseline(tmp_path_factory):
    d = tmp_path_factory.mktemp("b")
    p = d / "ref.jsonl"
    make_recorded_fixture(list(CASES.values()), G, p)
    return run_eval(model="recorded", fixture=p, out_root=d, run_id="base").run_dir / "report.json"


def test_repaired_cases_are_counted_and_scored_on_final_output(tmp_path, baseline):
    r = run_eval(model="fake", gateway=gw(tmp_path, {"EL-004": "EL-004", "EL-005": "EL-005"}), out_root=tmp_path,
                 run_id="a", baseline=baseline)
    rev = r.report["review"]
    assert rev["repaired"] == 2 and rev["routed_to_review"] == 0 and rev["repaired_ids"] == ["EL-004", "EL-005"]
    for m in ("protected_codes", "number_unit", "safety_signals"):
        assert r.report["metrics"][m]["value"] == 1.0 and r.report["metrics"][m]["n"] >= 1
    row = {c["id"]: c for c in r.report["cases"]}
    assert row["EL-004"]["repaired"] and row["EL-004"]["attempts"] == 1 and not row["EL-004"]["routed_to_review"]
    assert not row["EL-001"]["repaired"] and row["EL-001"]["attempts"] == 0
    out = {json.loads(l)["id"]: json.loads(l) for l in (tmp_path / "a" / "results.jsonl").read_text("utf-8").splitlines()}
    assert "远" not in out["EL-004"]["output"] and out["EL-004"]["output"].startswith("NGUY HIỂM: ")
    assert out["EL-005"]["output"].startswith("THẬN TRỌNG: Siết ")
    assert r.ok   # repaired output equals the references: gate passes
    md = (tmp_path / "a" / "report.md").read_text("utf-8")
    assert "Repaired by the fix loop: 2" in md and "Routed to review: 0" in md


def test_routed_cases_are_reported_separately_and_not_counted_as_pass(tmp_path, baseline):
    r = run_eval(model="fake", gateway=gw(tmp_path, {"EL-004": "EL-004-never-repaired"}), out_root=tmp_path,
                 run_id="b", baseline=baseline)
    rev = r.report["review"]
    assert rev["routed_to_review"] == 1 and rev["routed_ids"] == ["EL-004"] and rev["repaired"] == 0
    assert "SCR001" in rev["by_rule"]
    n_base = json.loads(baseline.read_text("utf-8"))["metrics"]["protected_codes"]["n"]
    assert r.report["metrics"]["protected_codes"]["n"] == n_base - 1         # excluded from the denominator
    assert r.report["metrics"]["protected_codes"]["value"] == 1.0            # ...but never silently "passed":
    row = {c["id"]: c for c in r.report["cases"]}["EL-004"]
    assert row["routed_to_review"] and row["hard_pass"] is None and row["attempts"] == 2
    assert not r.ok and any("EL-004" in f and "routed" in f for f in r.gate.failures)
    md = (tmp_path / "b" / "report.md").read_text("utf-8")
    assert "Routed to review: 1" in md and "EL-004" in md
    out = {json.loads(l)["id"]: json.loads(l) for l in (tmp_path / "b" / "results.jsonl").read_text("utf-8").splitlines()}
    assert out["EL-004"]["output"] == "" and out["EL-004"]["routed_to_review"]   # failed output is not shipped


def test_safety_signals_metric_checks_final_text_independently():
    c = CASES["EL-004"]
    assert M.safety_signals(c, "Ắc quy sinh ra khí nổ. Điện áp là 24 V.").passed is False   # no VI signal at all
    assert M.safety_signals(c, "NGUY HIỂM: Ắc quy sinh ra khí nổ. Điện áp là 24 V.").passed is True
    assert M.safety_signals(c, "CẢNH BÁO: Ắc quy sinh ra khí nổ. 24 V.").passed is False     # wrong level


def test_fake_default_routes_everything_and_fails_gate(tmp_path, baseline):
    r = run_eval(model="fake", out_root=tmp_path, run_id="f", baseline=baseline)
    assert r.report["review"]["routed_to_review"] == len(CASES) and not r.ok
