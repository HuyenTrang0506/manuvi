"""BL-US-30: per-node trace records. Local JSONL always; Langfuse exporter is OFF by default.

Records never contain customer text: only ids, counts, hashes and cost figures.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Optional, Protocol

ALLOWED_KEYS = {
    "ts", "job_id", "chunk_id", "node", "stage", "tokens_in", "tokens_out", "cost_usd", "latency_ms",
    "node_latency_ms", "prompt_version", "model", "cache_hit", "ledger", "input_hash", "output_hash",
    "input_len", "output_len", "attempt", "status", "issues",
}


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def sanitize(rec: dict[str, Any]) -> dict[str, Any]:
    """Allow-list filter. Anything not listed (e.g. a stray 'text' field) is dropped."""
    return {k: v for k, v in rec.items() if k in ALLOWED_KEYS}


class TraceExporter(Protocol):
    def export(self, rec: dict[str, Any]) -> None: ...


class JsonlExporter:
    def __init__(self, path: Path):
        self.path = Path(path)

    def export(self, rec: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(sanitize(rec), ensure_ascii=False) + "\n")


class LangfuseExporter:
    """Sends the sanitized record (hashes, lengths, cost) as a Langfuse event. Never sends text.

    Not enabled unless asked for (`--langfuse` or MANUVI_LANGFUSE=1). The langfuse package is imported
    lazily; pass `client` to inject a stub. The client call (`create_event`) follows the v3 SDK and has
    not been run against a live Langfuse server.
    """

    def __init__(self, client: Any = None):
        if client is None:
            try:
                from langfuse import Langfuse  # type: ignore
            except ImportError as e:  # pragma: no cover
                raise RuntimeError("langfuse is not installed; Langfuse export stays off") from e
            client = Langfuse()
        self.client = client

    def export(self, rec: dict[str, Any]) -> None:
        clean = sanitize(rec)
        self.client.create_event(name=f"pipeline.{clean.get('node', 'node')}", metadata=clean)


class S3Exporter:
    """Uploads masked records to S3 (SSE) when MANUVI_S3_BUCKET is set. Off by default: only added by
    `s3_enabled()` (MANUVI_S3_TRACE=1). One object per job, rewritten from a buffer every `flush_every`."""

    def __init__(self, job_id: str, sink: Any = None, flush_every: int = 50):
        from ..common.s3_sink import S3Sink
        self.job_id, self.sink, self.flush_every = job_id, sink or S3Sink(), flush_every
        self.buf: list[dict[str, Any]] = []

    def export(self, rec: dict[str, Any]) -> None:
        if not self.sink.enabled:
            return
        self.buf.append(sanitize(rec))
        if len(self.buf) % self.flush_every == 0:
            self.flush()

    def flush(self) -> None:
        if self.buf:
            self.sink.upload_trace(self.job_id, self.buf)


def s3_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return env.get("MANUVI_S3_TRACE", "").strip().lower() in ("1", "true", "yes", "on") and bool(env.get("MANUVI_S3_BUCKET"))


def langfuse_enabled(env: Optional[dict] = None) -> bool:
    env = os.environ if env is None else env
    return env.get("MANUVI_LANGFUSE", "").strip().lower() in ("1", "true", "yes", "on")


class Tracer:
    def __init__(self, job_id: str, exporters: list[TraceExporter]):
        self.job_id, self.exporters = job_id, exporters

    def record(self, node: str, chunk_id: str, started: float, **fields: Any) -> None:
        rec = {"ts": time.time(), "job_id": self.job_id, "chunk_id": chunk_id, "node": node,
               "node_latency_ms": round((time.perf_counter() - started) * 1000, 3), **fields}
        for ex in self.exporters:
            try:
                ex.export(rec)
            except Exception:  # tracing must never break a job
                if isinstance(ex, JsonlExporter):
                    raise
