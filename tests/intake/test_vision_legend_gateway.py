"""vision_legend must reach the model only through the gateway (alias vision, customer data)."""
import json
import urllib.request

import pymupdf
import pytest

from src.llm_gateway import Gateway
from src.llm_gateway.adapters import OllamaAdapter
from src.llm_gateway.errors import DataClassViolation
from src.translate import vision_legend as VL

from .vision_helpers import ScriptedVLM, make_gateway, raster_label

ANSWER = json.dumps({"figure_summary_vi": "Sơ đồ đèn", "labels": [
    {"en": "CAB WORK LIGHT", "vi": "Đèn làm việc cabin", "kind": "component"}]})


@pytest.fixture
def ws(tmp_path):
    w = tmp_path / "doc"
    (w / "images").mkdir(parents=True)
    (w / "images" / "fig.png").write_bytes(raster_label("CAB WORK LIGHT", 600, 300, 40))
    return w


def job():
    return {"figure_id": "fig-43-1", "figure_number": "43-1", "caption_en": "", "image_path": "images/fig.png",
            "bytes": 1000}


def test_legend_goes_through_gateway_and_needs_no_api_key(ws, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    def no_network(*a, **k):
        raise AssertionError("direct HTTP call")
    monkeypatch.setattr(urllib.request, "urlopen", no_network)
    vlm = ScriptedVLM(raw=ANSWER)
    res = VL.process_one(ws, job(), gateway=make_gateway(tmp_path, vlm), sleep_s=0)
    assert res.error is None and [l.vi for l in res.labels] == ["Đèn làm việc cabin"]
    req = vlm.requests[0]
    assert req.model_alias == "vision" and req.data_class == "customer"
    assert any(p.get("type") == "image_url" for p in req.messages[1]["content"])
    assert res.model == "fake-vision"


def test_second_run_is_cached_by_the_gateway(ws, tmp_path):
    vlm = ScriptedVLM(raw=ANSWER)
    gw = make_gateway(tmp_path, vlm)
    VL.process_one(ws, job(), gateway=gw, sleep_s=0, force=True)
    VL.process_one(ws, job(), gateway=gw, sleep_s=0, force=True)
    assert vlm.calls == 1


def test_free_hosted_model_is_refused_for_customer_images(ws, tmp_path):
    remote = OllamaAdapter(base_url="https://hosted.example.com/v1")
    gw = Gateway(cache_path=tmp_path / "c.sqlite", env={"MANUVI_PROFILE": "qa"}, adapters={"ollama": remote})
    with pytest.raises(DataClassViolation):
        VL.process_one(ws, job(), gateway=gw, sleep_s=0)
    assert not VL.result_path(ws, "fig-43-1").exists()      # a refusal is not stored as a figure error
