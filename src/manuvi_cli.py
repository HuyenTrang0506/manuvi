"""`manuvi` entry point. Subcommands: `manuvi aws doctor`."""
from __future__ import annotations

import typer

from .llm_gateway.doctor import exit_code, render, run_doctor

app = typer.Typer(help="Manuvi tools.", no_args_is_help=True)
aws_app = typer.Typer(help="AWS wiring checks.", no_args_is_help=True)
app.add_typer(aws_app, name="aws")


@aws_app.command("doctor")
def doctor() -> None:
    """Check credentials, SSM parameters, allow_paid state and the S3 bucket (read-only, no secrets printed)."""
    checks = run_doctor()
    typer.echo(render(checks))
    raise typer.Exit(exit_code(checks))


if __name__ == "__main__":
    app()
