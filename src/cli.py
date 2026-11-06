from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from src.assemble.docx_builder import build_docx
from src.assemble.figure_index import build_figure_index
from src.assemble.pdf_exporter import export_pdf
from src.excel.io import export_workbook, import_workbook
from src.glossary.manager import (
    extract_from_chunks,
    load_glossary,
    load_seed_glossary,
    save_glossary,
)
from src.ingest.parser import (
    load_document,
    parse_pdf,
    save_document,
    save_page_map,
)
from src.qa.checker import run_qa
from src.qa.output_test import run_output_tests
from src.qa.label_validator import run_label_audit
from src.qa.page_classifier import run_page_classification
from src.qa.page_validator import run_page_audit
from src.qa.page_sampler import run_sample_review
from src.translate.auto_captions import auto_figure_captions
from src.translate.apply import apply_from_file
from src.translate.chunker import (
    document_to_chunks,
    load_chunks,
    load_chunks_backup,
    remap_translations_from_backup,
    save_chunks,
)
from src.translate.mark_diagrams import mark_diagram_chunks
from src.translate.remap import remap_all

app = typer.Typer(help="Pipeline dịch Shop Manual Kobelco — chương điện")
from src.intake.cli import intake as _intake_cmd  # noqa: E402

app.command("intake")(_intake_cmd)
console = Console()

DEFAULT_DOC_ID = "sk70sr-1es"
DEFAULT_PDF = Path("data/382014584-sk70sr-1es-shop-manual.pdf")
SEEDS = Path("src/glossary/seeds/construction_electrical_en_vi.csv")


def workspace_path(doc_id: str) -> Path:
    return Path("workspace") / doc_id


@app.command()
def ingest(
    doc_id: str = typer.Option(DEFAULT_DOC_ID, help="Document ID"),
    pdf: Path = typer.Option(DEFAULT_PDF, help="Đường dẫn PDF"),
    sections: str = typer.Option("23,43", help="Sections cần parse, VD: 23,43"),
):
    """Parse PDF → document_model.json + extract hình."""
    if not pdf.exists():
        console.print(f"[red]Không tìm thấy PDF: {pdf}[/red]")
        raise typer.Exit(1)

    ws = workspace_path(doc_id)
    sec_list = [s.strip() for s in sections.split(",")]

    console.print(f"[cyan]Ingesting[/cyan] {pdf.name} sections {sec_list}...")
    document = parse_pdf(pdf, doc_id, sec_list, ws)
    model_path = save_document(document, ws)
    map_path = save_page_map(document, ws)

    console.print(f"[green]✓[/green] document_model: {model_path}")
    console.print(f"[green]✓[/green] page_map: {map_path}")
    console.print(f"  Pages parsed: {len(document.pages)}")
    console.print(f"  Figures: {len(document.figures_registry)}")


@app.command()
def chunk(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    preserve_translations: bool = typer.Option(True, help="Giữ bản dịch cũ theo block_id"),
):
    """Tạo file chunk JSON — 1 block = 1 chunk, giữ cấu trúc text."""
    ws = workspace_path(doc_id)
    document = load_document(ws)
    backup = load_chunks_backup(ws) if preserve_translations else []
    chunks = document_to_chunks(document)
    if backup:
        chunks = remap_translations_from_backup(chunks, backup)
    chunks_dir = save_chunks(chunks, ws)

    review = sum(1 for c in chunks if c.requires_review)
    done = sum(1 for c in chunks if c.status.value == "done")
    console.print(f"[green]✓[/green] {len(chunks)} chunks → {chunks_dir}")
    console.print(f"  Đã dịch (giữ lại): {done} | Cần review: {review}")


@app.command("glossary-init")
def glossary_init(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
):
    """Khởi tạo glossary từ seed + extract từ chunks."""
    ws = workspace_path(doc_id)
    glossary = load_seed_glossary(SEEDS)
    chunks_dir = ws / "chunks"
    if chunks_dir.exists():
        glossary = extract_from_chunks(chunks_dir, glossary)
    path = save_glossary(glossary, ws)
    approved = sum(1 for e in glossary.entries if e.status == "approved")
    console.print(f"[green]✓[/green] glossary: {path}")
    console.print(f"  Entries: {len(glossary.entries)} ({approved} approved)")
    console.print(f"  Review CSV: {ws / 'glossary' / 'glossary_review.csv'}")


@app.command()
def status(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Xem tiến độ dịch chunk."""
    ws = workspace_path(doc_id)
    chunks = load_chunks(ws)
    if not chunks:
        console.print("[yellow]Chưa có chunk. Chạy: ingest → chunk[/yellow]")
        raise typer.Exit(1)

    done = [c for c in chunks if c.status.value == "done"]
    pending = [c for c in chunks if c.status.value == "pending"]
    review = [c for c in chunks if c.requires_review]

    table = Table(title=f"Tiến độ dịch — {doc_id}")
    table.add_column("Metric", style="cyan")
    table.add_column("Count")
    table.add_row("Tổng chunks", str(len(chunks)))
    table.add_row("Đã dịch", f"[green]{len(done)}[/green]")
    table.add_row("Pending", f"[yellow]{len(pending)}[/yellow]")
    table.add_row("Cần review", str(len(review)))
    console.print(table)

    if pending:
        console.print("\n[bold]Pending chunks (10 đầu):[/bold]")
        for c in pending[:10]:
            console.print(f"  {c.chunk_id} | p.{c.pdf_page} | {c.manual_ref} | {c.text_en[:60]}...")


@app.command("list-pending")
def list_pending(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    limit: int = typer.Option(10),
):
    """Liệt kê chunk pending cho Cursor agent."""
    ws = workspace_path(doc_id)
    chunks = load_chunks(ws)
    pending = [c for c in chunks if c.status.value == "pending"][:limit]
    for c in pending:
        console.print(c.chunk_id)


@app.command()
def qa(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Chạy QA kiểm tra bản dịch."""
    ws = workspace_path(doc_id)
    report = run_qa(ws, doc_id)

    table = Table(title="QA Report")
    table.add_column("Metric")
    table.add_column("Value")
    table.add_row("Tổng", str(report.total_chunks))
    table.add_row("Đã dịch", str(report.translated))
    table.add_row("Pending", str(report.pending))
    table.add_row("Issues", str(len(report.issues)))
    console.print(table)

    errors = [i for i in report.issues if i.severity == "error"]
    if errors:
        console.print("\n[red]Errors:[/red]")
        for e in errors[:15]:
            console.print(f"  {e.chunk_id}: {e.message}")
    console.print(f"\nReport: {ws / 'qa' / 'report.json'}")


@app.command()
def assemble(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    title: str = typer.Option("SK70SR-1ES — Chương Điện (Section 23 & 43)"),
    pdf: bool = typer.Option(True, help="Xuất thêm file PDF"),
    source_pdf: Path = typer.Option(DEFAULT_PDF, help="PDF gốc để render ảnh đầy đủ"),
):
    """Ghép DOCX + figure index (+ PDF nếu có Word/LibreOffice)."""
    ws = workspace_path(doc_id)
    out_dir = Path("output") / doc_id / "vi"
    docx_path = out_dir / "section_electrical_vi.docx"
    pdf_path = out_dir / "section_electrical_vi.pdf"

    src_pdf = source_pdf if source_pdf.exists() else None
    if src_pdf:
        console.print(f"[cyan]Source PDF:[/cyan] {src_pdf} (render ảnh đầy đủ)")
    else:
        console.print("[yellow]⚠[/yellow] Không tìm thấy source PDF — dùng ảnh đã trích")

    build_figure_index(ws)
    build_docx(ws, docx_path, title, source_pdf_path=src_pdf)

    console.print(f"[green]✓[/green] DOCX: {docx_path}")
    console.print(f"[green]✓[/green] Figure index: {ws / 'reference' / 'figure_index.md'}")

    if pdf:
        try:
            export_pdf(ws, docx_path, pdf_path, title, source_pdf_path=src_pdf)
            size_mb = pdf_path.stat().st_size / (1024 * 1024)
            console.print(f"[green]✓[/green] PDF: {pdf_path} ({size_mb:.1f} MB)")
        except Exception as e:
            console.print(f"[yellow]⚠[/yellow] Không xuất được PDF: {e}")

    # Tự kiểm tra output (font VI, nhãn, file tồn tại)
    report = run_output_tests(ws, doc_id)
    for c in report.checks:
        icon = "[green]✓[/green]" if c.passed else "[red]✗[/red]"
        console.print(f"  {icon} {c.name}: {c.message}")
    if not report.passed:
        console.print("[yellow]⚠ Output test có lỗi — xem workspace/qa/output_test.json[/yellow]")

    # Kiểm tra từng trang (64 trang)
    _, json_path, md_path, summary = run_page_audit(ws, doc_id, pdf_path if pdf else None)
    console.print(
        f"[cyan]Page audit:[/cyan] ✅ {summary['pass']} | "
        f"⚠️ {summary['warn']} | ❌ {summary['fail']} / {summary['total_pages']} trang"
    )
    console.print(f"  {json_path}")
    console.print(f"  {md_path}")
    if summary["fail"] > 0:
        console.print("[yellow]⚠ Có trang FAIL — xem page_audit.md[/yellow]")


@app.command("classify-pages")
def classify_pages_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Phân loại 64 trang theo category — xử lý batch (sơ đồ, BOM, flowchart…)."""
    ws = workspace_path(doc_id)
    items, json_path, md_path, summary = run_page_classification(ws)
    table = Table(title="Page categories")
    table.add_column("Category")
    table.add_column("Trang")
    table.add_column("Pipeline")
    for cat, n in sorted(summary["by_category"].items(), key=lambda x: -x[1]):
        table.add_row(cat, str(n), summary["handlers"].get(cat, "?"))
    console.print(table)
    console.print(f"\n[green]✓[/green] {len(items)} trang → {json_path}")
    console.print(f"  MD: {md_path}")


@app.command("test-pages")
def test_pages_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    fail_on_error: bool = typer.Option(True, help="Exit 1 nếu có trang FAIL"),
):
    """Kiểm tra chất lượng TỪNG TRANG (chunks, labels, ảnh, PDF font)."""
    ws = workspace_path(doc_id)
    pdf_path = Path("output") / doc_id / "vi" / "section_electrical_vi.pdf"
    audits, json_path, md_path, summary = run_page_audit(ws, doc_id, pdf_path)

    table = Table(title=f"Page audit — {summary['total_pages']} trang")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("✅ Pass", str(summary["pass"]))
    table.add_row("⚠️ Warn", str(summary["warn"]))
    table.add_row("❌ Fail", str(summary["fail"]))
    console.print(table)

    if summary["fail"] or summary["warn"]:
        console.print("\n[bold]Trang có vấn đề:[/bold]")
        for a in audits:
            if a.status != "pass":
                console.print(
                    f"  {'❌' if a.status == 'fail' else '⚠️'} "
                    f"p.{a.pdf_page} ref {a.manual_ref} — {len(a.findings)} finding(s)"
                )

    console.print(f"\n[green]✓[/green] JSON: {json_path}")
    console.print(f"[green]✓[/green] MD:   {md_path}")

    if fail_on_error and summary["fail"] > 0:
        raise typer.Exit(1)


@app.command("test-output")
def test_output_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Tự kiểm tra DOCX/PDF: font tiếng Việt, nhãn Hình 23-3, file tồn tại."""
    ws = workspace_path(doc_id)
    report = run_output_tests(ws, doc_id)
    table = Table(title="Output self-test")
    table.add_column("Check")
    table.add_column("OK")
    table.add_column("Chi tiết")
    for c in report.checks:
        table.add_row(c.name, "✓" if c.passed else "✗", c.message)
    console.print(table)
    if not report.passed:
        raise typer.Exit(1)


@app.command("export-excel")
def export_excel_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    out: Optional[Path] = typer.Option(None, help="Đường dẫn file .xlsx"),
):
    """Xuất chunks + labels + captions + glossary ra Excel để KTV chỉnh sửa."""
    ws = workspace_path(doc_id)
    path = out or ws / "excel" / "translation.xlsx"
    export_workbook(ws, path, doc_id)
    chunks = load_chunks(ws)
    n_labels = sum(
        len(e.labels) for e in load_document(ws).figures_registry.values()
    )
    console.print(f"[green]✓[/green] Excel: {path}")
    console.print(f"  chunks: {len(chunks)} | labels: {n_labels} | captions: {len(load_document(ws).figures_registry)}")
    console.print("  Sửa cột có ✏️ → import-excel → assemble --pdf")


@app.command("import-excel")
def import_excel_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    file: Path = typer.Option(..., help="File .xlsx đã chỉnh"),
):
    """Import bản dịch từ Excel vào workspace (chunks, labels, captions, glossary)."""
    ws = workspace_path(doc_id)
    stats = import_workbook(ws, file)
    table = Table(title="Import Excel")
    table.add_column("Sheet")
    table.add_column("Đã cập nhật")
    for k, v in stats.items():
        table.add_row(k, str(v))
    console.print(table)
    console.print("\n[bold]Tiếp theo:[/bold] python -m src.cli assemble --pdf")


@app.command("review-samples")
def review_samples_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    count: int = typer.Option(8, help="Số trang lấy mẫu"),
    seed: int = typer.Option(42, help="Random seed"),
):
    """Review ngẫu nhiên N trang — so sánh cấu trúc, ref, tiến độ dịch."""
    ws = workspace_path(doc_id)
    reviews, json_path, md_path = run_sample_review(ws, count=count, seed=seed)
    pass_n = sum(1 for r in reviews if r.status == "pass")
    warn_n = sum(1 for r in reviews if r.status == "warn")
    fail_n = sum(1 for r in reviews if r.status == "fail")
    console.print(f"[green]✓[/green] Review {len(reviews)} trang (seed={seed})")
    console.print(f"  ✅ {pass_n} | ⚠️ {warn_n} | ❌ {fail_n}")
    console.print(f"  JSON: {json_path}")
    console.print(f"  MD:   {md_path}")


@app.command("reextract-labels")
def reextract_labels_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    pdf: Path = typer.Option(DEFAULT_PDF, help="PDF gốc để trích lại nhãn"),
):
    """Trích lại nhãn từ PDF gốc (line-by-line) rồi gắn vào figure — không re-ingest toàn bộ."""
    import fitz
    from src.glossary.manager import load_glossary
    from src.ingest.diagram_labels import extract_labels_from_page
    from src.ingest.parser import load_document, save_document
    from src.models import BlockType

    if not pdf.exists():
        console.print(f"[red]Không tìm thấy PDF: {pdf}[/red]")
        raise typer.Exit(1)

    ws = workspace_path(doc_id)
    glossary = load_glossary(ws)
    document = load_document(ws)
    src = fitz.open(str(pdf))
    total = 0
    pages_done = 0
    for page in document.pages:
        fig_blocks = [b for b in page.blocks if b.type == BlockType.FIGURE]
        if not fig_blocks:
            continue
        pdf_idx = page.pdf_page - 1
        if pdf_idx < 0 or pdf_idx >= len(src):
            continue
        labels = extract_labels_from_page(src[pdf_idx], glossary)
        if not labels:
            continue
        pages_done += 1
        total += len(labels)
        for b in fig_blocks:
            b.labels = list(labels)
            if b.figure_id and b.figure_id in document.figures_registry:
                document.figures_registry[b.figure_id].labels = list(labels)

    src.close()
    save_document(document, ws)
    console.print(f"[green]✓[/green] Re-extract {total} nhãn trên {pages_done} trang")
    console.print("Chạy: retranslate-labels → assemble --pdf")


@app.command("retranslate-labels")
def retranslate_labels_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Dịch lại toàn bộ nhãn sơ đồ (figures + blocks)."""
    from src.glossary.manager import load_glossary
    from src.ingest.parser import load_document, save_document
    from src.translate.label_translator import retranslate_all_labels

    ws = workspace_path(doc_id)
    glossary = load_glossary(ws)
    document = load_document(ws)
    n = retranslate_all_labels(document, glossary)
    save_document(document, ws)
    console.print(f"[green]✓[/green] Dịch lại {n} nhãn sơ đồ")
    _run_label_unit_tests()
    console.print("Chạy: verify-labels → assemble --pdf")


def _run_label_unit_tests() -> None:
    import unittest

    loader = unittest.TestLoader()
    suite = loader.discover("tests", pattern="test_label_semantics.py")
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    if result.wasSuccessful():
        console.print("[green]✓[/green] Unit test nhãn: pass")
    else:
        console.print(f"[red]✗[/red] Unit test nhãn: {len(result.failures)} fail, {len(result.errors)} error")
        raise typer.Exit(1)


@app.command("test-legend")
def test_legend_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    fail_on_error: bool = typer.Option(True, help="Exit 1 nếu lẫn EN/VI hoặc thiếu VI"),
    check_bom: bool = typer.Option(True, help="Fail nếu BOM/list còn EN sót"),
):
    """Audit bảng chú thích: sort theo mã, thiếu VI, lẫn Anh–Việt, số chân."""
    from src.qa.legend_auditor import audit_legend, write_legend_audit
    from src.qa.untranslated_collector import collect_untranslated, write_untranslated_report

    ws = workspace_path(doc_id)
    audit = audit_legend(ws)
    jp, mp = write_legend_audit(ws, audit)
    table = Table(title="Legend audit")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("Figures có legend", str(audit.figures))
    table.add_row("Dòng tra cứu", str(audit.rows))
    table.add_row("🔴 Lẫn EN/VI", str(audit.mixed))
    table.add_row("🔵 Thiếu VI", str(audit.missing_vi))
    table.add_row("🟠 Số chân", str(audit.pins))
    table.add_row("🟡 Sort sai", str(audit.sort_errors))
    console.print(table)
    console.print(f"  {jp}")
    console.print(f"  {mp}")
    bad = audit.mixed + audit.missing_vi + audit.pins + audit.sort_errors

    bom_bad = 0
    if check_bom:
        from src.glossary.manager import load_glossary
        from src.translate.list_translator import translate_line_list

        terms = collect_untranslated(ws)
        must_vi = {
            "MOUNTING BOOM LIGHT",
            "MOUNTING CAB",
            "MOUNTING LIGHT",
            "WASHER TANK",
            "CIGALETTE LIGHTER",
            "CIGARETTE LIGHTER",
            "KYE SWITCH",
            "KEY SWITCH",
            "ACCEL POTENTIO DIAL",
            "WINDOW WASHER TANK & PUMP",
        }
        glossary = load_glossary(ws)
        leftover = []
        for t in terms:
            if t.term_en not in must_vi:
                continue
            # Xác minh phrase map đã cover — nếu dịch được thì không fail
            got = translate_line_list(t.term_en.title() if t.term_en.isupper() else t.term_en, glossary)
            # normalize: try exact upper too
            got2 = translate_line_list(t.term_en, glossary)
            vi = got if got.upper() != t.term_en.upper() else got2
            if vi.upper() == t.term_en.upper() or (t.sample_vi and t.sample_vi.upper() == t.term_en.upper()):
                leftover.append(t)
        bom_bad = len(leftover)
        uj, um, uc = write_untranslated_report(ws, terms)
        console.print(f"  Untranslated terms: {len(terms)} (BOM must-fix leftover: {bom_bad})")
        console.print(f"  {uj}")
        if leftover:
            for t in leftover[:12]:
                console.print(f"    [red]•[/red] {t.term_en} ({t.kind}) @ {t.sample_ref}")

    if fail_on_error and (bad or bom_bad):
        console.print("[red]Legend/BOM audit FAIL[/red]")
        raise typer.Exit(1)
    console.print("[green]✓[/green] Legend audit OK")


@app.command("collect-untranslated")
def collect_untranslated_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    top: int = typer.Option(40, help="In top N cụm"),
):
    """Quét EN còn sót trong nhãn / BOM / list → qa/untranslated_terms.*"""
    from src.qa.untranslated_collector import collect_untranslated, write_untranslated_report

    ws = workspace_path(doc_id)
    terms = collect_untranslated(ws)
    jp, mp, cp = write_untranslated_report(ws, terms)
    table = Table(title=f"Untranslated ({len(terms)})")
    table.add_column("N", justify="right")
    table.add_column("Kind")
    table.add_column("Term EN")
    table.add_column("Sample VI")
    table.add_column("Ref")
    for t in terms[:top]:
        table.add_row(str(t.count), t.kind, t.term_en[:40], (t.sample_vi or "—")[:30], t.sample_ref)
    console.print(table)
    console.print(f"[green]✓[/green] {jp}")
    console.print(f"  {mp}")
    console.print(f"  {cp} (glossary seed stub)")


@app.command("audit-mix")
def audit_mix_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    top: int = typer.Option(40, help="In top N dòng lẫn"),
):
    """Quét lẫn Anh–Việt trong chunks + nhãn (detector mới)."""
    from src.ingest.parser import load_document
    from src.qa.mix_detector import is_mixed_vi_en, residual_english_tokens, scan_text_lines
    from src.translate.chunker import load_chunks

    ws = workspace_path(doc_id)
    rows = []
    for c in load_chunks(ws):
        for ln, toks in scan_text_lines(c.text_vi or ""):
            rows.append(("chunk", c.manual_ref or "", c.chunk_id, ln, ",".join(toks)))
    doc = load_document(ws)
    for fig in doc.figures_registry.values():
        for lb in fig.labels or []:
            vi = lb.text_vi or ""
            if is_mixed_vi_en(vi):
                rows.append(
                    (
                        "label",
                        fig.figure_id,
                        "",
                        vi[:100],
                        ",".join(residual_english_tokens(vi)[:8]),
                    )
                )
    table = Table(title=f"EN/VI mix ({len(rows)})")
    table.add_column("Kind")
    table.add_column("Ref")
    table.add_column("Text")
    table.add_column("EN tokens")
    for r in rows[:top]:
        table.add_row(r[0], r[1][:20], r[3][:55], r[4][:30])
    console.print(table)
    out = ws / "qa" / "mix_audit.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    import json as _json

    out.write_text(
        _json.dumps(
            [{"kind": a, "ref": b, "id": c, "text": d, "tokens": e} for a, b, c, d, e in rows],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    console.print(f"[green]✓[/green] {out} ({len(rows)} findings)")
    if rows:
        raise typer.Exit(1)


@app.command("hybrid-probe")
def hybrid_probe_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    pages: str = typer.Option("663,666", help="PDF page numbers, comma-separated"),
):
    """Thử hybrid extract (PyMuPDF + map; VLM nếu có API key) trên vài trang."""
    from src.translate.hybrid_page import hybrid_extract_page, write_hybrid_report

    ws = workspace_path(doc_id)
    pdf = Path("data/382014584-sk70sr-1es-shop-manual.pdf")
    if not pdf.exists():
        console.print(f"[red]Thiếu PDF:[/red] {pdf}")
        raise typer.Exit(1)
    results = []
    for raw in pages.split(","):
        pno = int(raw.strip())
        ref = f"43-{pno - 660}" if 661 <= pno <= 682 else f"PDF-{pno}"
        results.append(hybrid_extract_page(pdf, manual_ref=ref, pdf_page=pno, workspace=ws))
    out = write_hybrid_report(ws, results)
    for r in results:
        console.print(f"  {r.manual_ref} p{r.pdf_page}: {r.source}, {len(r.blocks)} blocks")
    console.print(f"[green]✓[/green] {out}")


@app.command("vision-legends")
def vision_legends_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    model: str = typer.Option("gpt-4o", help="gpt-4o | gpt-4.1 | claude-…"),
    force: bool = typer.Option(False, help="Ghi đè JSON đã có"),
    dry_run: bool = typer.Option(False, help="Chỉ liệt kê job, không gọi API"),
    only: str = typer.Option("", help="Chỉ các figure_id, cách nhau bởi dấu phẩy (mặc định: TẤT CẢ)"),
    sleep: float = typer.Option(0.8, help="Nghỉ giữa các ảnh (giây)"),
    min_bytes: int = typer.Option(8000, help="Bỏ ảnh nhỏ hơn N bytes"),
):
    """Batch Vision GPT: đọc TỪNG hình → bảng EN→VI (resume được, không bỏ sót)."""
    from src.translate.vision_legend import list_figure_jobs, run_batch, vision_dir

    ws = workspace_path(doc_id)
    jobs = list_figure_jobs(ws, min_bytes=min_bytes)
    only_ids = [x.strip() for x in only.split(",") if x.strip()] or None
    if only_ids:
        jobs = [j for j in jobs if j["figure_id"] in set(only_ids)]

    table = Table(title=f"Vision legend jobs ({len(jobs)})")
    table.add_column("#", justify="right")
    table.add_column("figure_id")
    table.add_column("No")
    table.add_column("KB", justify="right")
    table.add_column("Status")
    for i, j in enumerate(jobs, 1):
        st = j["skip_reason"] or "READY"
        table.add_row(str(i), j["figure_id"], j.get("figure_number") or "—", str(j["bytes"] // 1024), st)
    console.print(table)

    if dry_run:
        console.print("[yellow]dry-run — chưa gọi API[/yellow]")
        console.print(f"Output dir: {vision_dir(ws)}")
        return

    def _prog(i, total, job):
        console.print(f"[{i}/{total}] {job['figure_id']} ({job.get('figure_number') or '—'}) …")

    try:
        results = run_batch(
            ws,
            model=model,
            force=force,
            only=only_ids,
            sleep_s=sleep,
            min_bytes=min_bytes,
            progress_cb=_prog,
        )
    except RuntimeError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(1)

    ok = sum(1 for r in results if not r.error and not (r.skipped and r.skip_reason not in ("cached", "image_too_small", "missing_image")))
    cached = sum(1 for r in results if r.skip_reason == "cached")
    errs = [r for r in results if r.error]
    labels_n = sum(len(r.labels) for r in results)
    console.print(
        f"[green]✓[/green] Xong {len(results)} hình | nhãn={labels_n} | cached={cached} | lỗi={len(errs)}"
    )
    console.print(f"  {vision_dir(ws)}/_manifest.json")
    if errs:
        for r in errs[:12]:
            console.print(f"  [red]{r.figure_id}[/red]: {r.error}")
        raise typer.Exit(1)
    console.print("Tiếp: assemble --pdf  (bảng Vision hiện dưới mỗi hình)")


@app.command("vision-text")
def vision_text_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    model: str = typer.Option("gpt-4o", help="gpt-4o | gpt-4.1 | claude-… (tự dùng engine nếu không có API key)"),
    force: bool = typer.Option(False, help="Ghi đè cache đã có"),
    dry_run: bool = typer.Option(False, help="Chỉ liệt kê unit cần dịch, không gọi API"),
    only: str = typer.Option("", help="Chỉ chunk_id cụ thể, cách nhau bởi dấu phẩy"),
    include_mixed: bool = typer.Option(True, help="Bao gồm chunk còn lẫn EN/VI"),
    apply: bool = typer.Option(True, help="Áp dụng cache vào chunk JSON sau khi dịch"),
    no_vision: bool = typer.Option(False, help="Không đính kèm ảnh trang nguồn (chỉ text)"),
    source_pdf: Path = typer.Option(DEFAULT_PDF, help="PDF gốc để render ảnh context"),
    sleep: float = typer.Option(0.3, help="Nghỉ giữa các chunk (giây)"),
):
    """Batch dịch văn bản — for-loop TỪNG chunk (resume, cache, không bỏ sót).

    Cache: workspace/<doc>/translations/vision_text/<chunk_id>.json
    Dùng LLM nếu có OPENAI_API_KEY / ANTHROPIC_API_KEY; ngược lại dùng engine rule-based.
    """
    from src.translate.batch_text import (
        apply_cached_translations,
        list_pending_text_units,
        run_batch_text,
    )

    ws = workspace_path(doc_id)
    src_pdf = source_pdf if source_pdf.exists() else None
    only_ids = [x.strip() for x in only.split(",") if x.strip()] or None

    # Liệt kê tất cả unit trước
    jobs = list_pending_text_units(ws, include_mixed=include_mixed, force=force, only=only_ids)
    active = [j for j in jobs if not j["skip_reason"]]
    cached_clean = [j for j in jobs if j["skip_reason"] == "cached_clean"]

    table = Table(title=f"Batch text units ({len(active)} active / {len(cached_clean)} cached)")
    table.add_column("#", justify="right")
    table.add_column("chunk_id")
    table.add_column("Ref")
    table.add_column("Reason")
    table.add_column("EN (60c)")
    for i, j in enumerate(active[:50], 1):
        table.add_row(str(i), j["chunk_id"], j["manual_ref"], j["reason"], j["text_en"][:60])
    if len(active) > 50:
        table.add_row("…", f"(+{len(active) - 50} thêm)", "", "", "")
    console.print(table)
    console.print(
        f"Tổng: {len(jobs)} | Cần xử lý: {len(active)} | Cache sạch: {len(cached_clean)}"
    )

    if dry_run:
        console.print("[yellow]dry-run — chưa dịch[/yellow]")
        return

    if not active:
        console.print("[green]Tất cả unit đã có cache sạch — không cần dịch thêm[/green]")
        if apply:
            n = apply_cached_translations(ws)
            console.print(f"[green]✓[/green] Áp dụng {n} chunk từ cache")
        return

    def _prog(i, total, job):
        console.print(
            f"[{i}/{total}] {job['chunk_id']} ({job['manual_ref']}) reason={job['reason']} …"
        )

    results = run_batch_text(
        ws,
        model=model,
        force=force,
        include_mixed=include_mixed,
        only=only_ids,
        source_pdf_path=src_pdf,
        use_vision_context=not no_vision,
        sleep_s=sleep,
        progress_cb=_prog,
        dry_run=False,
    )

    ok = sum(1 for r in results if r.text_vi and not r.error and not r.skipped)
    errs = [r for r in results if r.error]
    skipped = [r for r in results if r.skipped and r.skip_reason not in ("dry_run",)]
    console.print(
        f"[green]✓[/green] Dịch xong {ok} chunk | cached/skip={len(skipped)} | lỗi={len(errs)}"
    )
    if errs:
        for r in errs[:8]:
            console.print(f"  [red]{r.chunk_id}[/red]: {r.error}")

    if apply:
        n = apply_cached_translations(ws)
        console.print(f"[green]✓[/green] Áp dụng {n} chunk vào workspace từ cache")
        console.print("Tiếp: assemble --pdf  để cập nhật output")


@app.command("ocr-supplement")
def ocr_supplement_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    force: bool = typer.Option(False, help="Chạy lại dù đã supplement trước đó"),
    only: str = typer.Option("", help="Chỉ các figure_id (cách nhau bởi dấu phẩy)"),
    min_conf: float = typer.Option(0.75, help="Ngưỡng độ tin cậy OCR (0–1)"),
):
    """OCR CPU (RapidOCR) bổ sung nhãn còn thiếu vào vision legend JSON.

    Vision translation được ưu tiên; OCR chỉ thêm nhãn chưa có.
    Yêu cầu: rapidocr-onnxruntime (python -m pip install rapidocr-onnxruntime).
    """
    try:
        from src.translate.ocr_labels import run_ocr_supplement_batch
    except ImportError as e:
        console.print(f"[red]Thiếu gói: {e}[/red]")
        raise typer.Exit(1)

    ws = workspace_path(doc_id)
    only_ids = [x.strip() for x in only.split(",") if x.strip()] or None

    def _prog(i, total, fid):
        console.print(f"[{i}/{total}] OCR → {fid} …")

    results = run_ocr_supplement_batch(
        ws, only=only_ids, force=force, min_conf=min_conf, progress_cb=_prog
    )
    total_added = sum(r["added"] for r in results)
    errors = [r for r in results if r["status"] != "ok"]
    console.print(
        f"[green]✓[/green] {len(results)} hình | nhãn OCR thêm={total_added} | lỗi={len(errors)}"
    )
    if errors:
        for r in errors[:10]:
            console.print(f"  [red]{r['figure_id']}[/red]: {r['status']}")
    console.print("Tiếp: assemble --pdf  (Vision + OCR bổ sung trong bảng chú thích)")


@app.command("write-manifest")
def write_manifest_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
):
    """Ghi _manifest.json tổng hợp từ tất cả vision legend JSON đã có."""
    from src.translate.vision_legend import vision_dir

    ws = workspace_path(doc_id)
    vdir = vision_dir(ws)
    jsons = sorted(p for p in vdir.glob("*.json") if not p.name.startswith("_"))
    rows = []
    for jpath in jsons:
        try:
            d = json.loads(jpath.read_text(encoding="utf-8"))
            rows.append(
                {
                    "figure_id": d.get("figure_id", jpath.stem),
                    "figure_number": d.get("figure_number", ""),
                    "n_labels": len(d.get("labels") or []),
                    "model": d.get("model", ""),
                    "error": d.get("error"),
                    "skipped": d.get("skipped", False),
                    "skip_reason": d.get("skip_reason", ""),
                    "ocr_supplemented": d.get("ocr_supplemented", False),
                    "ocr_labels_added": d.get("ocr_labels_added", 0),
                    "file": jpath.name,
                }
            )
        except Exception as exc:
            console.print(f"[yellow]⚠[/yellow] {jpath.name}: {exc}")
    man_path = vdir / "_manifest.json"
    man_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"[green]✓[/green] {man_path} ({len(rows)} hình)")
    # quick summary
    ready = sum(1 for r in rows if not r["error"] and not r["skipped"])
    labels_total = sum(r["n_labels"] for r in rows)
    console.print(f"  READY={ready} | total_labels={labels_total}")


@app.command("test-labels")
def test_labels_cmd():
    """Chạy unit test dịch semantic nhãn SE/SV/R."""
    _run_label_unit_tests()


@app.command("verify-labels")
def verify_labels_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    fail_on_error: bool = typer.Option(True, help="Exit 1 nếu còn nhãn EN/VI lẫn"),
):
    """Kiểm tra nhãn sơ đồ — phát hiện dịch lẫn Anh-Việt, chưa dịch."""
    ws = workspace_path(doc_id)
    audit, json_path, md_path = run_label_audit(ws)
    table = Table(title="Label audit")
    table.add_column("Metric")
    table.add_column("Count")
    table.add_row("Tổng nhãn", str(audit.total))
    table.add_row("🔴 EN/VI lẫn", str(audit.mixed))
    table.add_row("🔵 Chưa dịch", str(audit.untranslated))
    table.add_row("🟠 OCR", str(audit.ocr))
    console.print(table)
    if audit.mixed:
        console.print("\n[yellow]Ví dụ EN/VI lẫn:[/yellow]")
        for f in audit.findings:
            if f.issue == "mixed":
                console.print(f"  {f.text_en} → {f.text_vi}")
                if len([x for x in audit.findings if x.issue == "mixed"]) > 8:
                    break
    console.print(f"\n[green]✓[/green] {json_path}")
    console.print(f"  MD: {md_path}")
    if fail_on_error and audit.mixed > 0:
        raise typer.Exit(1)


@app.command("translate-all")
def translate_all_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Dịch tất cả chunk pending (engine glossary + phrase rules)."""
    from src.translate.engine import translate_all_pending

    ws = workspace_path(doc_id)
    n = translate_all_pending(ws)
    console.print(f"[green]✓[/green] Đã dịch {n} chunk")
    status(doc_id=doc_id)


@app.command("remap-translations")
def remap_translations_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Khôi phục bản dịch từ chunks_vi + translations.json."""
    ws = workspace_path(doc_id)
    n = remap_all(ws)
    console.print(f"[green]✓[/green] Remapped {n} chunks")
    status(doc_id=doc_id)


@app.command("auto-captions")
def auto_captions_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Tự động dịch chunk caption hình (Fig. XX-YY)."""
    ws = workspace_path(doc_id)
    n = auto_figure_captions(ws)
    console.print(f"[green]✓[/green] Auto-caption: {n} chunks")
    status(doc_id=doc_id)


@app.command("export-pending")
def export_pending(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    limit: int = typer.Option(10),
    out: Path = typer.Option(Path("workspace/pending_batch.md")),
):
    """Xuất batch chunk pending cho Cursor agent dịch."""
    ws = workspace_path(doc_id)
    chunks = [c for c in load_chunks(ws) if c.status.value == "pending"][:limit]
    lines = [
        "# Pending chunks — dịch EN→VI",
        "",
        "Đọc glossary tại workspace/sk70sr-1es/glossary/glossary.json",
        "",
    ]
    for c in chunks:
        lines.append(f"## {c.chunk_id} (p.{c.pdf_page} | {c.manual_ref})")
        lines.append("```")
        lines.append(c.text_en)
        lines.append("```")
        lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    console.print(f"[green]✓[/green] Exported {len(chunks)} chunks → {out}")


@app.command("fix-chunks")
def fix_chunks_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Sửa chunk Section 43 bị remap sai + áp dụng rebatch translations."""
    from src.translate.fix_chunks import fix_all_corrupted

    ws = workspace_path(doc_id)
    n, ids = fix_all_corrupted(ws)
    console.print(f"[green]✓[/green] Đã sửa {n} chunk")
    if ids:
        console.print(f"  Ví dụ: {', '.join(ids[:8])}{'...' if len(ids) > 8 else ''}")
    console.print("Chạy: assemble --pdf → test-pages")


@app.command("apply-translations")
def apply_translations_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    file: Path = typer.Option(..., help="JSON file: {chunk_id: text_vi}"),
    force: bool = typer.Option(False, help="Ghi đè chunk đã done"),
):
    """Áp dụng bản dịch từ file JSON (sau khi Cursor agent dịch)."""
    ws = workspace_path(doc_id)
    n = apply_from_file(ws, file, only_pending=not force)
    console.print(f"[green]✓[/green] Applied {n} translations")
    status(doc_id=doc_id)


@app.command("mark-diagrams")
def mark_diagrams_cmd(doc_id: str = typer.Option(DEFAULT_DOC_ID)):
    """Đánh dấu chunk wiring diagram — không cần dịch (xem ảnh gốc)."""
    ws = workspace_path(doc_id)
    n = mark_diagram_chunks(ws)
    console.print(f"[green]✓[/green] Đã đánh dấu {n} chunk sơ đồ (skip dịch)")
    status(doc_id=doc_id)


@app.command("section43-redraw")
def section43_redraw_cmd(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    force: bool = typer.Option(False, help="Vẽ lại dù đã có PNG"),
    dry_run: bool = typer.Option(False, help="Chỉ liệt kê, không vẽ"),
    only: str = typer.Option("", help="Chỉ vẽ các trang cụ thể, VD: 43-13,43-14"),
):
    """Vẽ lại lưu đồ Section 43 bằng tiếng Việt (for-loop, resume/cache).

    Mỗi trang flowchart → 1 ảnh PNG tiếng Việt tại:
      workspace/<doc>/assets/flowcharts_vi/43-*.png

    Sau khi vẽ: assemble --pdf để nhúng ảnh VI vào DOCX/PDF.
    """
    from src.translate.flowchart_redraw import enumerate_flowchart_pages, run_redraw_batch

    ws = workspace_path(doc_id)
    only_list = [x.strip() for x in only.split(",") if x.strip()] or None

    # Dry-run: liệt kê trước
    pages = enumerate_flowchart_pages(ws)
    pages = [p for p in pages if p["chunk_count"] >= 3]
    if only_list:
        pages = [p for p in pages if p["manual_ref"] in set(only_list)]

    table = Table(title=f"Section 43 flowchart pages ({len(pages)} trang)")
    table.add_column("#", justify="right")
    table.add_column("Ref")
    table.add_column("PDF p.")
    table.add_column("Chunks")
    table.add_column("Status")
    from src.translate.flowchart_redraw import _flowchart_dir
    fc_dir = _flowchart_dir(ws)
    for i, p in enumerate(pages, 1):
        png = fc_dir / f"{p['manual_ref']}.png"
        st = "✓ cached" if (png.exists() and not force) else "READY"
        table.add_row(str(i), p["manual_ref"], str(p["pdf_page"]), str(p["chunk_count"]), st)
    console.print(table)

    if dry_run:
        console.print("[yellow]dry-run — không vẽ[/yellow]")
        console.print(f"Output dir: {fc_dir}")
        return

    def _prog(i, total, page_info):
        console.print(f"[{i}/{total}] Vẽ {page_info['manual_ref']} (PDF p.{page_info['pdf_page']}, {page_info['chunk_count']} chunks) …")

    results = run_redraw_batch(ws, force=force, only=only_list, progress_cb=_prog, dry_run=False)

    drawn = [r for r in results if r["status"] == "drawn"]
    cached = [r for r in results if r["status"] == "cached"]
    errors = [r for r in results if r["status"] == "error"]
    console.print(
        f"\n[green]✓[/green] Vẽ xong {len(drawn)} trang | cached={len(cached)} | lỗi={len(errors)}"
    )
    if errors:
        for r in errors[:10]:
            console.print(f"  [red]{r['manual_ref']}[/red]: {r['error']}")
        raise typer.Exit(1)
    console.print(f"  PNG dir: {fc_dir}")
    console.print("Tiếp: assemble --pdf  (nhúng ảnh VI vào DOCX/PDF)")


@app.command("run-all")
def run_all(
    doc_id: str = typer.Option(DEFAULT_DOC_ID),
    pdf: Path = typer.Option(DEFAULT_PDF),
):
    """Chạy ingest → chunk → glossary → (chờ dịch) → assemble prep."""
    ingest(doc_id=doc_id, pdf=pdf, sections="23,43")
    chunk(doc_id=doc_id)
    glossary_init(doc_id=doc_id)
    mark_diagram_chunks(workspace_path(doc_id))
    auto_figure_captions(workspace_path(doc_id))
    status(doc_id=doc_id)
    console.print("\n[bold green]Pipeline sẵn sàng cho bước dịch![/bold green]")
    console.print("Tiếp theo: mở Cursor agent, đọc prompts/translate_electrical.md")
    console.print(f"  Dịch batch pending trong workspace/{doc_id}/chunks/")


if __name__ == "__main__":
    app()
