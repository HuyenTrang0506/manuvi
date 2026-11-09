import json

import boto3
import pytest
from moto import mock_aws

from src.common.s3_sink import S3Sink, mask_record
from src.pipeline.tracing import S3Exporter, s3_enabled

BUCKET = "manuvi-test-bucket"
SECRET_TEXT = "Replace the hydraulic pump relief valve YT12-3456 before starting"


def rec(**kw):
    return {"ts": 1.0, "job_id": "j1", "chunk_id": "C001", "node": "translate", "tokens_in": 10,
            "cost_usd": 0.0, "input_hash": "abc123", "status": "ok", **kw}


def test_mask_drops_text_and_unknown_fields():
    m = mask_record(rec(text=SECRET_TEXT, source=SECRET_TEXT, issues=[SECRET_TEXT], note=SECRET_TEXT,
                        node=SECRET_TEXT, status="x" * 500))
    assert SECRET_TEXT not in json.dumps(m)
    assert set(m) == {"ts", "job_id", "chunk_id", "tokens_in", "cost_usd", "input_hash"}


@mock_aws
def test_upload_only_masked_records_with_encryption():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    sink = S3Sink(bucket=BUCKET, client=s3)
    assert sink.upload_trace("j1", [rec(text=SECRET_TEXT, translation="Thay van"), rec(chunk_id="C002")])
    obj = s3.get_object(Bucket=BUCKET, Key="jobs/j1/trace.jsonl")
    body = obj["Body"].read().decode()
    assert obj["ServerSideEncryption"] == "AES256"
    assert "YT12" not in body and "translation" not in body and "C002" in body
    assert len(body.splitlines()) == 2


@mock_aws
def test_exporter_sanitizes_before_upload():
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=BUCKET)
    ex = S3Exporter("j1", S3Sink(bucket=BUCKET, client=s3), flush_every=1)
    ex.export(rec(text=SECRET_TEXT))
    assert SECRET_TEXT not in s3.get_object(Bucket=BUCKET, Key="jobs/j1/trace.jsonl")["Body"].read().decode()


def test_skip_when_not_configured():
    class Boom:
        def __getattr__(self, n):
            raise AssertionError("client used")
    sink = S3Sink(env={}, client=Boom())
    assert not sink.enabled
    assert sink.upload_trace("j1", [rec()]) is False
    assert sink.upload_artifact("j1", "cost.json", b"{}") is False
    assert not s3_enabled({}) and not s3_enabled({"MANUVI_S3_TRACE": "1"})  # needs bucket too
    assert s3_enabled({"MANUVI_S3_TRACE": "1", "MANUVI_S3_BUCKET": "b"})


@mock_aws
def test_upload_failure_does_not_raise_and_bad_names_rejected():
    s3 = boto3.client("s3", region_name="us-east-1")  # bucket does not exist
    sink = S3Sink(bucket="missing-bucket", client=s3)
    assert sink.upload_trace("j1", [rec()]) is False and sink.last_error
    with pytest.raises(ValueError):
        sink.upload_artifact("j1", "../x", b"")
