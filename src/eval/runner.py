"""Run an eval set through the LLM gateway (fake by default, recorded replay, ollama, ...) and score it."""
from __future__ import annotations

import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import unicodedata

from src.guard import load_signal_table
from src.guard.masking import load_patterns, mask
from src.guard.segment import prepare, run_guarded
from src.llm_gateway import Gateway, LLMRequest, RecordedAdapter, RecordedMiss, cache_key
from src.llm_gateway.config import ROOT
from src.terms.prompt import build_translation_prompt, select_glossary_subset
from src.terms.store import TermRecord

from .cases import DEFAULT_GLOSSARY, DEFAULT_SET, EvalCase, GlossaryTerm, load_cases, load_glossary
from .report import DEFAULT_SOFT_TOLERANCE_PCT, GateResult, Output, build_report, gate, render_markdown

PROMPT_VERSION = "eval-v3"
RECORDED_MODEL_ID = "eval-recorded"
EVAL_ROOT = ROOT / "workspace" / "eval"   # workspace/ is gitignored: outputs of customer runs stay local


class _ListStore:
    """Duck-typed stand-in for TermStore.approved(). Eval-only: these in-memory records are NEVER written to the
    term store and the VI terms stay proposals until the founder/uncle approve them."""

    def __init__(self, g: dict[str, GlossaryTerm]):
        self._recs = [TermRecord(term_en=t.term_en, term_vi=t.term_vi, status="approved", citation="eval-proposal",
                                 approved_by="eval-harness", must_use_in_prompt=t.must_use_in_prompt) for t in g.values()]

    def approved(self) -> list[TermRecord]:
        return self._recs


def build_request(case: EvalCase, g: dict[str, GlossaryTerm], run_id: str, *, data_class: str = "customer",
                  alias: str = "bulk") -> tuple[LLMRequest, dict[str, str]]:
    """First-call request for a case: the model sees the signal-stripped, masked BODY (as in the pipeline)."""
    prep = prepare(case.source, load_patterns(), load_signal_table())
    return _request(prep.masked, _messages(prep.masked, g), case.id, run_id, data_class, alias), prep.mapping


def _messages(masked: str, g: dict[str, GlossaryTerm]) -> list[dict]:
    subset = select_glossary_subset(masked, _ListStore(g))
    return build_translation_prompt(masked, subset, [], PROMPT_VERSION)["messages"]


def _request(masked: str, messages: list[dict], case_id: str, run_id: str, data_class: str, alias: str) -> LLMRequest:
    return LLMRequest(messages=messages, model_alias=alias, data_class=data_class, job_id=run_id, target_id=case_id,
                      prompt_version=PROMPT_VERSION, source_hash=hashlib.sha256(masked.encode()).hexdigest())


def mask_reference(ref: str, mapping: dict[str, str]) -> str:
    """Put placeholders back into a human reference (to build a recorded fixture)."""
    for ph, val in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        ref = ref.replace(val, ph, 1)
    return ref


def strip_signal(reference: str, source: str) -> str:
    """The model only translates the body, so a reference's leading fixed VI signal word is not part of its answer."""
    prefix = prepare(source, load_patterns(), load_signal_table()).prefix
    if not prefix:
        return reference
    word = re.escape(unicodedata.normalize("NFC", prefix).rstrip(": \n"))
    return re.sub(rf"^\s*{word}\s*[:\n]?\s*", "", unicodedata.normalize("NFC", reference), count=1, flags=re.I)


def make_recorded_fixture(cases: list[EvalCase], g: dict[str, GlossaryTerm], path: Path | str,
                          *, data_class: str = "customer", alias: str = "bulk") -> int:
    """Fixture whose 'model' answers every case with its human reference. Used to prove the harness and as the
    CI baseline; replace it with real recorded model output to track a real model."""
    n = 0
    with open(path, "w", encoding="utf-8") as f:
        for c in cases:
            if not c.reference_vi:
                continue
            req, mapping = build_request(c, g, "fixture", data_class=data_class, alias=alias)
            text = mask_reference(strip_signal(c.reference_vi, c.source), mapping)
            f.write(json.dumps({"key": cache_key(req, RECORDED_MODEL_ID), "text": text, "tokens_in": 0,
                                "tokens_out": 0}, ensure_ascii=False) + "\n")
            n += 1
    return n


def make_gateway(model: str, run_dir: Path, *, fixture: Optional[Path] = None, alias: str = "bulk") -> Gateway:
    """`model`: 'fake' | 'recorded' (needs fixture) | '<adapter>:<model_id>' such as 'ollama:qwen2.5:7b'.
    Always the dev profile: no paid call is possible unless the caller also enables it explicitly."""
    env = {"MANUVI_PROFILE": "dev"}
    adapters = {}
    if model == "recorded":
        if not fixture:
            raise ValueError("--model recorded needs --fixture")
        adapters["recorded"] = RecordedAdapter(fixture)
        env[f"MANUVI_MODEL_{alias.upper()}"] = f"recorded:{RECORDED_MODEL_ID}"
    elif model != "fake":
        env[f"MANUVI_MODEL_{alias.upper()}"] = model
    run_dir.mkdir(parents=True, exist_ok=True)
    return Gateway(cache_path=run_dir / "llm_cache.sqlite", env=env, adapters=adapters)


def run_cases(cases: list[EvalCase], g: dict[str, GlossaryTerm], gateway: Gateway, run_id: str, *,
              data_class: str = "customer", alias: str = "bulk") -> dict[str, Output]:
    """Same guarded path as the pipeline (src/guard/segment.run_guarded): signal split, mask, call, re-attach, QA
    with script guard, fix loop (max 2). Output.output is the FINAL accepted text ("" when it must not be shipped)."""
    patterns, table = load_patterns(), load_signal_table()
    outs: dict[str, Output] = {}
    for c in cases:
        def complete(stage: str, messages: list[dict], c=c) -> dict:
            masked = next(m["content"] for m in messages if m["role"] == "user")
            masked = prep_masked[c.id]
            res = gateway.complete(_request(masked, messages, c.id, run_id, data_class, alias))
            return {"text": res.text, "tokens_in": res.tokens_in, "tokens_out": res.tokens_out,
                    "cost_usd": res.cost_usd, "cache_hit": res.cache_hit}

        prep_masked = {c.id: prepare(c.source, patterns, table).masked}
        t0 = time.perf_counter()
        try:   # DataClassViolation / budget errors propagate: they stop the run
            r = run_guarded(c.source, patterns=patterns, table=table,
                            build_messages=lambda masked: _messages(masked, g), complete=complete)
        except RecordedMiss as e:
            outs[c.id] = Output(id=c.id, mapping=prepare(c.source, patterns, table).mapping,
                                error=f"RecordedMiss: {e}", seconds=time.perf_counter() - t0)
            continue
        outs[c.id] = Output(
            id=c.id, output=r.text, masked_output=r.prep.prefix + r.raw, mapping=r.prep.mapping,
            tokens_in=sum(x["tokens_in"] for x in r.calls), tokens_out=sum(x["tokens_out"] for x in r.calls),
            cost_usd=sum(x["cost_usd"] for x in r.calls), cache_hit=bool(r.calls) and all(x["cache_hit"] for x in r.calls),
            attempts=r.attempts, repaired=r.repaired, routed_to_review=r.routed_to_review,
            review_rules=sorted(set(r.rules)) if r.routed_to_review else [], seconds=time.perf_counter() - t0)
    return outs


def read_hypotheses(path: Path | str) -> dict[str, Output]:
    """Score translations produced elsewhere: JSONL {"id", "vi"}."""
    outs = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            outs[d["id"]] = Output(id=d["id"], output=d["vi"])
    return outs


class EvalRun:
    def __init__(self, report: dict, run_dir: Path, gate_result: Optional[GateResult]):
        self.report, self.run_dir, self.gate = report, run_dir, gate_result

    @property
    def ok(self) -> bool:
        return self.gate is None or self.gate.ok


def new_run_id(model: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", model).strip("-")[:30] or "model"
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + slug


def run_eval(*, set_path: Path | str = DEFAULT_SET, glossary_path: Path | str = DEFAULT_GLOSSARY,
             model: str = "fake", fixture: Optional[Path | str] = None, hypotheses: Optional[Path | str] = None,
             out_root: Path | str = EVAL_ROOT, run_id: Optional[str] = None, baseline: Optional[Path | str] = None,
             tolerance_pct: float = DEFAULT_SOFT_TOLERANCE_PCT, data_class: str = "customer",
             alias: str = "bulk", gateway: Optional[Gateway] = None) -> EvalRun:
    """The single entry point used by the CLI, the worker and CI. Writes workspace/eval/<run-id>/."""
    cases = load_cases(set_path)
    g = load_glossary(glossary_path)
    run_id = run_id or new_run_id(model if not hypotheses else "hypotheses")
    run_dir = Path(out_root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    if hypotheses:
        outs = read_hypotheses(hypotheses)
        label = f"hypotheses:{Path(hypotheses).name}"
    else:
        gw = gateway or make_gateway(model, run_dir, fixture=Path(fixture) if fixture else None, alias=alias)
        outs = run_cases(cases, g, gw, run_id, data_class=data_class, alias=alias)
        label = model
    meta = {"run_id": run_id, "prompt_version": PROMPT_VERSION, "data_class": data_class,
            "created": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    rep = build_report(cases, outs, g, model=label, meta=meta)
    gr = None
    if baseline:
        gr = gate(rep, json.loads(Path(baseline).read_text(encoding="utf-8")), tolerance_pct)
        (run_dir / "gate.json").write_text(gr.model_dump_json(indent=2), encoding="utf-8")
    (run_dir / "results.jsonl").write_text(
        "".join(o.model_dump_json() + "\n" for o in outs.values()), encoding="utf-8")
    (run_dir / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "report.md").write_text(render_markdown(rep, gr), encoding="utf-8")
    return EvalRun(rep, run_dir, gr)
