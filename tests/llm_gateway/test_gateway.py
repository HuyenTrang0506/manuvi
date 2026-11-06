import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

from src.llm_gateway import (BedrockAdapter, BudgetExceeded, DataClassViolation, FakeAdapter,
                             FakeParameterStore, Gateway, LLMRequest, NotConfigured,
                             OllamaAdapter, RecordedAdapter, RecordedMiss, Throttled,
                             cache_key, resolve_config)
from src.llm_gateway.adapters import Adapter
from src.llm_gateway.errors import ServerError

ROOT = Path(__file__).resolve().parents[2]


def req(job="j1", text="hello", dc="public", alias="bulk", **kw):
    return LLMRequest(messages=[{"role": "user", "content": text}], model_alias=alias,
                      data_class=dc, job_id=job, target_id="t", prompt_version="v1",
                      source_hash="h1", **kw)


class PaidFake(FakeAdapter):
    def __init__(self):
        super().__init__(paid=True)


def gw(tmp_path, env=None, **kw):
    kw.setdefault("sleep", lambda s: None)
    return Gateway(tmp_path / "c.db", env=env if env is not None else {}, **kw)


def paid_env(**extra):
    return {"MANUVI_PROFILE": "prod", "MANUVI_MODEL_BULK": "paid:placeholder-bulk-model", **extra}


def test_profiles_and_precedence():
    assert resolve_config({}).profile == "dev"
    c = resolve_config({})
    assert not c.allow_paid and c.cap_run_usd == 0
    assert resolve_config({"MANUVI_PROFILE": "qa"}).cap_run_usd == 1
    p = resolve_config({"MANUVI_PROFILE": "prod"})
    assert (p.cap_job_usd, p.cap_month_usd) == (5, 20)
    ssm = FakeParameterStore({"/manuvi/dev/model/bulk": "fake:from-ssm",
                              "/manuvi/dev/model/strong": "fake:ssm-strong"})
    assert resolve_config({}, ssm).models["bulk"] == "fake:from-ssm"
    c = resolve_config({"MANUVI_MODEL_BULK": "fake:from-env"}, ssm)
    assert c.models["bulk"] == "fake:from-env" and c.models["strong"] == "fake:ssm-strong"
    assert resolve_config({"MANUVI_ALLOW_PAID": "true"}).allow_paid


def test_fake_deterministic_and_cache_hit_costs_zero(tmp_path):
    g = gw(tmp_path)
    a = g.complete(req())
    b = g.complete(req())
    assert not a.cache_hit and b.cache_hit and b.cost_usd == 0 and a.text == b.text
    assert g.adapters["fake"].calls == 1


def test_paid_cache_hit_costs_zero_but_first_call_pays(tmp_path):
    g = gw(tmp_path, env=paid_env(), adapters={"paid": PaidFake()})
    first = g.complete(req(dc="customer"))
    second = g.complete(req(dc="customer"))
    assert first.cost_usd > 0 and not first.cache_hit
    assert second.cache_hit and second.cost_usd == 0
    assert g.run_spent_usd == pytest.approx(first.cost_usd)


def test_caps_stop_before_call(tmp_path):
    fake = PaidFake()
    # dev profile: cap $0, paid refused/blocked before any call
    g = gw(tmp_path, env={"MANUVI_MODEL_BULK": "paid:placeholder-bulk-model",
                          "MANUVI_ALLOW_PAID": "1"}, adapters={"paid": fake})
    with pytest.raises(BudgetExceeded):
        g.complete(req())
    assert fake.calls == 0
    # allow_paid false -> refused too
    g2 = gw(tmp_path, env={"MANUVI_MODEL_BULK": "paid:placeholder-bulk-model"},
            adapters={"paid": fake})
    with pytest.raises(NotConfigured):
        g2.complete(req())
    assert fake.calls == 0


def test_job_cap_stops_second_call(tmp_path):
    fake = PaidFake()
    g = gw(tmp_path, env=paid_env(), adapters={"paid": fake})
    g.start_job("j1")
    big = "x" * 4000
    g.store.add_spend("x", 0)
    g.store.settle(["job:j1"], 0, 0)
    g.store.reserve([("job:j1", None)], 4.9999)
    with pytest.raises(BudgetExceeded):
        g.complete(req(text=big, max_tokens=2000))
    assert fake.calls == 0


def test_month_cap(tmp_path):
    fake = PaidFake()
    g = gw(tmp_path, env=paid_env(), adapters={"paid": fake})
    g.store.add_spend(g._month(), 19.9999)
    with pytest.raises(BudgetExceeded):
        g.complete(req())
    assert fake.calls == 0


def test_free_hosted_adapter_refuses_customer_data_no_retry(tmp_path):
    hosted = FakeAdapter(data_classes={"public"})
    g = gw(tmp_path, adapters={"fake": hosted})
    assert g.complete(req(dc="public")).text
    with pytest.raises(DataClassViolation):
        g.complete(req(dc="customer", text="secret"))
    assert hosted.calls == 1  # not retried, not called


def test_remote_ollama_public_only():
    assert OllamaAdapter("https://hosted.example/v1").data_classes == frozenset({"public"})
    assert "customer" in OllamaAdapter().data_classes
    assert Adapter.data_classes == frozenset({"public"})
    assert RecordedAdapter.data_classes == frozenset({"public"})
    assert BedrockAdapter().data_classes == frozenset({"public"})
    assert "customer" in BedrockAdapter(customer_data=True).data_classes


def test_env_change_applies_to_next_job_not_running_job(tmp_path):
    env = {}
    other = FakeAdapter()
    g = gw(tmp_path, env=env, adapters={"other": other})
    g.start_job("A")
    env["MANUVI_MODEL_BULK"] = "other:model-x"
    g.start_job("B")
    ra = g.complete(req(job="A", text="same"))
    rb = g.complete(req(job="B", text="same"))
    assert ra.model_id == "fake-bulk" and rb.model_id == "model-x"
    assert other.calls == 1
    assert g.jobs["A"].config.models["bulk"] == "fake:fake-bulk"


def test_recorded_adapter_replay(tmp_path):
    r = req(text="recorded", dc="public")
    key = cache_key(r, "fake-bulk")
    fx = tmp_path / "fx.jsonl"
    fx.write_text(json.dumps({"key": key, "text": "xin chao", "tokens_in": 3, "tokens_out": 2}) + "\n")
    g = gw(tmp_path, adapters={"fake": RecordedAdapter(fx)})
    assert g.complete(r).text == "xin chao"
    with pytest.raises(RecordedMiss):
        g.complete(req(text="unknown", dc="public"))


class Flaky(Adapter):
    def __init__(self, fails, exc):
        self.fails, self.exc, self.calls = fails, exc, 0

    def complete(self, req, model_id):
        self.calls += 1
        if self.calls <= self.fails:
            raise self.exc("boom")
        return {"text": "ok", "tokens_in": 1, "tokens_out": 1}


def test_retries_with_backoff(tmp_path):
    sleeps = []
    flaky = Flaky(2, Throttled)
    g = gw(tmp_path, adapters={"fake": flaky}, sleep=sleeps.append)
    assert g.complete(req()).text == "ok"
    assert flaky.calls == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.0


def test_retries_exhausted(tmp_path):
    flaky = Flaky(99, ServerError)
    g = gw(tmp_path, adapters={"fake": flaky})
    with pytest.raises(ServerError):
        g.complete(req())
    assert flaky.calls == 4  # 1 + 3 retries


def test_ollama_http_mock_and_5xx_retry(tmp_path):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": "xin"}}],
                                         "usage": {"prompt_tokens": 5, "completion_tokens": 1}})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    g = gw(tmp_path, env={"MANUVI_MODEL_BULK": "ollama:m"},
           adapters={"ollama": OllamaAdapter(client=client)})
    r = g.complete(req(dc="customer"))
    assert r.text == "xin" and r.tokens_in == 5 and calls["n"] == 2


def test_bedrock_stub_not_configured(tmp_path):
    env = {"MANUVI_PROFILE": "prod"}
    g = gw(tmp_path, env=env)
    with pytest.raises(NotConfigured):
        g.complete(req())
    g2 = gw(tmp_path, env={"MANUVI_PROFILE": "prod",
                                                       "MANUVI_ALLOW_PAID": "0"})
    with pytest.raises(NotConfigured):
        g2.complete(req())


def test_ci_guard_script(tmp_path):
    log = tmp_path / "calls.jsonl"
    script = ROOT / "scripts" / "check_no_uncached_paid_calls.py"
    g = gw(tmp_path, env=paid_env(), adapters={"paid": PaidFake()}, call_log_path=log)
    g.complete(req())
    run = lambda p: subprocess.run([sys.executable, str(script), str(p)], capture_output=True)
    assert run(log).returncode == 1  # paid uncached call recorded
    log.write_text(json.dumps({"paid": True, "cache_hit": True}) + "\n"
                   + json.dumps({"paid": False, "cache_hit": False}) + "\n")
    assert run(log).returncode == 0
    assert run(tmp_path / "missing.jsonl").returncode == 0


def test_cache_key_changes_with_prompt_version_and_model_id():
    r = req()
    assert cache_key(r, "m1") != cache_key(r, "m2")
    r2 = r.model_copy(update={"prompt_version": "v2"})
    assert cache_key(r, "m1") != cache_key(r2, "m1")
    assert cache_key(r, "m1") == cache_key(r.model_copy(), "m1")


def test_month_spend_persists_across_gateways(tmp_path):
    g = gw(tmp_path, env=paid_env(), adapters={"paid": PaidFake()})
    cost = g.complete(req(text="a")).cost_usd
    g2 = gw(tmp_path, env=paid_env(), adapters={"paid": PaidFake()})
    assert g2.store.month_spend(g2._month()) == pytest.approx(cost)
    assert g2.run_spent_usd == 0.0


def test_prod_allow_paid_with_bedrock_stub_releases_reservation(tmp_path):
    g = gw(tmp_path, env={"MANUVI_PROFILE": "prod", "MANUVI_ALLOW_PAID": "true"})
    with pytest.raises(NotConfigured):
        g.complete(req())
    assert g.store.month_spend(g._month()) == 0.0
    assert g.job_spent_usd("j1") == 0.0
    # customer data to bedrock is allowed only via profile opt-in (prod has it)
    assert g.jobs["j1"].config.customer_data_adapters == ("bedrock",)


def test_bedrock_customer_data_blocked_without_profile_opt_in(tmp_path):
    env = {"MANUVI_MODEL_BULK": "bedrock:placeholder-bulk-model", "MANUVI_ALLOW_PAID": "1"}
    g = gw(tmp_path, env=env)
    with pytest.raises(DataClassViolation):
        g.complete(req(dc="customer"))


def test_concurrent_reservations_cannot_both_pass_cap(tmp_path):
    import threading
    from src.llm_gateway.cache import SqliteStore
    store = SqliteStore(tmp_path / "s.db")
    barrier, results = threading.Barrier(2), []

    def worker():
        barrier.wait()
        try:
            store.reserve([("job:x", 1.0)], 0.6)
            results.append("ok")
        except BudgetExceeded:
            results.append("denied")

    ts = [threading.Thread(target=worker) for _ in range(2)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert sorted(results) == ["denied", "ok"]
    assert store.spent("job:x") == pytest.approx(0.6)


def test_failed_paid_call_releases_reservation(tmp_path):
    class Boom(PaidFake):
        def complete(self, req, model_id):
            raise ServerError("x")
    g = gw(tmp_path, env=paid_env(), adapters={"paid": Boom()})
    with pytest.raises(ServerError):
        g.complete(req())
    assert g.job_spent_usd("j1") == 0.0


def test_connect_error_is_retried(tmp_path):
    n = {"c": 0}

    def handler(request):
        n["c"] += 1
        if n["c"] < 3:
            raise httpx.ConnectError("refused")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    g = gw(tmp_path, env={"MANUVI_MODEL_BULK": "ollama:m"},
           adapters={"ollama": OllamaAdapter(client=client)})
    assert g.complete(req()).text == "ok" and n["c"] == 3


def test_default_cache_path_under_workspace(monkeypatch, tmp_path):
    import src.llm_gateway.gateway as gm
    monkeypatch.setattr(gm, "ROOT", tmp_path)
    Gateway(env={})
    assert (tmp_path / "workspace" / "llm_cache.sqlite").exists()
