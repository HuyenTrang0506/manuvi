"""Xuất / nhập Excel — chỉnh sửa bản dịch ngoài pipeline rồi import lại output."""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Tuple

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from src.glossary.manager import load_glossary, save_glossary
from src.ingest.parser import load_document, save_document
from src.ingest.text_structure import clean_for_xml
from src.models import BlockStatus, GlossaryEntry, LabelNote
from src.translate.chunker import load_chunks, save_chunk

SHEETS = ("README", "chunks", "labels", "captions", "glossary")
HEADER_FILL = PatternFill("solid", fgColor="1F4E79")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LOCK_FILL = PatternFill("solid", fgColor="F2F2F2")
WRAP = Alignment(wrap_text=True, vertical="top")

CHUNK_COLS = [
    ("chunk_id", "ID chunk (khóa)", False),
    ("pdf_page", "Trang PDF", False),
    ("manual_ref", "Số sổ tay", False),
    ("block_type", "Loại block", False),
    ("status", "Trạng thái", False),
    ("text_en", "Tiếng Anh (gốc)", False),
    ("text_vi", "Tiếng Việt ✏️ SỬA CỘT NÀY", True),
    ("review_notes", "Ghi chú review", True),
]

LABEL_COLS = [
    ("figure_id", "ID hình", False),
    ("figure_number", "Số hình", False),
    ("pdf_page", "Trang PDF", False),
    ("label_en", "Ký hiệu gốc", False),
    ("label_vi", "Nghĩa tiếng Việt ✏️ SỬA CỘT NÀY", True),
    ("suspicion", "OCR nghi ngờ (yes/no)", True),
    ("guess_note", "Ghi chú đoán ngữ cảnh", False),
    ("review_notes", "Ghi chú review KTV", True),
]

CAPTION_COLS = [
    ("figure_id", "ID hình", False),
    ("figure_number", "Số hình", False),
    ("pdf_pages", "Trang PDF", False),
    ("caption_en", "Caption EN", False),
    ("caption_vi", "Caption VI ✏️ SỬA CỘT NÀY", True),
    ("review_notes", "Ghi chú review", True),
]

GLOSSARY_COLS = [
    ("id", "ID", False),
    ("term_en", "Thuật ngữ EN", False),
    ("term_vi", "Nghĩa VI ✏️ SỬA CỘT NÀY", True),
    ("status", "Trạng thái (draft/approved)", True),
    ("frequency", "Tần suất", False),
    ("do_not_translate", "Không dịch", False),
    ("notes", "Ghi chú", True),
]


def _write_headers(ws: Worksheet, cols: List[Tuple[str, str, bool]]) -> None:
    for i, (key, title, editable) in enumerate(cols, 1):
        cell = ws.cell(row=1, column=i, value=title)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL if editable else PatternFill("solid", fgColor="2F5496")
        cell.alignment = WRAP
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 36


def _autosize(ws: Worksheet, widths: Dict[int, int]) -> None:
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w


def _write_readme(ws: Worksheet) -> None:
    lines = [
        "TRANSLATOR ELECTRONIC — Excel workflow",
        f"Cập nhật: {date.today()}",
        "",
        "1. Chỉ sửa các cột có ✏️ (text_vi, label_vi, caption_vi, term_vi, review_notes, status glossary).",
        "2. KHÔNG đổi chunk_id, figure_id, text_en, label_en — dùng làm khóa import.",
        "3. Giữ xuống dòng trong ô Excel (Alt+Enter) — sẽ import đúng cấu trúc.",
        "4. Sau khi sửa: python -m src.cli import-excel --file workspace/.../excel/translation.xlsx",
        "5. Build output: python -m src.cli assemble --pdf",
        "",
        "Sheets:",
        "  chunks   — văn bản đoạn / TOC / flowchart",
        "  labels   — chú thích ký hiệu trên sơ đồ mạch",
        "  captions — tiêu đề hình Fig. 23-XX",
        "  glossary — thuật ngữ EN→VI",
    ]
    for i, line in enumerate(lines, 1):
        ws.cell(row=i, column=1, value=line)
    ws.column_dimensions["A"].width = 100


def export_workbook(workspace: Path, output_path: Path, doc_id: str) -> Path:
    document = load_document(workspace)
    chunks = load_chunks(workspace)
    glossary = load_glossary(workspace)

    wb = Workbook()
    ws_readme = wb.active
    ws_readme.title = "README"
    _write_readme(ws_readme)

    # --- chunks ---
    ws_c = wb.create_sheet("chunks")
    _write_headers(ws_c, CHUNK_COLS)
    for r, c in enumerate(chunks, 2):
        ws_c.cell(r, 1, c.chunk_id)
        ws_c.cell(r, 2, c.pdf_page)
        ws_c.cell(r, 3, c.manual_ref or "")
        ws_c.cell(r, 4, c.block_type)
        ws_c.cell(r, 5, c.status.value)
        ws_c.cell(r, 6, _xls(c.text_en)).alignment = WRAP
        ws_c.cell(r, 7, _xls(c.text_vi or "")).alignment = WRAP
        ws_c.cell(r, 8, "").alignment = WRAP
        for col in range(1, 7):
            ws_c.cell(r, col).fill = LOCK_FILL
    _autosize(ws_c, {1: 14, 2: 10, 3: 10, 4: 12, 5: 10, 6: 55, 7: 55, 8: 20})

    # --- labels ---
    ws_l = wb.create_sheet("labels")
    _write_headers(ws_l, LABEL_COLS)
    row = 2
    for entry in document.figures_registry.values():
        pdf_page = entry.pdf_pages[0] if entry.pdf_pages else ""
        for lb in entry.labels:
            if not lb.text_en.strip():
                continue
            ws_l.cell(row, 1, entry.figure_id)
            ws_l.cell(row, 2, entry.figure_number)
            ws_l.cell(row, 3, pdf_page)
            ws_l.cell(row, 4, _xls(lb.text_en)).alignment = WRAP
            ws_l.cell(row, 5, _xls(lb.text_vi or "")).alignment = WRAP
            ws_l.cell(row, 6, "yes" if getattr(lb, "suspicion", False) else "no")
            ws_l.cell(row, 7, _xls(getattr(lb, "guess_note", None) or ""))
            ws_l.cell(row, 8, "")
            for col in range(1, 5):
                ws_l.cell(row, col).fill = LOCK_FILL
            row += 1
    _autosize(ws_l, {1: 16, 2: 12, 3: 10, 4: 40, 5: 40, 6: 12, 7: 30, 8: 20})

    # --- captions ---
    ws_cap = wb.create_sheet("captions")
    _write_headers(ws_cap, CAPTION_COLS)
    row = 2
    for entry in document.figures_registry.values():
        pages = ", ".join(str(p) for p in entry.pdf_pages)
        ws_cap.cell(row, 1, entry.figure_id)
        ws_cap.cell(row, 2, entry.figure_number)
        ws_cap.cell(row, 3, pages)
        ws_cap.cell(row, 4, _xls(entry.caption_en)).alignment = WRAP
        ws_cap.cell(row, 5, _xls(entry.caption_vi or "")).alignment = WRAP
        ws_cap.cell(row, 6, "")
        for col in range(1, 5):
            ws_cap.cell(row, col).fill = LOCK_FILL
        row += 1
    _autosize(ws_cap, {1: 16, 2: 12, 3: 14, 4: 50, 5: 50, 6: 20})

    # --- glossary ---
    ws_g = wb.create_sheet("glossary")
    _write_headers(ws_g, GLOSSARY_COLS)
    for r, e in enumerate(sorted(glossary.entries, key=lambda x: -x.frequency), 2):
        ws_g.cell(r, 1, e.id)
        ws_g.cell(r, 2, e.term_en)
        ws_g.cell(r, 3, e.term_vi or "").alignment = WRAP
        ws_g.cell(r, 4, e.status)
        ws_g.cell(r, 5, e.frequency)
        ws_g.cell(r, 6, str(e.do_not_translate))
        ws_g.cell(r, 7, e.notes)
        for col in (1, 2, 5, 6):
            ws_g.cell(r, col).fill = LOCK_FILL
    _autosize(ws_g, {1: 12, 2: 28, 3: 28, 4: 14, 5: 10, 6: 12, 7: 24})

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))
    return output_path


def _cell_str(val: Any) -> str:
    if val is None:
        return ""
    return str(val).strip()


def _xls(val: Any) -> str:
    return clean_for_xml(_cell_str(val))
    if val is None:
        return ""
    return str(val).strip()


def _read_sheet_rows(ws: Worksheet, skip_header: int = 1) -> List[List[Any]]:
    rows = []
    for row in ws.iter_rows(min_row=skip_header + 1, values_only=True):
        if not any(v is not None and str(v).strip() for v in row):
            continue
        rows.append(list(row))
    return rows


def import_workbook(workspace: Path, excel_path: Path) -> Dict[str, int]:
    if not excel_path.exists():
        raise FileNotFoundError(f"Không tìm thấy Excel: {excel_path}")

    wb = load_workbook(excel_path, read_only=True, data_only=True)
    stats = {"chunks": 0, "labels": 0, "captions": 0, "glossary": 0}

    # --- chunks ---
    if "chunks" in wb.sheetnames:
        by_id = {c.chunk_id: c for c in load_chunks(workspace)}
        for row in _read_sheet_rows(wb["chunks"]):
            cid = _cell_str(row[0])
            if not cid or cid not in by_id:
                continue
            vi = _cell_str(row[6]) if len(row) > 6 else ""
            if vi:
                c = by_id[cid]
                c.text_vi = vi
                c.status = BlockStatus.DONE
                save_chunk(workspace, c)
                stats["chunks"] += 1

    document = load_document(workspace)

    # --- labels: key = figure_id + label_en ---
    if "labels" in wb.sheetnames:
        label_map: Dict[Tuple[str, str], str] = {}
        for row in _read_sheet_rows(wb["labels"]):
            fid = _cell_str(row[0])
            en = _cell_str(row[3]) if len(row) > 3 else ""
            vi = _cell_str(row[4]) if len(row) > 4 else ""
            if fid and en and vi:
                label_map[(fid, en)] = vi

        for entry in document.figures_registry.values():
            for lb in entry.labels:
                key = (entry.figure_id, lb.text_en)
                if key in label_map:
                    lb.text_vi = label_map[key]
                    stats["labels"] += 1

        label_suspicion: Dict[Tuple[str, str], Tuple[bool, str]] = {}
        for row in _read_sheet_rows(wb["labels"]):
            fid = _cell_str(row[0])
            en = _cell_str(row[3]) if len(row) > 3 else ""
            susp = _cell_str(row[5]).lower() if len(row) > 5 else ""
            note = _cell_str(row[6]) if len(row) > 6 else ""
            if fid and en:
                label_suspicion[(fid, en)] = (susp in ("yes", "y", "1", "true"), note)

        for entry in document.figures_registry.values():
            for lb in entry.labels:
                key = (entry.figure_id, lb.text_en)
                if key in label_suspicion:
                    s, n = label_suspicion[key]
                    lb.suspicion = s
                    if n:
                        lb.guess_note = n

        for page in document.pages:
            for block in page.blocks:
                for lb in block.labels or []:
                    if block.figure_id:
                        key = (block.figure_id, lb.text_en)
                    else:
                        key = ("", lb.text_en)
                    if key in label_map:
                        lb.text_vi = label_map[key]

    # --- captions ---
    if "captions" in wb.sheetnames:
        cap_map: Dict[str, str] = {}
        for row in _read_sheet_rows(wb["captions"]):
            fid = _cell_str(row[0])
            vi = _cell_str(row[4]) if len(row) > 4 else ""
            if fid and vi:
                cap_map[fid] = vi

        for fid, vi in cap_map.items():
            if fid in document.figures_registry:
                document.figures_registry[fid].caption_vi = vi
                stats["captions"] += 1
            # sync chunk captions
            fig_num = document.figures_registry[fid].figure_number if fid in document.figures_registry else ""
            for c in load_chunks(workspace):
                if c.block_type == "caption" and fig_num and fig_num in (c.text_en or ""):
                    c.text_vi = vi
                    c.status = BlockStatus.DONE
                    save_chunk(workspace, c)

    # --- glossary ---
    if "glossary" in wb.sheetnames:
        glossary = load_glossary(workspace)
        by_id = {e.id: e for e in glossary.entries}
        for row in _read_sheet_rows(wb["glossary"]):
            eid = _cell_str(row[0])
            if not eid or eid not in by_id:
                continue
            vi = _cell_str(row[2]) if len(row) > 2 else ""
            status = _cell_str(row[3]) if len(row) > 3 else ""
            notes = _cell_str(row[6]) if len(row) > 6 else ""
            e = by_id[eid]
            if vi:
                e.term_vi = vi
                stats["glossary"] += 1
            if status in ("draft", "approved", "protected"):
                e.status = status
            if notes:
                e.notes = notes
        save_glossary(glossary, workspace)

    save_document(document, workspace)
    wb.close()
    return stats
