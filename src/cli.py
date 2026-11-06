"""Manuvi CLI: intake, pipeline, terms, webapp."""
from __future__ import annotations

import typer

from src.intake.cli import intake as _intake_cmd
from src.pipeline.cli import app as _pipeline_app
from src.terms.cli import app as _terms_app

app = typer.Typer(help="Manuvi: EN -> VI technical manual translation.")
app.command("intake")(_intake_cmd)
app.add_typer(_pipeline_app, name="pipeline")
app.add_typer(_terms_app, name="terms")


@app.command()
def webapp(
    jobdir: str = typer.Option(..., help="Job directory to review"),
    port: int = typer.Option(8765, help="Local port"),
) -> None:
    """Start the local review web app (binds to 127.0.0.1 only)."""
    import uvicorn

    from src.webapp.app import create_app

    web = create_app(jobdir, port=port)
    typer.echo(f"Open: http://127.0.0.1:{port}/?t={web.state.token}")
    uvicorn.run(web, host="127.0.0.1", port=port)


if __name__ == "__main__":
    app()
