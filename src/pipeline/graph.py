"""BL-US-10c: LangGraph batch graph.

load -> classify -> pick -> mask -> prompt -> translate -> unmask -> qa -> (fix -> unmask -> qa)x<=2
     -> record -> gate -> pick ... -> END

Graph state stays small (current chunk only); outputs live in job.sqlite and the step ledger.
Every gateway call goes through Ledger.run_step (SP-05 idempotency guard).
"""
from __future__ import annotations

import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from ..guard import QAContext, Severity, load_patterns, load_signal_table, unmask
from ..guard.segment import check, finalize_target, fix_messages, needs_fix, prepare
from ..llm_gateway import (BudgetExceeded, DataClassViolation, Gateway, GatewayError, LLMRequest, NotConfigured)
from ..llm_gateway.adapters import estimate_tokens
from ..terms import TermStore, TranslationMemory, build_translation_prompt, select_glossary_subset
from .db import InFlightUnknown, JobDB, Ledger
from .schema import KINDS, Segment
from .tracing import Tracer, text_hash

PROMPT_VERSION = "pipe-v2"
PLACEHOLDER_RULE = ("Tokens like ⟦P001⟧ stand for protected codes. Copy each one exactly once, unchanged, "
                    "in the same order. Never translate, drop or invent them.")
RISK_BASE = {"signal": 0.8, "table": 0.4, "step": 0.3, "prose": 0.1, "label": 0.1}
_STEP_RE = re.compile(r"^\s*(?:\d+[.)]|[a-z][.)]|[-•*])\s+")


class State(TypedDict, total=False):
    only: Optional[list[str]]   # input: restrict to these chunk ids (re-run / continuation)
    order: list[str]
    pos: int
    cur: dict[str, Any]


def classify_kind(source: str, given: Optional[str], signal_words: dict[str, str]) -> str:
    """Deterministic kind. A signal word in the source (any case) always wins (safety first)."""
    words = [w for w in signal_words if w not in ("YES", "NO")]
    if any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", source, re.IGNORECASE) for w in words):
        return "signal"
    if given in KINDS:
        return given  # type: ignore[return-value]
    if "\t" in source or source.count("|") >= 2:
        return "table"
    if _STEP_RE.match(source):
        return "step"
    if len(source) <= 40 and not source.rstrip().endswith("."):
        return "label"
    return "prose"


def risk_of(kind: str, status: str, n_errors: int, n_warns: int) -> float:
    r = RISK_BASE.get(kind, 0.1) + 0.1 * n_warns + 0.2 * n_errors
    if status in ("needs_review", "error"):
        r = max(r, 0.7)
    return round(min(r, 1.0), 3)


@dataclass
class PipelineContext:
    job_id: str
    db: JobDB
    ledger: Ledger
    gateway: Gateway
    tracer: Tracer
    batch_size: int = 10
    allow_strong: Optional[bool] = None   # None -> follow the profile (allow_paid)
    store: Optional[TermStore] = None
    tm: TranslationMemory = field(default_factory=TranslationMemory)
    qa_ctx: QAContext = field(default_factory=lambda: QAContext(patterns=load_patterns(), signal_table=load_signal_table()))

    def strong_ok(self) -> bool:
        if self.allow_strong is not None:
            return self.allow_strong
        job = self.gateway.jobs.get(self.job_id) or self.gateway.start_job(self.job_id)
        return job.config.allow_paid


def _issue_str(i: dict) -> str:
    return f"{i['rule_id']}: {i['message']}"


def build_graph(ctx: PipelineContext, checkpointer):
    db, tracer = ctx.db, ctx.tracer

    def traced(name: str, fn: Callable[[State], dict]) -> Callable[[State], dict]:
        def run(state: State) -> dict:
            t0 = time.perf_counter()
            upd = fn(state)
            extra = upd.pop("_trace", {})
            cid = (upd.get("cur") or state.get("cur") or {}).get("id", "")
            tracer.record(name, cid, t0, **extra)
            return upd
        run.__name__ = name
        return run

    # ---- job level
    def load(state: State) -> dict:
        db.sync_chunks(db.load_chunks_file())
        only = state.get("only")
        order = [c for c in db.chunk_ids() if only is None or c in set(only)]
        return {"order": order, "pos": 0, "cur": {}}

    def classify(state: State) -> dict:
        for cid in state["order"]:
            c = db.get_chunk(cid)
            kind = classify_kind(c.source, c.kind, ctx.qa_ctx.signal_table)
            if kind != c.kind:
                db.set_kind(cid, kind)
        return {}

    def pick(state: State) -> dict:
        pos = state["pos"]
        if pos >= len(state["order"]):
            return {"cur": {}}
        return {"cur": {"id": state["order"][pos], "attempt": 0}}

    def after_pick(state: State) -> str:
        return "mask" if state["cur"].get("id") else "finalize"

    def finalize(state: State) -> dict:
        db.export_segments()
        return {}

    # ---- per chunk
    def mask_node(state: State) -> dict:
        cur = dict(state["cur"])
        c = db.get_chunk(cur["id"])
        try:
            prep = prepare(c.source, ctx.qa_ctx.patterns, ctx.qa_ctx.signal_table)
        except ValueError as e:
            cur["skip"] = f"MASK001: {e}"
            return {"cur": cur, "_trace": {"status": "skipped"}}
        # a leading signal word is split off here and re-attached from config after the call (never sent to the model)
        cur.update(masked=prep.masked, mapping=prep.mapping, prefix=prep.prefix, signal=prep.signal)
        if not prep.masked.strip():
            cur["raw"] = ""   # signal word only: nothing to translate, no LLM call
        return {"cur": cur, "_trace": {"input_hash": text_hash(c.source), "input_len": len(c.source)}}

    def after_mask(state: State) -> str:
        cur = state["cur"]
        return "record" if cur.get("skip") else ("unmask" if "raw" in cur else "prompt")

    def prompt_node(state: State) -> dict:
        cur = dict(state["cur"])
        masked = cur["masked"]
        subset = select_glossary_subset(masked, ctx.store) if ctx.store else []
        hits = ctx.tm.lookup(masked)
        gen = db.gen(cur["id"])
        version = PROMPT_VERSION + (f".r{gen}" if gen else "")  # a re-run must not hit the cache
        p = build_translation_prompt(masked, subset, hits, version)
        msgs = p["messages"]
        msgs[0] = {**msgs[0], "content": msgs[0]["content"] + "\n\n" + PLACEHOLDER_RULE}
        cur.update(messages=msgs, prompt_version=p["prompt_version"])
        return {"cur": cur, "_trace": {"prompt_version": version}}

    def call_gateway(cur: dict, stage: str, alias: str, messages: list[dict]) -> tuple[dict, str]:
        cid = cur["id"]

        def call() -> dict:
            req = LLMRequest(
                messages=messages, model_alias=alias, data_class="customer", job_id=ctx.job_id,
                target_id=cid, prompt_version=cur["prompt_version"], source_hash=text_hash(cur["masked"]),
                max_tokens=max(256, 3 * estimate_tokens(cur["masked"])))
            r = ctx.gateway.complete(req)
            return {"text": r.text, "tokens_in": r.tokens_in, "tokens_out": r.tokens_out,
                    "cost_usd": r.cost_usd, "cache_hit": r.cache_hit, "latency_ms": r.latency_ms,
                    "model_id": r.model_id}

        return ctx.ledger.run_step(cid, f"g{db.gen(cid)}:{stage}", call)

    def _llm_node(state: State, stage: str, alias: str, messages: list[dict]) -> dict:
        cur = dict(state["cur"])
        try:
            res, outcome = call_gateway(cur, stage, alias, messages)
        except (BudgetExceeded, NotConfigured, DataClassViolation):
            raise  # job-level problem: stop the run, the checkpoint stays at this node
        except InFlightUnknown:
            cur["fatal"] = "LEDGER001: a call for this step was started before a crash and its result was not recorded; not re-sent"
            return {"cur": cur, "_trace": {"stage": stage, "status": "needs_review"}}
        except GatewayError as e:
            cur["fatal"] = f"GW001: gateway call failed ({type(e).__name__})"  # no provider text
            return {"cur": cur, "_trace": {"stage": stage, "status": "error"}}
        cur["raw"] = res["text"]
        return {"cur": cur, "_trace": {
            "stage": stage, "tokens_in": res["tokens_in"], "tokens_out": res["tokens_out"],
            "cost_usd": 0.0 if outcome == "reused" else res["cost_usd"], "latency_ms": res["latency_ms"],
            "model": res["model_id"], "cache_hit": bool(res["cache_hit"]), "ledger": outcome,
            "prompt_version": cur["prompt_version"], "attempt": cur["attempt"],
            "output_hash": text_hash(res["text"]), "output_len": len(res["text"])}}

    def translate(state: State) -> dict:
        return _llm_node(state, "translate", "bulk", state["cur"]["messages"])

    def after_llm(state: State) -> str:
        return "record" if state["cur"].get("fatal") else "unmask"

    def unmask_node(state: State) -> dict:
        cur = dict(state["cur"])
        cur["raw"] = unicodedata.normalize("NFC", cur["raw"])
        cur["target"] = unmask(cur.get("prefix", "") + cur["raw"], cur["mapping"])
        return {"cur": cur}

    def qa(state: State) -> dict:
        cur = dict(state["cur"])
        c = db.get_chunk(cur["id"])
        prep = prepare(c.source, ctx.qa_ctx.patterns, ctx.qa_ctx.signal_table)
        issues = check(prep, cur["raw"], ctx.qa_ctx)   # same QA as `manuvi eval`: assembled text vs ORIGINAL source
        cur["issues"] = [i.model_dump(mode="json") for i in issues]
        n_err = sum(i.severity == Severity.error for i in issues)
        return {"cur": cur, "_trace": {"issues": n_err, "attempt": cur["attempt"]}}

    def after_qa(state: State) -> str:
        cur = state["cur"]
        return "fix" if needs_fix(cur["issues"], cur["attempt"]) else "record"

    def fix(state: State) -> dict:
        cur = dict(state["cur"])
        cur["attempt"] += 1
        messages = fix_messages(cur["messages"], cur["raw"], cur["issues"], cur["attempt"])
        alias = "strong" if ctx.strong_ok() else "bulk"
        return _llm_node({**state, "cur": cur}, f"fix{cur['attempt']}", alias, messages)

    def record(state: State) -> dict:
        cur = state["cur"]
        c = db.get_chunk(cur["id"])
        kind = c.kind or "prose"
        if cur.get("fatal") or cur.get("skip"):
            msg = cur.get("fatal") or cur["skip"]
            is_err = bool(cur.get("fatal")) and msg.startswith("GW001")
            seg = Segment(id=c.id, page=c.page, section=c.section, source=c.source, target="",
                          status="error" if is_err else "needs_review", issues=[msg],
                          risk=risk_of(kind, "error", 1, 0), kind=kind)
        else:
            issues = [i for i in cur["issues"] if i["severity"] != "ok"]
            n_err = sum(i["severity"] == "error" for i in issues)
            n_warn = sum(i["severity"] == "warn" for i in issues)
            status = "needs_review" if n_err else ("warn" if n_warn else "ok")
            strs = [_issue_str(i) for i in issues]
            if kind == "signal":  # safety text is never auto-accepted
                status = "needs_review"
                strs.append("SAFETY: signal segment needs human approval")
            seg = Segment(id=c.id, page=c.page, section=c.section, source=c.source,
                          target=finalize_target(issues, cur["target"]),   # script-guard failures are never shipped
                          status=status, issues=strs, risk=risk_of(kind, status, n_err, n_warn), kind=kind)
        db.put_segment(seg)
        return {"_trace": {"status": seg.status}}

    def gate(state: State) -> dict:
        pos, order, cur = state["pos"], state["order"], state["cur"]
        kind = db.get_chunk(cur["id"]).kind
        reasons = []
        if kind == "signal":
            reasons.append("safety")
        if (pos + 1) % ctx.batch_size == 0 and pos + 1 < len(order):
            reasons.append("batch")
        if reasons:
            db.export_segments()  # the reviewer must see what is being approved
            interrupt({"reasons": reasons, "chunk_id": cur["id"], "after": pos + 1})
        return {"pos": pos + 1}

    b = StateGraph(State)
    for name, fn in [("load", load), ("classify", classify), ("pick", pick), ("mask", mask_node),
                     ("prompt", prompt_node), ("translate", translate), ("unmask", unmask_node),
                     ("qa", qa), ("fix", fix), ("record", record), ("gate", gate)]:
        b.add_node(name, traced(name, fn))
    b.add_node("finalize", finalize)
    b.add_edge(START, "load")
    b.add_edge("load", "classify")
    b.add_edge("classify", "pick")
    b.add_conditional_edges("pick", after_pick, {"mask": "mask", "finalize": "finalize"})
    b.add_conditional_edges("mask", after_mask, {"record": "record", "prompt": "prompt", "unmask": "unmask"})
    b.add_edge("prompt", "translate")
    b.add_conditional_edges("translate", after_llm, {"record": "record", "unmask": "unmask"})
    b.add_edge("unmask", "qa")
    b.add_conditional_edges("qa", after_qa, {"fix": "fix", "record": "record"})
    b.add_conditional_edges("fix", after_llm, {"record": "record", "unmask": "unmask"})
    b.add_edge("record", "gate")
    b.add_edge("gate", "pick")
    b.add_edge("finalize", END)
    return b.compile(checkpointer=checkpointer)
