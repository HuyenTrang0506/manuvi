import pytest


@pytest.fixture(autouse=True)
def _aws_env(monkeypatch):
    for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test",
                 "AWS_DEFAULT_REGION": "ap-southeast-2"}.items():
        monkeypatch.setenv(k, v)
