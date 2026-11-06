"""Shared types for the guard package."""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    ok = "ok"
    warn = "warn"
    error = "error"


class Issue(BaseModel):
    rule_id: str
    severity: Severity
    message: str
    fix_hint: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class FixResult(BaseModel):
    target: str
    issues: list[Issue]
    attempts: int
    needs_review: bool
