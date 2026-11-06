"""python -m src.pipeline run|demo|status|approve|rerun"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from typing import Optional

import typer

from ..terms import TermStore
from .demo import DemoTranslator, chunks_from_fixture, synthetic_chunks, write_chunks
from .runner import job_status, make_gateway, rerun_selection, run_job
from .schema import CHUNKS_FILE

app = typer.Typer(help="Batch translation pipeline (offline with the fake adapter).")


def _emit(s: str) -> None:
    print(s, flush=True)


def _gateway(job_dir: Path, profile: str):
    adapters = {"fake": DemoTranslator()} if profile == "dev" else None
    return make_gateway(job_dir, profile, adapters=adapters)


def _finish(res) -> None:
    typer.echo(json.dumps({"state": res.state, "message": res.message, "status": res.status}, ensure_ascii=False))
    raise typer.Exit(res.exit_code)


def _common(job_dir, profile, job_id, batch_size, approve_batches, approve_safety, budget, terms, stall_at, no_guard):
    stall = tuple(stall_at.split(":", 1)) if stall_at else None  # "<chunk>:<stage>", e.g. C005:g0:translate
    return dict(
        gateway=_gateway(job_dir, profile), job_id=job_id, batch_size=batch_size,
        approve_batches=approve_batches, approve_safety=approve_safety, budget_usd=budget,
        store=TermStore(terms) if terms else None, guard=not no_guard, stall_at=stall, emit=_emit)


@app.command()
def run(
    pdf_text_fixture: Optional[Path] = typer.Option(None, "--pdf-text-fixture", help="chunks .json or paragraphs .txt; omit to resume an existing job dir"),
    profile: str = typer.Option("dev"),
    job_dir: Path = typer.Option(Path("workspace/job"), "--job-dir"),
    job_id: Optional[str] = typer.Option(None, "--job-id"),
    batch_size: int = typer.Option(10),
    approve_batches: bool = typer.Option(False, help="auto-approve batch gates (stands in for a human)"),
    budget: Optional[float] = typer.Option(None, help="hard job cap in USD, enforced by the gateway"),
    terms: Optional[Path] = typer.Option(None, help="term store JSONL"),
    stall_at: str = typer.Option("", hidden=True),
    no_guard: bool = typer.Option(False, hidden=True),
) -> None:
    if pdf_text_fixture is not None:
        write_chunks(job_dir, chunks_from_fixture(pdf_text_fixture))
    elif not (job_dir / CHUNKS_FILE).exists():
        typer.echo(f"no {CHUNKS_FILE} in {job_dir}; pass --pdf-text-fixture", err=True)
        raise typer.Exit(2)
    _finish(run_job(job_dir, **_common(job_dir, profile, job_id, batch_size, approve_batches, False,
                                       budget, terms, stall_at, no_guard)))


@app.command()
def approve(
    job_dir: Path = typer.Option(..., "--job-dir"),
    profile: str = typer.Option("dev"),
    job_id: Optional[str] = typer.Option(None),
    batches: bool = typer.Option(True),
    batch_size: int = typer.Option(10),
) -> None:
    """Resume a job waiting at a gate. Safety gates open only after the web app recorded decision=approved."""
    _finish(run_job(job_dir, **_common(job_dir, profile, job_id, batch_size, batches, False, None, None, "", False)))


@app.command()
def demo(job_dir: Path = typer.Option(Path("workspace/demo"), "--job-dir"), chunks: int = typer.Option(9),
         clean: bool = typer.Option(True)) -> None:
    """Synthetic chunks + fake adapter, $0. Safety gates are approved to let the demo finish."""
    if clean and job_dir.exists():
        shutil.rmtree(job_dir)
    write_chunks(job_dir, synthetic_chunks(chunks))
    res = run_job(job_dir, **_common(job_dir, "dev", "demo", 4, True, True, None, None, "", False))
    typer.echo(json.dumps(res.status, ensure_ascii=False, indent=1))
    typer.echo(f"segments: {job_dir / 'segments.json'}")


@app.command()
def status(job_dir: Path = typer.Option(..., "--job-dir"), job_id: Optional[str] = typer.Option(None)) -> None:
    typer.echo(json.dumps(job_status(job_dir, job_id), ensure_ascii=False, indent=1))


@app.command()
def rerun(
    job_dir: Path = typer.Option(..., "--job-dir"),
    pages: Optional[str] = typer.Option(None, help="comma separated pages"),
    failed: bool = typer.Option(False, help="re-run failed chunks only"),
    profile: str = typer.Option("dev"), job_id: Optional[str] = typer.Option(None),
    approve_batches: bool = typer.Option(False),
    budget: Optional[float] = typer.Option(None),
) -> None:
    if not pages and not failed:
        typer.echo("choose --pages and/or --failed", err=True)
        raise typer.Exit(2)
    pg = [int(p) for p in pages.split(",")] if pages else None
    kw = _common(job_dir, profile, job_id, 10, approve_batches, False, budget, None, "", False)
    _finish(rerun_selection(job_dir, pages=pg, failed=failed, **kw))
