"""`manuvi translate-file <path> --model ... --out ...`"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer

from .base import FormatError


def translate_file_cmd(
    path: Path = typer.Argument(..., exists=True, dir_okay=False, help=".md, .docx or .pptx"),
    model: str = typer.Option("fake", help="fake | <adapter>:<model_id> e.g. ollama:qwen2.5:7b"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output directory (default: next to the input)"),
    job_dir: Optional[Path] = typer.Option(None, "--job-dir", help="Keep the job directory here (resumable)"),
    budget: Optional[float] = typer.Option(None, help="Hard job cap in USD"),
) -> None:
    """Translate a whole file EN -> VI and write `<name>.vi.<ext>` in the same format, plus segments.json for review."""
    from .job import translate_file

    try:
        res = translate_file(path, out or path.parent, model=model, job_dir=job_dir, budget_usd=budget)
    except FormatError as e:
        typer.echo(f"REJECTED {e.code}: {e}", err=True)
        raise typer.Exit(2)
    typer.echo(json.dumps({k: v for k, v in res.items() if k != "status"}, ensure_ascii=False, indent=1))
    raise typer.Exit(0 if res["state"] == "done" else 4)
