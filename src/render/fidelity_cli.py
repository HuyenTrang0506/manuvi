from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from .fidelity import analyze, write
from .fonts import FontSet


def fidelity(
    pdf: Path = typer.Option(..., "--pdf", help="Manual PDF (stays on this machine)"),
    section: str = typer.Option("43", "--section", help="Section number as printed in page labels"),
    pages: int = typer.Option(12, "--pages", min=1, help="Number of pages to sample"),
    engine: str = typer.Option("faithful", "--engine", help="faithful | legacy | both"),
    out: Path = typer.Option(Path("workspace/fidelity"), "--out", help="Output folder"),
    label: Optional[str] = typer.Option(None, "--labels", help="Comma separated page labels instead of a sample"),
    keep: bool = typer.Option(False, "--keep-pdf", help="Also write the simulated pages (contain page content)"),
) -> None:
    """Measure how faithful the rendered page is to the original (fonts, sizes, positions, lines). Local, $0."""
    if not pdf.is_file():
        typer.echo("ERROR: --pdf must point to the manual PDF", err=True)
        raise typer.Exit(2)
    engines = ("legacy", "faithful") if engine == "both" else (engine,)
    if any(e not in ("faithful", "legacy") for e in engines):
        typer.echo("ERROR: --engine must be faithful, legacy or both", err=True)
        raise typer.Exit(2)
    fs = FontSet(_legacy_font()) if engine == "legacy" else FontSet()
    rep = analyze(pdf, section, pages, engines, fs, progress=lambda m: typer.echo(m, err=True),
                  labels=[x.strip() for x in label.split(",")] if label else None,
                  keep_dir=(out / "pages") if keep else None)
    if keep:
        (out / "pages").mkdir(parents=True, exist_ok=True)
    j, m = write(rep, out)
    typer.echo(f"Report: {m}")


def _legacy_font() -> Optional[str]:
    return FontSet().face().path
