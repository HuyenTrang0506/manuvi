import json

import pytest

from src.eval import judge as J
from src.llm_gateway import DataClassViolation, FakeAdapter, Gateway
from src.llm_gateway.adapters import Adapter


class HostedFree(Adapter):
    """Stands in for a free hosted tier (e.g. a Gemini free key): public data only, the Adapter default."""
    name = "hostedfree"
    calls = 0

    def complete(self, req, model_id):
        HostedFree.calls += 1
        return {"text": "{}", "tokens_in": 1, "tokens_out": 1}


class JudgeStub(FakeAdapter):
    def __init__(self, payload):
        super().__init__()
        self.payload = payload

    def complete(self, req, model_id):
        self.calls += 1
        return {"text": json.dumps(self.payload), "tokens_in": 100, "tokens_out": 30}


GOOD = {"terminology": 5, "fluency": 4, "completeness": 5, "safety": 5, "blocking_issues": [], "verdict": "pass"}


def gw(tmp_path, adapter, name="fake", model="strong-judge"):
    return Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "dev", "MANUVI_MODEL_STRONG": f"{name}:{model}"},
                   adapters={name: adapter})


def test_judge_refuses_free_hosted_adapter_for_customer_data(tmp_path):
    HostedFree.calls = 0
    g = gw(tmp_path, HostedFree(), name="hostedfree", model="gemini-free")
    with pytest.raises(DataClassViolation):
        J.judge(g, "Turn the key switch to ON.", "Xoay công tắc khóa điện sang ON.", ["key switch => công tắc khóa điện"], job_id="j")
    assert HostedFree.calls == 0  # refused before any network call


def test_request_is_always_customer_class():
    r = J.build_judge_request("a", "b", [], job_id="j")
    assert r.data_class == "customer" and r.model_alias == "strong" and r.temperature == 0
    import inspect
    assert "data_class" not in inspect.signature(J.build_judge_request).parameters


def test_judge_runs_on_private_adapter_and_parses(tmp_path):
    g = gw(tmp_path, JudgeStub(GOOD))
    v, cost = J.judge(g, "src", "tgt", [], job_id="j")
    assert v.effective_verdict() == "pass" and cost == 0.0


def test_invalid_json_gives_none():
    assert J.parse_verdict("not json") is None
    assert J.parse_verdict(json.dumps({**GOOD, "safety": 9})) is None
    assert J.parse_verdict("```json\n" + json.dumps(GOOD) + "\n```") is not None


def test_effective_verdict_is_never_more_lenient_than_scores():
    v = J.JudgeVerdict(**{**GOOD, "safety": 2})
    assert v.effective_verdict() == "fail"
    v = J.JudgeVerdict(**{**GOOD, "fluency": 3})
    assert v.effective_verdict() == "needs_review"
    v = J.JudgeVerdict(**{**GOOD, "blocking_issues": [{"dimension": "safety", "description": "warning dropped"}]})
    assert v.effective_verdict() == "fail"


def test_schema_and_rubric_cover_the_four_dimensions():
    assert set(J.JUDGE_SCHEMA["required"]) >= set(J.DIMENSIONS)
    assert all(d in J.RUBRIC for d in J.DIMENSIONS)


def test_cohen_kappa():
    assert J.cohen_kappa(["pass", "fail", "pass", "fail"], ["pass", "fail", "pass", "fail"]) == 1.0
    assert abs(J.cohen_kappa(["a", "a", "b", "b"], ["a", "b", "a", "b"])) < 1e-9
    assert 0.3 < J.cohen_kappa(["p", "p", "p", "f", "f", "p"], ["p", "p", "f", "f", "f", "p"]) < 0.8
    with pytest.raises(ValueError):
        J.cohen_kappa([], [])
