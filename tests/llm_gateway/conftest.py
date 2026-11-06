import pytest


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("MANUVI_PROFILE", "MANUVI_ALLOW_PAID", "MANUVI_MODEL_BULK",
              "MANUVI_MODEL_STRONG", "MANUVI_MODEL_VISION", "MANUVI_LLM_CALL_LOG"):
        monkeypatch.delenv(k, raising=False)
