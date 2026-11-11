from __future__ import annotations

from pathlib import Path
from typing import Optional

import httpx
import typer

from .report import write_form, write_report
from .run import PilotError, resolve_model, run_pilot

OLLAMA = "http://localhost:11434"


def _preflight(model: str) -> None:
    if not model.startswith("ollama:"):
        return
    tag = model.split(":", 1)[1]
    try:
        names = {m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=5).json().get("models", [])}
    except Exception:
        raise PilotError("Ollama is not reachable on localhost:11434. Start it with `ollama serve`.")
    if tag not in names and f"{tag}:latest" not in names:
        raise PilotError(f"model {tag} is not installed. Run `ollama pull {tag}`.")


def pilot(
    pdf: Optional[Path] = typer.Option(None, "--pdf", help="Manual PDF"),
    section: str = typer.Option("43", "--section", help="Section number as printed in page labels (43-12)"),
    pages: int = typer.Option(10, "--pages", min=1, help="Number of pages to sample"),
    model: Optional[str] = typer.Option(None, "--model", help="ollama:<tag> (default: profile bulk model)"),
    out: Path = typer.Option(Path("workspace/pilot"), "--out", help="Output folder (re-run to resume)"),
    profile: str = typer.Option("qa", help="Gateway profile: qa (Ollama) or dev (fake, offline tests)"),
    keep: str = typer.Option("", help="Extra names kept in English, comma separated"),
    show_model: bool = typer.Option(False, "--show-model", help="Print the model that would be used and exit"),
) -> None:
    """Translate a sample of real pages into a Vietnamese PDF with the original layout. Local only, $0."""
    try:
        name = resolve_model(profile, model)
        if show_model:
            typer.echo(name)
            return
        if pdf is None or not pdf.is_file():
            raise PilotError("--pdf must point to the manual PDF")
        if profile == "qa":
            _preflight(name)
        out.mkdir(parents=True, exist_ok=True)
        res = run_pilot(pdf, section, pages, out, model=model, profile=profile,
                        keep=tuple(k.strip() for k in keep.split(",") if k.strip()))
    except PilotError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(2)
    write_report(res, out / "pilot-report.md")
    write_form(res, out / "review-form.xlsx")
    typer.echo(res.summary_line())
    typer.echo(f"Output: {out}")
    raise typer.Exit(0 if res.succeeded else 1)
