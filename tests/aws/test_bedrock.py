import boto3
import pytest
from botocore.stub import Stubber

from src.llm_gateway import (BedrockAdapter, DataClassViolation, Gateway, LLMRequest, NotConfigured,
                             Prices, RequestRejected, Throttled)

PROD_PAID = {"MANUVI_PROFILE": "prod", "MANUVI_ALLOW_PAID": "true",
             "MANUVI_MODEL_BULK": "bedrock:amazon.nova-lite-v1:0"}


def req(**kw):
    d = dict(messages=[{"role": "system", "content": "be terse"}, {"role": "user", "content": "hi"}],
             job_id="j1", prompt_version="v1", source_hash="h", data_class="public", max_tokens=50)
    return LLMRequest(**{**d, **kw})


def ok_response(tin=1000, tout=500, text="xin chao"):
    return {"output": {"message": {"role": "assistant", "content": [{"text": text}]}},
            "stopReason": "end_turn", "usage": {"inputTokens": tin, "outputTokens": tout, "totalTokens": tin + tout},
            "metrics": {"latencyMs": 5}}


def make(tmp_path, stub_fn, env=None, prices=None, customer=True):
    c = boto3.client("bedrock-runtime", region_name="us-east-1")
    st = Stubber(c)
    stub_fn(st)
    st.activate()
    ad = BedrockAdapter(customer_data=customer, enabled=True, client=c)
    g = Gateway(cache_path=tmp_path / "c.db", env=env or PROD_PAID, adapters={"bedrock": ad},
                prices=prices, sleep=lambda s: None, max_retries=3)
    return g, st


def verified_prices(tmp_path):
    p = tmp_path / "prices.yaml"
    p.write_text("models:\n  amazon.nova-lite-v1:0: {input_per_1k: 0.1, output_per_1k: 0.2, verified: true}\n")
    return Prices(p)


def test_throttling_retried_by_gateway_then_usage_and_cost(tmp_path):
    def stubs(st):
        for _ in range(2):
            st.add_client_error("converse", "ThrottlingException", http_status_code=429)
        st.add_response("converse", ok_response(), expected_params={
            "modelId": "amazon.nova-lite-v1:0",
            "system": [{"text": "be terse"}],
            "messages": [{"role": "user", "content": [{"text": "hi"}]}],
            "inferenceConfig": {"maxTokens": 50, "temperature": 0.0}})
    g, st = make(tmp_path, stubs, prices=verified_prices(tmp_path))
    r = g.complete(req())
    st.assert_no_pending_responses()
    assert (r.text, r.tokens_in, r.tokens_out) == ("xin chao", 1000, 500)
    assert r.cost_usd == pytest.approx(1000 / 1000 * 0.1 + 500 / 1000 * 0.2)
    assert g.job_spent_usd("j1") == pytest.approx(r.cost_usd)  # reservation settled to actual


def test_5xx_is_transient_and_gives_up_after_retries(tmp_path):
    from src.llm_gateway import ServerError
    def stubs(st):
        for _ in range(4):
            st.add_client_error("converse", "InternalServerException", http_status_code=500)
    g, st = make(tmp_path, stubs, prices=verified_prices(tmp_path))
    with pytest.raises(ServerError):
        g.complete(req())
    assert g.job_spent_usd("j1") == 0.0  # reservation released


def test_validation_and_access_errors_not_retried(tmp_path):
    for code in ("ValidationException", "AccessDeniedException"):
        def stubs(st, code=code):
            st.add_client_error("converse", code, "secret-arn arn:aws:iam::123456789012:role/x", 400)
        g, st = make(tmp_path, stubs, prices=verified_prices(tmp_path))
        with pytest.raises(RequestRejected) as e:
            g.complete(req(source_hash=code))
        assert code in str(e.value) and "123456789012" not in str(e.value)
        st.assert_no_pending_responses()  # exactly one call was made


def test_unverified_price_cost_guard_x1_5(tmp_path):
    g, _ = make(tmp_path, lambda st: st.add_response("converse", ok_response()))  # default prices.yaml
    r = g.complete(req())
    assert r.cost_usd == pytest.approx((1.0 * 0.00006 + 0.5 * 0.00024) * 1.5)


def test_customer_data_refused_without_opt_in(tmp_path):
    # no injected adapter: the gateway builds one from profile config; dev profile has no opt-in
    env = {"MANUVI_MODEL_BULK": "bedrock:amazon.nova-lite-v1:0", "MANUVI_ALLOW_PAID": "true"}
    g = Gateway(cache_path=tmp_path / "c.db", env=env)
    with pytest.raises(DataClassViolation):
        g.complete(req(data_class="customer"))
    # the adapter itself is closed to customer data by default
    assert BedrockAdapter(enabled=True).data_classes == frozenset({"public"})


def test_injected_adapter_without_opt_in_refuses_customer_data(tmp_path):
    g, _ = make(tmp_path, lambda st: st.add_response("converse", ok_response()), prices=verified_prices(tmp_path),
                customer=False)
    # injected adapter without opt-in still refuses customer data
    with pytest.raises(DataClassViolation):
        g.complete(req(data_class="customer"))
    assert BedrockAdapter(customer_data=True).data_classes == frozenset({"public", "customer"})


def test_adapter_disabled_by_default():
    with pytest.raises(NotConfigured):
        BedrockAdapter(client=object()).complete(req(), "amazon.nova-lite-v1:0")


def test_dev_profile_never_creates_boto_client(tmp_path, monkeypatch):
    import src.llm_gateway.aws as aws
    monkeypatch.setattr(aws, "make_client", lambda *a, **k: pytest.fail("client created"))
    g = Gateway(cache_path=tmp_path / "c.db", env={"MANUVI_PROFILE": "dev"})
    assert g.complete(req()).cost_usd == 0.0


def test_client_has_timeouts_and_no_botocore_retries():
    from src.llm_gateway.aws import make_client
    c = make_client("bedrock-runtime", {"MANUVI_AWS_REGION": "us-east-1"})
    cfg = c.meta.config
    assert cfg.read_timeout == 60 and cfg.retries.get("total_max_attempts") == 1  # = no retries


def test_param_validation_error_is_rejected_not_transient():
    from botocore.exceptions import ParamValidationError
    class C:
        def converse(self, **kw):
            raise ParamValidationError(report="bad")
    with pytest.raises(RequestRejected):
        BedrockAdapter(enabled=True, client=C()).complete(req(), "amazon.nova-lite-v1:0")
