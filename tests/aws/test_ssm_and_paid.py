import logging

import pytest
from botocore.exceptions import EndpointConnectionError, NoCredentialsError

from src.llm_gateway import Gateway, LLMRequest, NotConfigured
from src.llm_gateway.config import SsmConfigSource, resolve_config

from .conftest import FakeSsm

P = "/manuvi/prod/"
PAID = {P + "allow_paid": "true", P + "cap/job_usd": "2", P + "cap/month_usd": "10",
        P + "model/bulk": "bedrock:amazon.nova-lite-v1:0"}


def cfg(params, env=None, ttl=300, clock=None):
    src = SsmConfigSource(client=FakeSsm(params), ttl=ttl, **({"clock": clock} if clock else {}))
    return resolve_config({"MANUVI_PROFILE": "prod", **(env or {})}, src), src


@pytest.mark.parametrize("exc", [NoCredentialsError(), EndpointConnectionError(endpoint_url="https://ssm.x")])
def test_unreachable_falls_back_to_yaml(exc):
    src = SsmConfigSource(client=FakeSsm(exc=exc))
    c = resolve_config({"MANUVI_PROFILE": "qa"}, src)
    assert c.models["bulk"] == "ollama:qwen2.5:7b" and not c.allow_paid  # yaml values
    assert not src.reachable and src.last_error == type(exc).__name__


def test_missing_boto3_credentials_never_crash_dev(monkeypatch):
    # a real client with no creds anywhere: error is swallowed, yaml wins
    monkeypatch.delenv("AWS_ACCESS_KEY_ID")
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    src = SsmConfigSource(env={"MANUVI_AWS_REGION": "us-east-1"})
    assert resolve_config({"MANUVI_PROFILE": "dev"}, src).models["bulk"] == "fake:fake-bulk"


def test_ttl_cache_five_minutes():
    t = [0.0]
    fake = FakeSsm({P + "model/strong": "ollama:qwen2.5:14b"})
    src = SsmConfigSource(client=fake, clock=lambda: t[0])
    for _ in range(3):
        resolve_config({"MANUVI_PROFILE": "prod"}, src)
    assert fake.calls == 1
    t[0] = 301
    resolve_config({"MANUVI_PROFILE": "prod"}, src)
    assert fake.calls == 2


def test_ssm_values_override_yaml_but_env_wins():
    c, _ = cfg({P + "model/strong": "ollama:qwen2.5:14b", P + "model/vision": "none"},
               env={"MANUVI_MODEL_STRONG": "fake:x"})
    assert c.models["strong"] == "fake:x" and c.models["vision"] == "none"
    with pytest.raises(NotConfigured, match="disabled"):
        c.split("vision")


def test_invalid_values_ignored():
    c, _ = cfg({P + "model/bulk": "garbage with spaces", P + "model/strong": "rm -rf /",
                P + "cap/job_usd": "-3", P + "cap/month_usd": "nan", P + "allow_paid": "yes"})
    assert c.models["bulk"] == "bedrock:placeholder-bulk-model"  # yaml kept
    assert (c.cap_job_usd, c.cap_month_usd) == (5, 20)


def test_paid_models_from_ssm_need_allow_paid_and_caps():
    c, _ = cfg(PAID)
    assert c.allow_paid and c.caps_ok() and c.models["bulk"] == "bedrock:amazon.nova-lite-v1:0"
    for drop in ({P + "allow_paid": "false"}, {P + "cap/job_usd": "0"}, {P + "cap/month_usd": "0"}):
        c, _ = cfg({**PAID, **drop})
        assert not c.allow_paid or not c.caps_ok()
        assert c.models["bulk"] == "bedrock:placeholder-bulk-model"  # SSM bedrock model not applied
    only_model = {P + "model/bulk": "bedrock:amazon.nova-lite-v1:0"}
    c, _ = cfg(only_model)
    assert c.models["bulk"] == "bedrock:placeholder-bulk-model"


def test_ssm_allow_paid_true_without_caps_stays_off():
    c = resolve_config({"MANUVI_PROFILE": "qa"},
                       SsmConfigSource(client=FakeSsm({"/manuvi/qa/allow_paid": "true"})))
    assert c.allow_paid is False


def req(**kw):
    d = dict(messages=[{"role": "user", "content": "hi"}], job_id="j1", prompt_version="v1",
             source_hash="h", data_class="public", max_tokens=50)
    return LLMRequest(**{**d, **kw})


@pytest.mark.parametrize("params", [
    {P + "allow_paid": "false", P + "cap/job_usd": "2", P + "cap/month_usd": "10"},
    {P + "allow_paid": "true", P + "cap/job_usd": "0", P + "cap/month_usd": "10"},
])
def test_gateway_blocks_bedrock_without_allow_paid_or_caps(tmp_path, params, monkeypatch):
    # prod yaml says bedrock + allow_paid; SSM revokes it. The boto client must never be created.
    import src.llm_gateway.aws as aws
    monkeypatch.setattr(aws, "make_client", lambda *a, **k: pytest.fail("client created"))
    g = Gateway(cache_path=tmp_path / "c.db", env={"MANUVI_PROFILE": "prod"},
                ssm=SsmConfigSource(client=FakeSsm(params)))
    with pytest.raises(NotConfigured):
        g.complete(req())


def test_unverified_price_warns_once_and_uses_multiplier(caplog):
    from src.llm_gateway import Prices
    pr = Prices()
    with caplog.at_level(logging.WARNING):
        c1 = pr.cost("amazon.nova-lite-v1:0", 1000, 1000)
        pr.cost("amazon.nova-lite-v1:0", 1000, 1000)
    assert c1 == pytest.approx((0.00006 + 0.00024) * 1.5)
    assert sum("unverified" in r.message for r in caplog.records) == 1
    assert all(v["verified"] is False and "bedrock/pricing" in v["source"] for v in pr.table.values())


def test_price_without_verified_key_is_unverified(tmp_path):
    from src.llm_gateway import Prices
    f = tmp_path / "p.yaml"
    f.write_text("models:\n  m: {input_per_1k: 1.0, output_per_1k: 1.0}\n  v: {input_per_1k: 1.0, output_per_1k: 1.0, verified: true}\n")
    pr = Prices(f)
    assert pr.cost("m", 1000, 0) == 1.5 and pr.cost("v", 1000, 0) == 1.0
