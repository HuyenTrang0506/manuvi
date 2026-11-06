from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Optional

import httpx

from .cache import cache_key
from .errors import CallTimeout, NotConfigured, RecordedMiss, ServerError, Throttled, TransientError
from .models import LLMRequest


def estimate_tokens(obj) -> int:
    s = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return max(1, (len(s) + 3) // 4)


class Adapter:
    name = "base"
    paid = False
    # data classes this adapter may receive; default is public only (opt in explicitly)
    data_classes = frozenset({"public"})

    def complete(self, req: LLMRequest, model_id: str) -> dict:
        """Return {"text", "tokens_in", "tokens_out"}."""
        raise NotImplementedError


class FakeAdapter(Adapter):
    name = "fake"

    def __init__(self, data_classes=("public", "customer"), paid: bool = False):
        self.data_classes = frozenset(data_classes)
        self.paid = paid
        self.calls = 0

    def complete(self, req, model_id):
        self.calls += 1
        digest = hashlib.sha256(
            json.dumps(req.messages, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()[:12]
        text = f"[{model_id}] {digest}"
        return {
            "text": text,
            "tokens_in": estimate_tokens(req.messages),
            "tokens_out": estimate_tokens(text),
        }


class RecordedAdapter(Adapter):
    """Replays responses by cache key from a JSONL file: {"key","text","tokens_in","tokens_out"}."""

    name = "recorded"
    data_classes = frozenset({"public", "customer"})  # local replay, no network

    def __init__(self, fixture_path):
        self.entries: dict[str, dict] = {}
        for line in Path(fixture_path).read_text().splitlines():
            if line.strip():
                e = json.loads(line)
                self.entries[e["key"]] = e

    def complete(self, req, model_id):
        key = cache_key(req, model_id)
        e = self.entries.get(key)
        if e is None:
            raise RecordedMiss(f"no recorded response for key {key}")
        return {
            "text": e["text"],
            "tokens_in": e.get("tokens_in", 0),
            "tokens_out": e.get("tokens_out", 0),
        }


class OllamaAdapter(Adapter):
    """OpenAI-compatible local server. Local only, so customer data is allowed on localhost."""

    name = "ollama"

    def __init__(self, base_url: str = "http://localhost:11434/v1",
                 client: Optional[httpx.Client] = None, timeout: float = 120.0):
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        host = httpx.URL(self.base_url).host
        if host in ("localhost", "127.0.0.1", "::1"):
            self.data_classes = frozenset({"public", "customer"})

    def complete(self, req, model_id):
        body = {"model": model_id, "messages": req.messages, "temperature": req.temperature}
        if req.max_tokens:
            body["max_tokens"] = req.max_tokens
        if req.response_format:
            body["response_format"] = req.response_format
        try:
            r = self.client.post(f"{self.base_url}/chat/completions", json=body)
        except httpx.TimeoutException as e:
            raise CallTimeout(str(e)) from e
        except httpx.TransportError as e:  # ConnectError etc.
            raise TransientError(f"{type(e).__name__}: {e}") from e
        if r.status_code == 429:
            raise Throttled("429")
        if r.status_code >= 500:
            raise ServerError(str(r.status_code))
        r.raise_for_status()
        j = r.json()
        usage = j.get("usage", {})
        text = j["choices"][0]["message"]["content"]
        return {
            "text": text,
            "tokens_in": usage.get("prompt_tokens") or estimate_tokens(req.messages),
            "tokens_out": usage.get("completion_tokens") or estimate_tokens(text),
        }


class BedrockAdapter(Adapter):
    """Stub: no AWS calls are wired yet. allow_paid is enforced by the gateway only."""

    name = "bedrock"
    paid = True

    def __init__(self, customer_data: bool = False):
        # customer data must be opted in explicitly (profiles.yaml customer_data_adapters)
        if customer_data:
            self.data_classes = frozenset({"public", "customer"})

    def complete(self, req, model_id):
        raise NotConfigured("bedrock adapter is a stub (no boto3 client wired)")
