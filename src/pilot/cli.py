from __future__ import annotations

from pathlib import Path
from typing import Optional

import typer

from src.llm_gateway.config import resolve_config
from src.llm_gateway.local import OllamaUnreachable, installed_ollama_models, ollama_has

from .report import write_form, write_report
from .run import PilotError, resolve_model, run_pilot


def _preflight(model: str, what: str = "model") -> None:
    if not model.startswith("ollama:"):
        return
    tag = model.split(":", 1)[1]
    try:
        names = installed_ollama_models()
    except OllamaUnreachable:
        raise PilotError("Ollama is not reachable on this machine. Start it with `ollama serve`.")
    if not ollama_has(tag, names):
        raise PilotError(f"{what} {tag} is not installed. Run `ollama pull {tag}`.")


def pilot(
    pdf: Optional[Path] = typer.Option(None, "--pdf", help="Manual PDF"),
    section: str = typer.Option("43", "--section", help="Section number as printed in page labels (43-12)"),
    pages: int = typer.Option(10, "--pages", min=1, help="Number of pages to sample"),
    model: Optional[str] = typer.Option(None, "--model", help="ollama:<tag> (default: profile bulk model)"),
    vision_model: Optional[str] = typer.Option(None, "--vision-model",
                                               help="ollama:<tag> reading pages without text (default: profile vision model)"),
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
            try:
                _preflight(vision_model or resolve_config({"MANUVI_PROFILE": profile}).models["vision"], "vision model")
            except PilotError as e:  # only pages without a text layer need it, so this is a warning
                typer.echo(f"WARNING: {e} Pages without a text layer will fail until it is installed.", err=True)
        out.mkdir(parents=True, exist_ok=True)
        res = run_pilot(pdf, section, pages, out, model=model, profile=profile, vision_model=vision_model,
                        keep=tuple(k.strip() for k in keep.split(",") if k.strip()))
    except PilotError as e:
        typer.echo(f"ERROR: {e}", err=True)
        raise typer.Exit(2)
    write_report(res, out / "pilot-report.md")
    write_form(res, out / "review-form.xlsx")
    typer.echo(res.summary_line())
    typer.echo(f"Output: {out}")
    raise typer.Exit(0 if res.succeeded else 1)
