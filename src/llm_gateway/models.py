from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

ModelAlias = Literal["bulk", "strong", "vision"]
DataClass = Literal["public", "customer"]


class LLMRequest(BaseModel):
    messages: list[dict[str, Any]]  # OpenAI-style
    response_format: Optional[dict[str, Any]] = None
    model_alias: ModelAlias = "bulk"
    data_class: DataClass = "customer"  # safest default
    job_id: str
    target_id: str = ""
    prompt_version: str
    source_hash: str
    temperature: float = 0.0
    max_tokens: Optional[int] = None


class LLMResult(BaseModel):
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: float = 0.0
    cache_hit: bool = False
    model_id: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)
