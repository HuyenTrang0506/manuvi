"""Excel export of the proposed electrical glossary (review template for the founder and the uncle)."""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

README = [
    "Electrical terms for the Kobelco manual (EN -> VI), checked against how Vietnamese mechanics and textbooks write.",
    "status = verified-usage: at least one Vietnamese source (column source) uses the term. status = proposed: no "
    "source found yet; see note. Workshop sign-off is NOT required while usage matches; a mechanic may still change a term.",
    "variants_vi lists accepted spellings and synonyms (separated by |). The eval metric accepts them.",
    "keep_english = yes: true names and abbreviations (ECU, SOL, GND, B+) stay in English. Every other term has a Vietnamese form.",
    "Reviewer (optional): fill reviewer_decision with approve | change | reject and write your term in reviewer_vi.",
    "Keep one Vietnamese term per English term across the whole manual (consistency is measured).",
]


def write_glossary_xlsx(csv_path: Path | str, xlsx_path: Path | str) -> Path:
    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "README"
    for line in README:
        ws0.append([line])
    ws0["A1"].font = Font(bold=True, color="C00000")
    ws0.column_dimensions["A"].width = 120
    ws = wb.create_sheet("terms")
    for r in rows:
        ws.append(r)
    for c in ws[1]:
        c.font = Font(bold=True)
    for row in ws.iter_rows(min_row=2):
        for c in row:
            if isinstance(c.value, str):
                c.data_type = "s"  # never interpreted as a formula
        row[2].fill = PatternFill("solid", fgColor="E2EFDA" if row[2].value == "verified-usage" else "FFF2CC")
    for col, w in zip("ABCDEFGHIJKLM", (30, 32, 15, 12, 18, 48, 16, 28, 36, 14, 36, 60, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    xlsx_path = Path(xlsx_path)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return xlsx_path
