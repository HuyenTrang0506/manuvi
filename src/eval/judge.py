"""LLM-as-judge DESIGN (not executed in this repo's CI or by default). A second model reviews a translation on
four dimensions. The call always carries data_class=customer, so the gateway only lets it reach local/private
adapters (ollama on localhost, recorded, or an explicitly opted-in private adapter). Free hosted tiers are refused
by the gateway's data-class check. The judge reports; it never gates (ADR-007) until calibrated (docs/EVAL.md)."""
from __future__ import annotations

import hashlib
import json
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError

from src.llm_gateway import Gateway, LLMRequest

JUDGE_PROMPT_VERSION = "judge-v1"
DIMENSIONS = ("terminology", "fluency", "completeness", "safety")

RUBRIC = """You are a bilingual (English-Vietnamese) reviewer of technical service manuals for construction \
machinery. You receive an English SOURCE, a Vietnamese TRANSLATION, and the GLOSSARY of approved terms.
Score the translation on four dimensions, each an integer from 1 to 5:
- terminology: every glossary term is rendered with the glossary Vietnamese term, consistently; no invented synonyms.
- fluency: natural, grammatical Vietnamese for a technician; no word-by-word calques.
- completeness: nothing added, nothing omitted, nothing summarised; every step, value and condition is present.
- safety: warnings, cautions and dangers keep their level and meaning; no instruction is weakened, reversed or lost.
Rules:
1. Codes, part numbers, connector/fuse/relay IDs and numbers with units must be unchanged. Any change is a blocking \
issue under completeness.
2. Judge only what is written. Do not correct the translation and do not use outside knowledge about the machine.
3. Give each blocking issue as {dimension, description}. A blocking issue is anything a technician could act on wrongly.
4. verdict: "pass" only if all scores >= 4 and there is no blocking issue; "fail" if any score <= 2 or a safety \
issue exists; otherwise "needs_review".
Answer with one JSON object that matches the schema. No prose."""

JUDGE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["terminology", "fluency", "completeness", "safety", "blocking_issues", "verdict"],
    "properties": {
        **{d: {"type": "integer", "minimum": 1, "maximum": 5} for d in DIMENSIONS},
        "blocking_issues": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["dimension", "description"],
            "properties": {"dimension": {"enum": list(DIMENSIONS)}, "description": {"type": "string", "maxLength": 300}}}},
        "verdict": {"enum": ["pass", "fail", "needs_review"]},
    },
}


class Issue(BaseModel):
    dimension: Literal["terminology", "fluency", "completeness", "safety"]
    description: str


class JudgeVerdict(BaseModel):
    terminology: int = Field(ge=1, le=5)
    fluency: int = Field(ge=1, le=5)
    completeness: int = Field(ge=1, le=5)
    safety: int = Field(ge=1, le=5)
    blocking_issues: list[Issue] = []
    verdict: Literal["pass", "fail", "needs_review"]

    def effective_verdict(self) -> str:
        """The judge can only make things stricter than what it said; consistency with its own scores is enforced."""
        scores = [self.terminology, self.fluency, self.completeness, self.safety]
        if self.safety <= 2 or min(scores) <= 2 or any(i.dimension == "safety" for i in self.blocking_issues):
            return "fail"
        if self.verdict == "pass" and (min(scores) < 4 or self.blocking_issues):
            return "needs_review"
        return self.verdict


def build_judge_request(source: str, translation: str, glossary_lines: list[str], *, job_id: str,
                        target_id: str = "") -> LLMRequest:
    """data_class is fixed to 'customer' on purpose: there is no parameter to relax it."""
    user = ("GLOSSARY:\n" + "\n".join(glossary_lines) + f"\n\nSOURCE:\n{source}\n\nTRANSLATION:\n{translation}")
    return LLMRequest(
        messages=[{"role": "system", "content": RUBRIC}, {"role": "user", "content": user}],
        response_format={"type": "json_schema", "json_schema": {"name": "judge_verdict", "schema": JUDGE_SCHEMA, "strict": True}},
        model_alias="strong", data_class="customer", job_id=job_id, target_id=target_id,
        prompt_version=JUDGE_PROMPT_VERSION, source_hash=hashlib.sha256((source + "\n" + translation).encode()).hexdigest(),
        temperature=0.0)


def parse_verdict(text: str) -> Optional[JudgeVerdict]:
    """None when the model did not return valid JSON for the schema (caller flags needs_review)."""
    try:
        s = text.strip()
        if s.startswith("```"):
            s = s.strip("`").removeprefix("json").strip()
        return JudgeVerdict.model_validate(json.loads(s))
    except (ValueError, ValidationError):
        return None


def judge(gateway: Gateway, source: str, translation: str, glossary_lines: list[str], *, job_id: str,
          target_id: str = "") -> tuple[Optional[JudgeVerdict], float]:
    """Raises DataClassViolation if the pinned 'strong' alias points at a public-only (free hosted) adapter."""
    res = gateway.complete(build_judge_request(source, translation, glossary_lines, job_id=job_id, target_id=target_id))
    return parse_verdict(res.text), res.cost_usd


def cohen_kappa(a: list[str], b: list[str]) -> float:
    """Agreement between judge and human labels beyond chance (labels such as pass / needs_review / fail)."""
    if len(a) != len(b) or not a:
        raise ValueError("label lists must be non-empty and equal length")
    n = len(a)
    po = sum(x == y for x, y in zip(a, b)) / n
    pe = sum((a.count(k) / n) * (b.count(k) / n) for k in set(a) | set(b))
    return 1.0 if pe == 1 else (po - pe) / (1 - pe)
