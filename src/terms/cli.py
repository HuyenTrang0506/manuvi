from __future__ import annotations

import json
from pathlib import Path

import typer

from .excel_io import export_proposed, import_terms_xlsx, write_terms_xlsx
from .store import TermRecord, TermStore

app = typer.Typer(help="Term store: propose, export, import (QA-01, BL-US-06).")


@app.command()
def propose(terms_json: Path, out_xlsx: Path, store_path: Path = typer.Option(None, "--store"),
            include_protected: bool = False) -> None:
    """Candidate terms (intake terms.json) -> proposals Excel with empty VI/citation columns."""
    cands = json.loads(terms_json.read_text(encoding="utf-8"))
    cands = [c for c in cands if include_protected or (not c.get("protected") and c.get("translate", True))]
    if store_path:
        store = TermStore(store_path)
        store.propose(cands)
        export_proposed(store, out_xlsx)
    else:
        seen, recs = set(), []
        for c in cands:
            if c["term"].casefold() not in seen:
                seen.add(c["term"].casefold())
                recs.append(TermRecord(term_en=c["term"], protected=bool(c.get("protected"))))
        write_terms_xlsx(recs, out_xlsx)
    typer.echo(f"wrote {out_xlsx}")


@app.command("export")
def export_cmd(store_path: Path, out_xlsx: Path) -> None:
    export_proposed(TermStore(store_path), out_xlsx)


@app.command("import")
def import_cmd(store_path: Path, xlsx: Path, reviewer: str = "") -> None:
    res = import_terms_xlsx(TermStore(store_path), xlsx, reviewer)
    for i in res.issues:
        typer.echo(f"row {i.row} [{i.code}] {i.term_en}: {i.message}")
    typer.echo(f"ok={res.ok} version={res.version} applied={res.applied}")
    raise typer.Exit(0 if res.ok else 1)


if __name__ == "__main__":
    app()
