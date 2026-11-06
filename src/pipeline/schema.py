"""Job file formats. Documented in docs/job-format.md; the review web app reads segments.json."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

Status = Literal["ok", "warn", "error", "needs_review"]
Kind = Literal["prose", "step", "signal", "table", "label"]
KINDS: tuple[str, ...] = ("prose", "step", "signal", "table", "label")

# Pipeline file names inside a job dir.
CHUNKS_FILE = "chunks.json"        # input: list[Chunk]
SEGMENTS_FILE = "segments.json"    # output for the web app: list[Segment]
TRACE_FILE = "trace.jsonl"
DB_FILE = "job.sqlite"             # chunk table, results, meta
LEDGER_FILE = "ledger.sqlite"      # step ledger (idempotency guard for paid calls)
CHECKPOINT_FILE = "checkpoints.sqlite"
CACHE_FILE = "llm_cache.sqlite"


class Chunk(BaseModel):
    """One translation unit coming out of intake."""

    id: str
    page: int
    section: str = ""
    source: str
    kind: Optional[Kind] = None  # classify fills it when missing

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("empty chunk id")
        return v


class Segment(BaseModel):
    id: str
    page: int
    section: str = ""
    source: str
    target: str = ""
    status: Status
    issues: list[str] = Field(default_factory=list)
    risk: float = Field(0.0, ge=0.0, le=1.0)
    kind: Kind = "prose"
