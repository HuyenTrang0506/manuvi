import boto3
from botocore.exceptions import NoCredentialsError
from moto import mock_aws
from typer.testing import CliRunner

from src.llm_gateway.doctor import exit_code, render, run_doctor
from src.manuvi_cli import app

ENV = {"MANUVI_PROFILE": "qa", "MANUVI_AWS_REGION": "us-east-1", "MANUVI_S3_BUCKET": "doc-bucket"}
ACCOUNT = "123456789012"  # moto's default account


def factory(service):
    return boto3.client(service, region_name="us-east-1")


def put_params(ssm, paid="false", job="0", month="0"):
    for k, v in {"model/bulk": "ollama:qwen2.5:7b", "model/strong": "ollama:qwen2.5:14b", "model/vision": "none",
                 "allow_paid": paid, "cap/job_usd": job, "cap/month_usd": month}.items():
        ssm.put_parameter(Name=f"/manuvi/qa/{k}", Value=v, Type="String")


@mock_aws
def test_all_ok_and_no_secrets():
    ssm, s3 = factory("ssm"), factory("s3")
    put_params(ssm)
    s3.create_bucket(Bucket="doc-bucket")
    s3.put_public_access_block(Bucket="doc-bucket", PublicAccessBlockConfiguration={
        "BlockPublicAcls": True, "IgnorePublicAcls": True, "BlockPublicPolicy": True, "RestrictPublicBuckets": True})
    checks = run_doctor(ENV, factory)
    out = render(checks)
    assert exit_code(checks) == 0, out
    assert "9012" in out and ACCOUNT not in out
    assert "testing" not in out  # the dummy secret key / access key id
    assert "free mode" in out


@mock_aws
def test_failures_have_fix_and_nonzero_exit():
    s3 = factory("s3")
    s3.create_bucket(Bucket="doc-bucket")  # no public access block, no SSM params
    checks = run_doctor({**ENV, "MANUVI_AWS_REGION": ""}, factory)
    out = render(checks)
    assert exit_code(checks) == 1
    assert "fix:" in out and "Block Public Access" in out and "MANUVI_AWS_REGION" in out
    assert ACCOUNT not in out


def test_no_credentials_stops_early():
    class Sts:
        def get_caller_identity(self):
            raise NoCredentialsError()
    checks = run_doctor(ENV, lambda s: Sts())
    out = render(checks)
    assert exit_code(checks) == 1 and "sso login" in out and "NoCredentialsError" in out


@mock_aws
def test_paid_enabled_is_flagged_and_inconsistent_state_fails():
    ssm = factory("ssm")
    put_params(ssm, paid="true", job="2", month="10")
    out = render(run_doctor({"MANUVI_PROFILE": "qa", "MANUVI_AWS_REGION": "us-east-1"}, factory))
    assert "PAID ENABLED" in out
    # allow_paid true with caps 0 is neutralised by the safety rule (reads as free mode)
    ssm.put_parameter(Name="/manuvi/qa/cap/job_usd", Value="0", Type="String", Overwrite=True)
    assert "free mode" in render(run_doctor({"MANUVI_PROFILE": "qa", "MANUVI_AWS_REGION": "us-east-1"}, factory))


@mock_aws
def test_cli_runs_offline(monkeypatch):
    for k, v in ENV.items():
        monkeypatch.setenv(k, v)
    import src.llm_gateway.aws as aws
    monkeypatch.setattr(aws, "make_client", lambda service, env=None, **kw: factory(service))
    factory("s3").create_bucket(Bucket="doc-bucket")
    r = CliRunner().invoke(app, ["aws", "doctor"])
    assert r.exit_code == 1 and "[FAIL] s3 bucket" in r.output and "9012" in r.output and ACCOUNT not in r.output


@mock_aws
def test_access_denied_on_public_access_block_is_warn(monkeypatch):
    from botocore.exceptions import ClientError
    s3 = factory("s3")
    s3.create_bucket(Bucket="doc-bucket")
    def denied(**kw):
        raise ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetPublicAccessBlock")
    monkeypatch.setattr(s3, "get_public_access_block", denied)
    checks = run_doctor(ENV, lambda svc: s3 if svc == "s3" else factory(svc))
    bucket = [c for c in checks if c.name == "s3 bucket"][0]
    assert bucket.status == "warn"
