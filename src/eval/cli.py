"""`manuvi eval ...`"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .cases import DEFAULT_GLOSSARY, DEFAULT_SET, SetModified, freeze, load_cases, load_glossary
from .glossary_io import write_glossary_xlsx
from .report import DEFAULT_SOFT_TOLERANCE_PCT
from .runner import EVAL_ROOT, make_recorded_fixture, run_eval

app = typer.Typer(help="Translation quality evaluation (offline, $0 by default).", no_args_is_help=True)


@app.command("run")
def run(
    model: str = typer.Option("fake", help="fake | recorded | <adapter>:<model_id> e.g. ollama:qwen2.5:7b"),
    fixture: Optional[Path] = typer.Option(None, help="Recorded responses (JSONL) for --model recorded"),
    hypotheses: Optional[Path] = typer.Option(None, help="Score existing translations {id, vi} instead of calling a model"),
    set_path: Path = typer.Option(DEFAULT_SET, "--set", help="Frozen eval set (JSONL)"),
    glossary: Path = typer.Option(DEFAULT_GLOSSARY, help="Glossary CSV (proposed terms by default)"),
    baseline: Optional[Path] = typer.Option(None, help="Baseline report.json; exit 1 when the gate fails"),
    tolerance: float = typer.Option(DEFAULT_SOFT_TOLERANCE_PCT, help="Allowed drop of soft metrics, percent"),
    out: Path = typer.Option(EVAL_ROOT, help="Output root (default workspace/eval)"),
    run_id: Optional[str] = typer.Option(None, help="Run id (default timestamp + model)"),
    data_class: str = typer.Option("customer", help="public | customer (customer only reaches private adapters)"),
    write_baseline: Optional[Path] = typer.Option(None, help="Also copy report.json here as the new baseline"),
) -> None:
    try:
        r = run_eval(set_path=set_path, glossary_path=glossary, model=model, fixture=fixture, hypotheses=hypotheses,
                     out_root=out, run_id=run_id, baseline=baseline, tolerance_pct=tolerance, data_class=data_class)
    except SetModified as e:
        typer.echo(f"eval set error: {e}", err=True)
        raise typer.Exit(2)
    m = r.report["metrics"]
    typer.echo(f"run {r.report['meta']['run_id']} -> {r.run_dir}")
    for k, v in m.items():
        typer.echo(f"  {k:22s} {v['kind']:5s} {'n/a' if v['value'] is None else f'{v['value'] * 100:6.1f}%'}")
    c = r.report["cost"]
    if c:
        typer.echo(f"  cost ${c['total_usd']:.4f} total, ${c['cost_per_page_usd']:.4f}/page over {c['pages']} pages")
    if write_baseline:
        write_baseline.parent.mkdir(parents=True, exist_ok=True)
        write_baseline.write_text((r.run_dir / "report.json").read_text(encoding="utf-8"), encoding="utf-8")
    tm = r.report.get("timing") or {}
    if tm.get("total_seconds"):
        typer.echo(f"  time {tm['total_seconds']:.1f} s total, {tm['seconds_per_segment']:.2f} s/segment")
    if r.gate is not None:
        typer.echo("GATE " + ("PASS" if r.gate.ok else "FAIL"))
        for f in r.gate.failures:
            typer.echo(f"  - {f}")
    raise typer.Exit(0 if r.ok else 1)


@app.command("freeze")
def freeze_cmd(set_path: Path = typer.Argument(DEFAULT_SET)) -> None:
    """Write the hash manifest next to the set. Do this once per set version."""
    typer.echo(str(freeze(set_path)))


@app.command("verify")
def verify(set_path: Path = typer.Argument(DEFAULT_SET)) -> None:
    try:
        n = len(load_cases(set_path))
    except SetModified as e:
        typer.echo(str(e), err=True)
        raise typer.Exit(2)
    typer.echo(f"ok: {n} cases match the manifest")


@app.command("make-fixture")
def make_fixture(path: Path = typer.Argument(..., help="Output JSONL"), set_path: Path = typer.Option(DEFAULT_SET, "--set"),
                 glossary: Path = typer.Option(DEFAULT_GLOSSARY)) -> None:
    """Recorded fixture that answers each case with its human reference (harness self-test / CI baseline)."""
    typer.echo(f"{make_recorded_fixture(load_cases(set_path), load_glossary(glossary), path)} responses -> {path}")


@app.command("glossary-template")
def glossary_template(xlsx: Path = typer.Argument(Path("electrical_terms_for_review.xlsx")),
                      glossary: Path = typer.Option(DEFAULT_GLOSSARY)) -> None:
    """Excel version of the proposed electrical terms for the founder/uncle to review."""
    typer.echo(str(write_glossary_xlsx(glossary, xlsx)))
