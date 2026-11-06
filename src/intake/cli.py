from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .classify import classify_pdf, load_overrides
from .jobspec import build_jobspec, write_jobspec
from .terms import extract_terms, load_patterns

app = typer.Typer(help="Intake: JobSpec, page classes, term candidates (offline, no LLM).")


@app.command()
def intake(
    pdf: Path = typer.Argument(..., exists=True),
    sections: str = typer.Option(..., help='e.g. "23:1-42,43:1-22"'),
    out_dir: Path = typer.Option(Path("workspace/intake"), help="Output directory"),
    overrides: Optional[Path] = typer.Option(None, help="overrides.yaml (wins over auto classes)"),
    patterns: Optional[Path] = typer.Option(None, help="protected code patterns YAML"),
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = build_jobspec(pdf, sections)
    write_jobspec(spec, out_dir / "jobspec.json")
    classes = classify_pdf(pdf, load_overrides(overrides))
    (out_dir / "page_classes.json").write_text(
        json.dumps([c.model_dump() for c in classes], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    scanned = set(spec.scanned_pages)
    terms = extract_terms(pdf, load_patterns(patterns),
                          pages=[p.pdf_page for p in spec.pages if p.pdf_page not in scanned])
    (out_dir / "terms.json").write_text(
        json.dumps([t.model_dump() for t in terms], indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    for w in spec.warnings:
        typer.echo(f"WARNING: {w}")
    typer.echo(f"{spec.page_count} pages, {len(spec.scanned_pages)} scanned, {len(terms)} term entries -> {out_dir}")


if __name__ == "__main__":
    app()
