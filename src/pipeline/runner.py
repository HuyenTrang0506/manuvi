"""Run / resume / status / re-run of a job dir. Thread id = job id (re-runs get '<job>#rN')."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from ..llm_gateway import BudgetExceeded, Gateway, Job
from ..llm_gateway.adapters import Adapter
from ..llm_gateway.cache import SqliteStore  # noqa: F401  (documented dependency of the cache file)
from ..terms import TermStore, TranslationMemory
from .db import JobDB, Ledger
from .graph import PipelineContext, build_graph
from .schema import CACHE_FILE, CHECKPOINT_FILE, TRACE_FILE
from .tracing import JsonlExporter, LangfuseExporter, TraceExporter, Tracer, langfuse_enabled

EXIT = {"done": 0, "waiting": 3, "budget_stopped": 4}


@dataclass
class RunResult:
    state: str                      # done | waiting | budget_stopped
    thread_id: str
    gate: Optional[dict] = None     # pending interrupt payload when waiting
    message: str = ""
    status: dict = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        return EXIT[self.state]


def make_gateway(job_dir: Path, profile: str = "dev", adapters: Optional[dict[str, Adapter]] = None,
                 env: Optional[dict] = None, **kw: Any) -> Gateway:
    e = {"MANUVI_PROFILE": profile, **(env or {})}
    return Gateway(cache_path=Path(job_dir) / CACHE_FILE, env=e, adapters=adapters, **kw)


def apply_job_budget(gw: Gateway, job_id: str, usd: float) -> None:
    """Hard cap for this job, enforced by the gateway's atomic reserve (BudgetExceeded)."""
    job = gw.jobs.get(job_id) or gw.start_job(job_id)
    gw.jobs[job_id] = Job(job_id, job.config.model_copy(update={"cap_job_usd": usd}))


def _pending_interrupt(snapshot: Any) -> Optional[dict]:
    for t in snapshot.tasks:
        for i in t.interrupts:
            return i.value
    return None


def run_job(
    job_dir: Path | str,
    *,
    gateway: Gateway,
    job_id: Optional[str] = None,
    batch_size: int = 10,
    approve_batches: bool = False,
    approve_safety: bool = False,
    only: Optional[list[str]] = None,
    budget_usd: Optional[float] = None,
    allow_strong: Optional[bool] = None,
    store: Optional[TermStore] = None,
    tm: Optional[TranslationMemory] = None,
    exporters: Optional[list[TraceExporter]] = None,
    langfuse: Optional[TraceExporter] = None,
    guard: bool = True,
    stall_at: Optional[tuple[str, str]] = None,
    emit: Callable[[str], None] = lambda s: None,
) -> RunResult:
    job_dir = Path(job_dir)
    job_id = job_id or job_dir.name
    db = JobDB(job_dir)
    ledger = Ledger(job_dir, job_id, guard=guard, stall_at=stall_at, emit=emit)
    exps: list[TraceExporter] = [JsonlExporter(job_dir / TRACE_FILE), *(exporters or [])]
    if langfuse is not None:
        exps.append(langfuse)
    elif langfuse_enabled():
        exps.append(LangfuseExporter())
    if budget_usd is not None:
        apply_job_budget(gateway, job_id, budget_usd)
    ctx = PipelineContext(job_id=job_id, db=db, ledger=ledger, gateway=gateway, tracer=Tracer(job_id, exps),
                          batch_size=batch_size, allow_strong=allow_strong, store=store,
                          tm=tm or TranslationMemory())
    conn = sqlite3.connect(job_dir / CHECKPOINT_FILE, check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_graph(ctx, saver)
    try:
        # choose the thread
        thread = db.get_meta("thread", job_id)
        graph_input: Any = None
        if only is not None:
            n = int(db.get_meta("rerun_n", "0")) + 1
            db.set_meta("rerun_n", str(n))
            thread = f"{job_id}#r{n}"
            graph_input = {"only": only}
        config = {"configurable": {"thread_id": thread}, "recursion_limit": 100_000}
        snap = graph.get_state(config)
        if only is None:
            if not snap.values:
                graph_input = {"only": None}
            elif not snap.next:
                pending = db.pending_ids()
                if not pending:
                    db.set_meta("state", "done")
                    return RunResult("done", thread, status=job_status(job_dir, job_id))
                n = int(db.get_meta("rerun_n", "0")) + 1
                db.set_meta("rerun_n", str(n))
                thread = f"{job_id}#r{n}"
                config = {"configurable": {"thread_id": thread}, "recursion_limit": 100_000}
                graph_input = {"only": pending}
        db.set_meta("thread", thread)

        payload = _pending_interrupt(snap) if graph_input is None else None
        while True:
            if payload is not None:
                reasons = payload["reasons"]
                ok = approve_safety if "safety" in reasons else approve_batches
                if "safety" in reasons and "batch" in reasons:
                    ok = approve_safety and approve_batches
                if not ok:
                    db.export_segments()
                    db.set_meta("state", "waiting")
                    return RunResult("waiting", thread, gate=payload,
                                     message=f"waiting for human approval: {','.join(reasons)} after {payload['chunk_id']}",
                                     status=job_status(job_dir, job_id))
                emit(f"GATE_APPROVED {','.join(reasons)} chunk={payload['chunk_id']}")
                graph_input = Command(resume={"approved": True})
            try:
                out = graph.invoke(graph_input, config)
            except BudgetExceeded as e:
                db.export_segments()
                db.set_meta("state", "budget_stopped")
                return RunResult("budget_stopped", thread, message=f"budget cap reached: {e}",
                                 status=job_status(job_dir, job_id))
            if "__interrupt__" in out:
                payload = out["__interrupt__"][0].value
                graph_input = None
                continue
            break
        db.export_segments()
        db.set_meta("state", "done")
        return RunResult("done", thread, status=job_status(job_dir, job_id))
    finally:
        conn.close()
        ledger.close()
        db.close()


def job_status(job_dir: Path | str, job_id: Optional[str] = None) -> dict:
    """BL-US-29: counts by status, cost and cache hits. Reads files only, no gateway."""
    job_dir = Path(job_dir)
    job_id = job_id or job_dir.name
    db = JobDB(job_dir)
    ledger = Ledger(job_dir, job_id)
    try:
        counts = {"ok": 0, "warn": 0, "error": 0, "needs_review": 0}
        for seg, _ in db.segments():
            counts[seg.status] += 1
        total = len(db.chunk_ids())
        t = ledger.totals()
        return {"job_id": job_id, "state": db.get_meta("state", "new"), "chunks": total,
                "pending": len(db.pending_ids()), "by_status": counts,
                "cost_usd": round(t["cost_usd"], 6), "calls": t["calls"], "cache_hits": t["cache_hits"],
                "reused_from_ledger": t["reused"], "tokens_in": t["tokens_in"], "tokens_out": t["tokens_out"]}
    finally:
        ledger.close()
        db.close()


def select_for_rerun(job_dir: Path | str, *, pages: Optional[list[int]] = None, failed: bool = False,
                     ids: Optional[list[str]] = None) -> list[str]:
    """Chunk ids to re-run. 'failed' = error, or needs_review caused by a QA issue (not only the
    always-on safety flag)."""
    db = JobDB(Path(job_dir))
    try:
        out = []
        for seg, _ in db.segments():
            real = [i for i in seg.issues if not i.startswith("SAFETY:")]
            hit = (pages is not None and seg.page in pages) or (ids is not None and seg.id in ids) or (
                failed and (seg.status == "error" or (seg.status == "needs_review" and bool(real))))
            if hit:
                out.append(seg.id)
        return out
    finally:
        db.close()


def rerun_selection(job_dir: Path | str, *, gateway: Gateway, pages: Optional[list[int]] = None,
                    failed: bool = False, ids: Optional[list[str]] = None, **kw: Any) -> RunResult:
    """BL-US-29: re-run only the selected pages / failed chunks. Their ledger generation is bumped,
    so they are billed again on purpose; everything else is untouched."""
    sel = select_for_rerun(job_dir, pages=pages, failed=failed, ids=ids)
    if not sel:
        return RunResult("done", "", message="nothing selected", status=job_status(job_dir))
    db = JobDB(Path(job_dir))
    db.reset_chunks(sel)
    db.close()
    return run_job(job_dir, gateway=gateway, only=sel, **kw)
