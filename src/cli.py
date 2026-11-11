"""Manuvi CLI: intake, pipeline, terms, webapp."""
from __future__ import annotations

import typer

from src.intake.cli import intake as _intake_cmd
from src.pipeline.cli import app as _pipeline_app
from src.terms.cli import app as _terms_app
from src.manuvi_cli import aws_app as _aws_app
from src.eval.cli import app as _eval_app
from src.pilot.cli import pilot as _pilot_cmd
from src.formats.cli import translate_file_cmd as _translate_file_cmd
from src.render.fidelity_cli import fidelity as _fidelity_cmd

app = typer.Typer(help="Manuvi: EN -> VI technical manual translation.")
app.command("intake")(_intake_cmd)
app.command("pilot")(_pilot_cmd)
app.command("translate-file")(_translate_file_cmd)
app.command("fidelity")(_fidelity_cmd)
app.add_typer(_pipeline_app, name="pipeline")
app.add_typer(_terms_app, name="terms")
app.add_typer(_aws_app, name="aws")
app.add_typer(_eval_app, name="eval")
web_app = typer.Typer(help="Hosted web portal helpers.")
app.add_typer(web_app, name="web")


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


@app.command()
def worker(
    profile: str = typer.Option("", help="Gateway profile (default: MANUVI_PROFILE, else qa = local Ollama)"),
    work_dir: str = typer.Option("", help="Scratch directory (default: <MANUVI_DATA_DIR>/work)"),
    once: bool = typer.Option(False, help="Process at most one job and exit"),
    visibility: int = typer.Option(300, help="Queue visibility timeout in seconds"),
    heartbeat: float = typer.Option(30.0, help="Heartbeat interval in seconds"),
    batch_size: int = typer.Option(10),
    budget: float = typer.Option(-1.0, help="Per-job USD cap (default: profile cap)"),
) -> None:
    """Pull jobs from the queue and translate them. Outbound connections only; SIGTERM stops gracefully."""
    import logging
    import os

    from src.jobs import data_dir, make_queue, make_store
    from src.worker import Worker

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    w = Worker(make_queue(), make_store(), work_dir or data_dir() / "work",
               profile=profile or os.environ.get("MANUVI_PROFILE", "qa"), visibility=visibility,
               heartbeat=heartbeat, batch_size=batch_size,
               approve_batches=os.environ.get("MANUVI_APPROVE_BATCHES", "1") != "0",
               budget_usd=budget if budget >= 0 else None)
    w.install_signal_handlers()
    if once:
        typer.echo(w.run_once(wait=5) or "idle")
    else:
        w.run_forever()


@web_app.command("hash-password")
def hash_password_cmd() -> None:
    """Print a scrypt hash for MANUVI_WEB_USERS (reads the password from a hidden prompt)."""
    from src.webapp.auth import hash_password

    typer.echo(hash_password(typer.prompt("Password", hide_input=True, confirmation_prompt=True)))


@web_app.command("serve")
def web_serve(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8000),
) -> None:
    """Serve the login-protected portal (users from MANUVI_WEB_USERS; same app as src.webapp.hosted)."""
    import uvicorn

    uvicorn.run("src.webapp.hosted:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    app()
