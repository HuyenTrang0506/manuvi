"""Excel export of the proposed electrical glossary (review template for the founder and the uncle)."""
from __future__ import annotations

import csv
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

README = [
    "PROPOSED electrical terms for the Kobelco manual (EN -> VI).",
    "Every Vietnamese term here is a PROPOSAL written by the engineering side. NOTHING is approved.",
    "Reviewer (founder / uncle): fill reviewer_decision with approve | change | reject, write your term in reviewer_vi "
    "when you change it, and add a comment.",
    "Do not edit term_en or status. Approval is recorded by import into the term store (needs a citation and an approver).",
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
        row[2].fill = PatternFill("solid", fgColor="FFF2CC")  # status = proposed
    for col, w in zip("ABCDEFGHIJ", (30, 32, 11, 12, 18, 48, 16, 28, 36, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    xlsx_path = Path(xlsx_path)
    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)
    return xlsx_path
