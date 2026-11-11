"""pilot-report.md (internal check) and review-form.xlsx (workshop). No manual text is written here."""
from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font
from openpyxl.worksheet.datavalidation import DataValidation

from src.terms.excel_io import _safe

from .run import PageOutcome, PilotResult

VERDICTS = ("usable as is", "usable with fixes", "not usable")
TYPE_NAMES = {"text": "prose", "table": "table", "diagram": "diagram/legend", "mixed": "table + diagram",
              "scanned": "image only"}
STATUS_TEXT = {"ok": "ok", "needs_ocr": "needs OCR (skipped)", "failed": "FAILED", "render_failed": "FAILED"}


def _flags(o: PageOutcome) -> str:
    return ", ".join(o.flags) or "-"


def write_report(res: PilotResult, path: Path) -> Path:
    L = [f"# Pilot report: section {res.section}", "",
         f"Model: `{res.model}` (local, $0). Pages: {len(res.outcomes)}. {res.summary_line()}", "",
         "No manual text is stored in this file. Layout flags: `overflow` = text does not fit its box, "
         "`font_below_min` = text shrunk to the minimum size or below, `legend_fallback` = long labels moved "
         "to a numbered legend. Leftover English counts words outside protected names, codes and "
         "abbreviations.", "",
         "| Page | PDF | Type | Result | Segments | Kept as printed | Repaired | To review | Leftover English | Layout flags | Seconds |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for o in res.outcomes:
        L.append(f"| {o.label} | {o.pdf_page} | {TYPE_NAMES.get(o.kind, o.kind)} | {STATUS_TEXT.get(o.status, o.status)}"
                 f"{' (' + o.error + ')' if o.error else ''} | {o.segments} | {o.kept} | {o.repaired} | "
                 f"{len(o.review)} | {o.leaked} | {_flags(o)} | {o.seconds:.1f} |")
    L += ["", "## Segments routed to review", "",
          "Marked in the Vietnamese PDF with a dashed frame, a warning triangle and the text `[!]`. "
          "Safety text (WARNING, CAUTION, DANGER, NOTE) is always routed to a human.", ""]
    any_rev = False
    for o in res.outcomes:
        for r in o.review:
            any_rev = True
            what = "not translated" if r["status"] == "error" else "needs a human check"
            L.append(f"- Page {o.label}, segment {r['id']}: {what} (rules: {r['rules']})")
    if not any_rev:
        L.append("- none")
    todo = [o for o in res.outcomes if o.status != "ok"]
    if todo:
        L += ["", "## Pages that need action", ""]
        for o in todo:
            if o.status == "needs_ocr":
                L.append(f"- Page {o.label}: no text layer. Needs OCR before it can be translated.")
            else:
                L.append(f"- Page {o.label}: processing failed ({o.error or 'error'}). Re-run to retry; finished pages are not redone.")
    if res.warnings:
        L += ["", "## Notes", ""] + [f"- {w}" for w in res.warnings]
    L += ["", "## Files", "", f"- `{res.files['vi'].name}`: Vietnamese-only PDF, original page layout (main deliverable)"]
    if "interleaved" in res.files:
        L.append(f"- `{res.files['interleaved'].name}`: original page, then Vietnamese page (review aid)")
    L += ["- `review-form.xlsx`: one row per page for the workshop", ""]
    path.write_text("\n".join(L), "utf-8")
    return path


def write_form(res: PilotResult, path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "review"
    ws.append(["Page", "PDF page", "Type", "Segments to check", "Verdict", "Comment"])
    for c in ws[1]:
        c.font = Font(bold=True)
    for o in res.outcomes:
        note = {"needs_ocr": "Not translated: scanned page", "failed": "Not translated: error",
                "render_failed": "Not translated: error"}.get(o.status, "")
        ws.append([_safe(v) for v in (o.label, o.pdf_page, TYPE_NAMES.get(o.kind, o.kind), len(o.review), "", note)])
    n = len(res.outcomes) + 1
    dv = DataValidation(type="list", formula1='"' + ",".join(VERDICTS) + '"', allow_blank=True)
    dv.error, dv.errorTitle = "Choose one of the three options.", "Verdict"
    ws.add_data_validation(dv)
    dv.add(f"E2:E{n}")
    for col, w in zip("ABCDEF", (10, 9, 18, 18, 22, 70)):
        ws.column_dimensions[col].width = w
    for row in ws.iter_rows(min_row=2, max_row=n):
        row[5].alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    h = wb.create_sheet("how to fill")
    for line in ("Open the Vietnamese PDF next to the English page.",
                 "Pick one verdict per page: usable as is / usable with fixes / not usable.",
                 "Write what is wrong in the comment: wrong term, wrong number, unclear sentence, layout.",
                 "Pages marked [!] in the PDF were flagged by the system: please check them first."):
        h.append([line])
    h.column_dimensions["A"].width = 100
    wb.save(path)
    return path
