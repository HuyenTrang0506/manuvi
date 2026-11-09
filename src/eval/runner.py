"""Run an eval set through the LLM gateway (fake by default, recorded replay, ollama, ...) and score it."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.guard.masking import load_patterns, mask, unmask
from src.llm_gateway import Gateway, LLMRequest, RecordedAdapter, RecordedMiss, cache_key
from src.llm_gateway.config import ROOT
from src.terms.prompt import build_translation_prompt, select_glossary_subset
from src.terms.store import TermRecord

from .cases import DEFAULT_GLOSSARY, DEFAULT_SET, EvalCase, GlossaryTerm, load_cases, load_glossary
from .report import DEFAULT_SOFT_TOLERANCE_PCT, GateResult, Output, build_report, gate, render_markdown

PROMPT_VERSION = "eval-v1"
RECORDED_MODEL_ID = "eval-recorded"
EVAL_ROOT = ROOT / "workspace" / "eval"   # workspace/ is gitignored: outputs of customer runs stay local


class _ListStore:
    """Duck-typed stand-in for TermStore.approved(). Eval-only: these in-memory records are NEVER written to the
    term store and the VI terms stay proposals until the founder/uncle approve them."""

    def __init__(self, g: dict[str, GlossaryTerm]):
        self._recs = [TermRecord(term_en=t.term_en, term_vi=t.term_vi, status="approved", citation="eval-proposal",
                                 approved_by="eval-harness") for t in g.values()]

    def approved(self) -> list[TermRecord]:
        return self._recs


def build_request(case: EvalCase, g: dict[str, GlossaryTerm], run_id: str, *, data_class: str = "customer",
                  alias: str = "bulk") -> tuple[LLMRequest, dict[str, str]]:
    masked, mapping = mask(case.source, load_patterns())
    subset = select_glossary_subset(masked, _ListStore(g))
    p = build_translation_prompt(masked, subset, [], PROMPT_VERSION)
    req = LLMRequest(messages=p["messages"], model_alias=alias, data_class=data_class, job_id=run_id,
                     target_id=case.id, prompt_version=PROMPT_VERSION,
                     source_hash=hashlib.sha256(masked.encode()).hexdigest())
    return req, mapping


def mask_reference(ref: str, mapping: dict[str, str]) -> str:
    """Put placeholders back into a human reference (to build a recorded fixture)."""
    for ph, val in sorted(mapping.items(), key=lambda kv: -len(kv[1])):
        ref = ref.replace(val, ph, 1)
    return ref


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
            text = mask_reference(c.reference_vi, mapping)
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
    outs: dict[str, Output] = {}
    for c in cases:
        req, mapping = build_request(c, g, run_id, data_class=data_class, alias=alias)
        try:
            res = gateway.complete(req)   # DataClassViolation / budget errors propagate: they stop the run
        except RecordedMiss as e:
            outs[c.id] = Output(id=c.id, mapping=mapping, error=f"RecordedMiss: {e}")
            continue
        outs[c.id] = Output(id=c.id, output=unmask(res.text, mapping), masked_output=res.text, mapping=mapping,
                            tokens_in=res.tokens_in, tokens_out=res.tokens_out, cost_usd=res.cost_usd,
                            cache_hit=res.cache_hit)
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
