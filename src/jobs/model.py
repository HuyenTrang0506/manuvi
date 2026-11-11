"""Job model and states. A job is described by two small JSON files in the store:

* ``job.json``    (JobRecord): written by the web app once at submit time, plus the reviewer flag.
* ``status.json`` (JobStatus): written only by the worker (state, progress, cost, error code).
* ``cancel.json``: marker written by the web app; the worker stops at the next heartbeat.

Splitting them means the web app and the worker never overwrite each other's fields.
Status never carries source or target text, only counts and short error codes.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

JOB_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,40}$")
SECTION_RE = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class JobState(str, Enum):
    queued = "queued"
    running = "running"
    needs_review = "needs_review"  # waiting at a human gate (safety or batch)
    done = "done"
    failed = "failed"
    cancelled = "cancelled"


TERMINAL = frozenset({JobState.done, JobState.failed, JobState.cancelled})

# allowed transitions (a worker may re-run the same state, e.g. running -> running on resume)
TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.queued: frozenset({JobState.running, JobState.cancelled, JobState.failed}),
    JobState.running: frozenset({JobState.running, JobState.needs_review, JobState.done, JobState.failed,
                                 JobState.cancelled, JobState.queued}),
    JobState.needs_review: frozenset({JobState.running, JobState.done, JobState.failed, JobState.cancelled,
                                      JobState.queued}),
    JobState.done: frozenset(),
    JobState.failed: frozenset({JobState.queued}),  # manual retry
    JobState.cancelled: frozenset(),
}


class InvalidTransition(Exception):
    pass


def check_transition(old: JobState, new: JobState) -> None:
    if old != new and new not in TRANSITIONS[old]:
        raise InvalidTransition(f"{old.value} -> {new.value}")


def valid_job_id(job_id: str) -> str:
    if not isinstance(job_id, str) or not JOB_ID_RE.match(job_id):
        raise ValueError("bad job id")
    return job_id


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobRecord(BaseModel):
    id: str
    name: str = Field(max_length=120)
    owner: str
    created_at: str = Field(default_factory=utcnow)
    sections: str                                   # "23:1-42,43:1-22"
    page_map: dict[str, int] = Field(default_factory=dict)
    pdf_name: str = "input.pdf"
    format: str = "pdf"                             # pdf | md | docx | pptx (src/formats); non-PDF input is stored as input.<format>
    pdf_bytes: int = 0
    reviewer_can_review: bool = False               # founder allows the reviewer to read segments and approve non-safety ones
    reviewer_can_read: bool = False                 # founder also allows the reviewer to download outputs (default: no)
    priority_sections: list[str] = Field(default_factory=list)  # worked first; comes from the job spec, never hard-coded
    submitted: bool = False                         # False = draft (PDF may still be uploading), never queued

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        return valid_job_id(v)


class JobStatus(BaseModel):
    state: JobState = JobState.queued
    updated_at: str = Field(default_factory=utcnow)
    worker: str = ""
    attempts: int = 0
    pages: int = 0
    chunks: int = 0
    pending: int = 0
    by_status: dict[str, int] = Field(default_factory=dict)
    calls: int = 0
    reused: int = 0
    cost_usd: float = 0.0
    error_code: Optional[str] = None                # short code, never text from the document
    outputs: list[str] = Field(default_factory=list)  # names available for download
    page_states: dict[str, str] = Field(default_factory=dict)  # printed page label -> pending|review|done
    sections: dict[str, dict] = Field(default_factory=dict)    # section -> {chunks, pending, cost_usd}

    @property
    def progress(self) -> float:
        return round((self.chunks - self.pending) / self.chunks, 3) if self.chunks else 0.0


OUTPUTS = {  # download kind -> stored file name
    "vi_pdf": "out/vi.pdf",
    "interleaved_pdf": "out/interleaved.pdf",
    "segments_xlsx": "out/segments.xlsx",
    "terms_xlsx": "out/terms.xlsx",
}
