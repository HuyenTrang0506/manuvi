"""Optional S3 sink for trace JSONL and job artifacts. Off unless MANUVI_S3_BUCKET is set.

Only masked trace records leave the machine: an allow-list of ids, counts, hashes and cost figures, with
short scalar values only. Source text, translations and customer text are never uploaded. Uploads use
server-side encryption. Without a bucket (or boto3/credentials) every call is a no-op.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Mapping, Optional

log = logging.getLogger(__name__)

SAFE_KEYS = {
    "ts", "job_id", "chunk_id", "node", "stage", "tokens_in", "tokens_out", "cost_usd", "latency_ms",
    "node_latency_ms", "prompt_version", "model", "cache_hit", "ledger", "input_hash", "output_hash",
    "input_len", "output_len", "attempt", "status",
}
_MAX_STR = 64
_SAFE_STR = re.compile(r"^[A-Za-z0-9_.:/#@+-]*$")  # ids, hashes, model names; no spaces, so no prose


def mask_record(rec: Mapping[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in rec.items():
        if k not in SAFE_KEYS:
            continue
        if isinstance(v, str):
            if len(v) > _MAX_STR or not _SAFE_STR.match(v):
                continue
        elif v is not None and not isinstance(v, (int, float, bool)):
            continue
        out[k] = v
    return out


class S3Sink:
    def __init__(self, bucket: Optional[str] = None, client: Any = None,
                 env: Optional[Mapping[str, str]] = None, prefix: str = "jobs"):
        self.env = os.environ if env is None else env
        self.bucket = bucket if bucket is not None else (self.env.get("MANUVI_S3_BUCKET") or None)
        self._client, self.prefix = client, prefix.strip("/")
        self.last_error: Optional[str] = None

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)

    def _get_client(self):
        if self._client is None:
            from ..llm_gateway.aws import make_client
            self._client = make_client("s3", self.env)
        return self._client

    def _put(self, key: str, body: bytes, content_type: str) -> bool:
        if not self.enabled:
            return False
        try:
            self._get_client().put_object(Bucket=self.bucket, Key=f"{self.prefix}/{key}", Body=body,
                                          ContentType=content_type, ServerSideEncryption="AES256")
            self.last_error = None
            return True
        except Exception as e:  # never break a job because of the sink
            self.last_error = type(e).__name__
            log.warning("S3 upload skipped (%s)", self.last_error)
            return False

    def upload_trace(self, job_id: str, records: list[Mapping[str, Any]]) -> bool:
        if not self.enabled:
            return False
        body = "".join(json.dumps(mask_record(r), ensure_ascii=False) + "\n" for r in records)
        return self._put(f"{job_id}/trace.jsonl", body.encode("utf-8"), "application/x-ndjson")

    def upload_artifact(self, job_id: str, name: str, data: bytes) -> bool:
        """Caller decides what is an artifact; pass only files that carry no customer text
        (e.g. cost reports, status.json). Path separators in `name` are rejected."""
        if "/" in name or "\\" in name or name.startswith("."):
            raise ValueError("artifact name must be a plain file name")
        return self._put(f"{job_id}/artifacts/{name}", data, "application/octet-stream")
