import boto3
import pytest

PROFILES = "tests/aws/_unused"  # profiles.yaml from the repo is used


@pytest.fixture(autouse=True)
def _offline_env(monkeypatch, tmp_path):
    for k in ("MANUVI_PROFILE", "MANUVI_ALLOW_PAID", "MANUVI_MODEL_BULK", "MANUVI_MODEL_STRONG",
              "MANUVI_MODEL_VISION", "MANUVI_LLM_CALL_LOG", "MANUVI_AWS_REGION", "MANUVI_AWS_PROFILE",
              "MANUVI_S3_BUCKET", "MANUVI_S3_TRACE"):
        monkeypatch.delenv(k, raising=False)
    # dummy test-only credentials so moto/Stubber clients can sign; nothing here reaches AWS
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "no-config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "no-creds"))


def client(service):
    return boto3.client(service, region_name="us-east-1")


class FakeSsm:
    """Stands in for the SSM client: returns fixed params or raises."""

    def __init__(self, params=None, exc=None):
        self.params, self.exc, self.calls = params or {}, exc, 0

    def get_parameters_by_path(self, **kw):
        self.calls += 1
        if self.exc:
            raise self.exc
        return {"Parameters": [{"Name": k, "Value": v} for k, v in self.params.items()
                               if k.startswith(kw["Path"])]}
