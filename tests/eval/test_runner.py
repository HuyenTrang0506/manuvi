import json

import pytest
from typer.testing import CliRunner

from src.cli import app
from src.eval.cases import DEFAULT_GLOSSARY, load_cases, load_glossary
from src.eval.runner import make_recorded_fixture, mask_reference, run_eval
from src.guard.masking import mask, load_patterns
from src.llm_gateway import DataClassViolation, FakeAdapter, Gateway
from src.llm_gateway.adapters import Adapter

CASES = load_cases()
G = load_glossary()
runner = CliRunner()


@pytest.fixture(scope="module")
def fixture_path(tmp_path_factory):
    p = tmp_path_factory.mktemp("fx") / "ref.jsonl"
    assert make_recorded_fixture(CASES, G, p) == len(CASES)
    return p


@pytest.fixture(scope="module")
def baseline(tmp_path_factory, fixture_path):
    out = tmp_path_factory.mktemp("base")
    r = run_eval(model="recorded", fixture=fixture_path, out_root=out, run_id="base")
    return r.run_dir / "report.json"


def test_mask_reference_roundtrips_placeholders():
    c = next(c for c in CASES if "F23" in c.protected and "15 A" in c.protected)
    _, mapping = mask(c.source, load_patterns())
    masked = mask_reference(c.reference_vi, mapping)
    assert all(ph in masked for ph in mapping)


def test_recorded_replay_of_references_is_perfect(baseline):
    rep = json.loads(baseline.read_text(encoding="utf-8"))
    for m in ("protected_codes", "number_unit", "safety_signals", "glossary_hit", "placeholder_roundtrip"):
        assert rep["metrics"][m]["value"] == 1.0, (m, rep["metrics"][m])
    assert rep["consistency"]["score"] == 1.0


def test_run_dir_artifacts(tmp_path, fixture_path):
    r = run_eval(model="recorded", fixture=fixture_path, out_root=tmp_path, run_id="r1")
    for f in ("results.jsonl", "report.json", "report.md"):
        assert (tmp_path / "r1" / f).exists()
    assert r.report["cost"]["pages"] == len({c.page for c in CASES})
    assert r.report["cost"]["total_usd"] == 0


def test_fake_adapter_default_fails_the_gate(tmp_path, baseline):
    r = run_eval(model="fake", out_root=tmp_path, run_id="f", baseline=baseline)
    assert r.gate is not None and not r.gate.ok and not r.ok
    assert any("protected_codes" in f for f in r.gate.failures)
    assert (tmp_path / "f" / "gate.json").exists()


def test_recorded_replay_passes_gate(tmp_path, baseline, fixture_path):
    r = run_eval(model="recorded", fixture=fixture_path, out_root=tmp_path, run_id="again", baseline=baseline)
    assert r.ok, r.gate.failures


def test_recorded_miss_is_recorded_not_fatal(tmp_path):
    p = tmp_path / "empty.jsonl"
    p.write_text("")
    r = run_eval(model="recorded", fixture=p, out_root=tmp_path, run_id="m")
    assert r.report["metrics"]["protected_codes"]["value"] < 1.0
    assert all(c["error"].startswith("RecordedMiss") for c in r.report["cases"])


def test_hypotheses_scoring(tmp_path):
    hyp = tmp_path / "h.jsonl"
    hyp.write_text("".join(json.dumps({"id": c.id, "vi": c.reference_vi}, ensure_ascii=False) + "\n" for c in CASES))
    r = run_eval(hypotheses=hyp, out_root=tmp_path, run_id="h")
    assert r.report["metrics"]["safety_signals"]["value"] == 1.0
    assert r.report["metrics"]["placeholder_roundtrip"]["value"] is None  # no masked output to check


class PublicOnly(Adapter):
    name = "hostedfree"
    n = 0

    def complete(self, req, model_id):
        PublicOnly.n += 1
        return {"text": "x", "tokens_in": 1, "tokens_out": 1}


def test_customer_data_never_reaches_free_hosted_adapter(tmp_path):
    gw = Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "dev", "MANUVI_MODEL_BULK": "hostedfree:m"},
                 adapters={"hostedfree": PublicOnly()})
    with pytest.raises(DataClassViolation):
        run_eval(gateway=gw, out_root=tmp_path, run_id="x")
    assert PublicOnly.n == 0


def test_cli_run_and_exit_codes(tmp_path, baseline, fixture_path):
    ok = runner.invoke(app, ["eval", "run", "--model", "recorded", "--fixture", str(fixture_path), "--out", str(tmp_path),
                             "--baseline", str(baseline), "--run-id", "c1"])
    assert ok.exit_code == 0 and "GATE PASS" in ok.output
    bad = runner.invoke(app, ["eval", "run", "--out", str(tmp_path), "--baseline", str(baseline), "--run-id", "c2"])
    assert bad.exit_code == 1 and "GATE FAIL" in bad.output


def test_cli_verify_and_glossary_template(tmp_path):
    assert runner.invoke(app, ["eval", "verify"]).exit_code == 0
    x = tmp_path / "t.xlsx"
    assert runner.invoke(app, ["eval", "glossary-template", str(x)]).exit_code == 0
    from openpyxl import load_workbook
    wb = load_workbook(x)
    assert "NOTHING is approved" in wb["README"]["A2"].value
    rows = list(wb["terms"].iter_rows(values_only=True))
    assert rows[0][2] == "status" and {r[2] for r in rows[1:]} == {"proposed"}


def test_committed_baseline_matches_reference_replay(tmp_path, fixture_path):
    from pathlib import Path
    b = Path(__file__).resolve().parents[2] / "src/eval/baselines/electrical_v1.recorded-reference.json"
    r = run_eval(model="recorded", fixture=fixture_path, out_root=tmp_path, run_id="ci", baseline=b)
    assert r.ok, r.gate.failures
