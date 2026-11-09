from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
from typing import Optional

import httpx

from .cache import cache_key
from .errors import (CallTimeout, NotConfigured, RecordedMiss, RequestRejected, ServerError, Throttled,
                     TransientError)
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


_THROTTLE = {"ThrottlingException", "TooManyRequestsException", "ServiceQuotaExceededException",
             "ProvisionedThroughputExceededException", "RequestLimitExceeded"}
_SERVER = {"InternalServerException", "ServiceUnavailableException", "ModelNotReadyException",
           "ModelErrorException", "InternalFailure", "ServiceUnavailable"}


def _to_converse(messages: list[dict], json_mode: bool) -> tuple[list[dict], list[dict]]:
    """OpenAI-style messages -> (system blocks, Converse messages). Images arrive as data: URLs."""
    system: list[dict] = []
    out: list[dict] = []
    for m in messages:
        role, content = m.get("role"), m.get("content", "")
        parts = content if isinstance(content, list) else [{"type": "text", "text": content}]
        blocks: list[dict] = []
        for p in parts:
            if p.get("type") == "image_url":
                url = p["image_url"]["url"]
                head, _, b64 = url.partition(",")
                fmt = head.split(";")[0].split("/")[-1].replace("jpg", "jpeg") or "png"
                blocks.append({"image": {"format": fmt, "source": {"bytes": base64.b64decode(b64)}}})
            else:
                blocks.append({"text": p.get("text", "")})
        if role == "system":
            system += [b for b in blocks if "text" in b]
        else:
            out.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
    if json_mode:
        system.append({"text": "Respond with valid JSON only."})
    return system, out


class BedrockAdapter(Adapter):
    """Bedrock Runtime Converse API. Unusable unless `enabled` (the gateway sets it only when
    allow_paid is true and the job and month caps are > 0). The boto3 client is created lazily from
    MANUVI_AWS_PROFILE / MANUVI_AWS_REGION; no keys are read or stored here."""

    name = "bedrock"
    paid = True
    DEFAULT_MAX_TOKENS = 1024  # same figure the gateway uses to reserve budget

    def __init__(self, customer_data: bool = False, enabled: bool = False, client=None, env=None):
        # customer data must be opted in explicitly (profiles.yaml customer_data_adapters)
        if customer_data:
            self.data_classes = frozenset({"public", "customer"})
        self.enabled, self._client, self._env = enabled, client, env

    def _get_client(self):
        if self._client is None:
            from .aws import make_client
            self._client = make_client("bedrock-runtime", self._env, read_timeout=60)
        return self._client

    def complete(self, req, model_id):
        if not self.enabled:
            raise NotConfigured("bedrock is disabled: needs allow_paid=true and job/month caps > 0")
        from botocore.exceptions import (BotoCoreError, ClientError, ConnectionError as BotoConnErr,
                                         NoCredentialsError, NoRegionError, ParamValidationError,
                                         ProfileNotFound, ReadTimeoutError)

        system, messages = _to_converse(req.messages, bool(req.response_format))
        kw = {"modelId": model_id, "messages": messages,
              "inferenceConfig": {"maxTokens": req.max_tokens or self.DEFAULT_MAX_TOKENS,
                                  "temperature": req.temperature}}
        if system:
            kw["system"] = system
        try:
            r = self._get_client().converse(**kw)
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            status = e.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
            if code in _THROTTLE or status == 429:
                raise Throttled(code or "429") from None
            if code == "ModelTimeoutException" or status == 408:
                raise CallTimeout(code) from None
            if code in _SERVER or status >= 500:
                raise ServerError(code or str(status)) from None
            hint = {"AccessDeniedException": " (check IAM permissions and model access in the Bedrock console)",
                    "ValidationException": " (check model id, region and request size)",
                    "ResourceNotFoundException": " (model id not available in this region)"}.get(code, "")
            raise RequestRejected(f"bedrock rejected the request: {code}{hint}") from None
        except ParamValidationError as e:
            raise RequestRejected(f"invalid request parameters: {type(e).__name__}") from None
        except ReadTimeoutError as e:
            raise CallTimeout(type(e).__name__) from None
        except (NoCredentialsError, NoRegionError, ProfileNotFound) as e:
            raise NotConfigured(f"AWS credentials not available ({type(e).__name__}); run `manuvi aws doctor`") from None
        except BotoConnErr as e:
            raise TransientError(type(e).__name__) from None
        except BotoCoreError as e:
            raise TransientError(type(e).__name__) from None
        blocks = r.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks)
        usage = r.get("usage", {})
        return {
            "text": text,
            "tokens_in": usage.get("inputTokens") or estimate_tokens(req.messages),
            "tokens_out": usage.get("outputTokens") or estimate_tokens(text),
        }
