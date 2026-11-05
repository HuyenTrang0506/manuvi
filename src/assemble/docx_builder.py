"""
Lắp ráp DOCX theo cấu trúc text gốc:
- Đề mục: song ngữ EN | VI (giữ xuống dòng)
- Đoạn văn: chỉ VI, giữ \\n / \\n\\n như bản gốc
- Hình: ảnh gốc + chú thích nhãn
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

from docx import Document as DocxDocument
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from src.assemble.label_table import (
    build_harness_callout_section,
    build_structured_legend,
    format_vi_cell,
    format_vi_meaning,
    partition_labels,
    sort_labels,
    split_label,
)
from src.assemble.toc import (
    TOC_COL_HEADERS,
    TOC_TITLE,
    TOC_TITLE_VI,
    TocEntry,
    collect_toc_entries,
    toc_row_cells,
    translate_heading_text,
)
from src.assemble.wire_appendix import (
    APPENDIX_NOTES_VI,
    APPENDIX_TITLE,
    FIGURE_WIRE_NOTE,
    appendix_intro,
    color_table_rows,
)
from src.translate.equipment_list import EQUIPMENT_LIST_INTRO_VI, is_equipment_index_code

from src.ingest.parser import load_document
from src.ingest.text_structure import apply_structure_from_en, clean_for_xml
from src.models import BlockType
from src.translate.chunker import load_chunks
from src.translate.flowchart import translate_flowchart_text
from src.models import Glossary


_EMPTY_GLOSSARY = Glossary(terms={})


def _resolve_block_vi(block, block_vi: Dict[str, str], manual_ref: str = "") -> str:
    """VI từ chunk; Section 43 thiếu chunk → dịch flowchart nguyên khối."""
    vi = block_vi.get(block.block_id, "")
    if vi:
        return vi
    en = (block.text_en or "").strip()
    if not en:
        return ""
    ref = manual_ref or getattr(block, "manual_ref", "") or ""
    if str(ref).startswith("43"):
        return translate_flowchart_text(en, _EMPTY_GLOSSARY) or en
    return en


def _set_fixed_table(table, col_widths_in: List[float]) -> None:
    """Khóa layout bảng — cột không co giãn theo nội dung."""
    table.autofit = False
    table.alignment = WD_TABLE_ALIGNMENT.LEFT
    tbl = table._tbl
    tblPr = tbl.tblPr
    layout = OxmlElement("w:tblLayout")
    layout.set(qn("w:type"), "fixed")
    tblPr.append(layout)
    tblW = OxmlElement("w:tblW")
    tblW.set(qn("w:type"), "pct")
    tblW.set(qn("w:w"), "5000")
    tblPr.append(tblW)
    for i, w in enumerate(col_widths_in):
        if i < len(table.columns):
            table.columns[i].width = Inches(w)
    for row in table.rows:
        for i, cell in enumerate(row.cells):
            if i < len(col_widths_in):
                cell.width = Inches(col_widths_in[i])
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            for p in cell.paragraphs:
                p.paragraph_format.space_after = Pt(0)
                p.paragraph_format.space_before = Pt(0)

SKIP_VI = "[GIỮ NGUYÊN — SƠ ĐỒ MẠCH"
HEADING_TYPES = {BlockType.HEADING, BlockType.PAGE_REF}
PROSE_TYPES = {BlockType.PARAGRAPH, BlockType.LIST, BlockType.WARNING, BlockType.TABLE}


def _build_block_vi_map(chunks) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for c in chunks:
        if not c.text_vi:
            continue
        for bid in c.source_block_ids:
            m[bid] = c.text_vi
    return m


def _build_block_en_map(chunks) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for c in chunks:
        for bid in c.source_block_ids:
            m[bid] = c.text_en
    return m


def _translate_heading(en: str, vi_map: Dict[str, str], block_id: str) -> str:
    if block_id in vi_map and vi_map[block_id]:
        return vi_map[block_id]
    vi = translate_heading_text(en)
    if vi:
        return vi
    low = en.lower()
    rules = {
        "electrical system": "HỆ THỐNG ĐIỆN",
        "troubleshooting (electric)": "CHẨN ĐOÁN SỰ CỐ (ĐIỆN)",
        "electrical circuit diagram": "SƠ ĐỒ MẠCH ĐIỆN",
        "equipment list": "DANH MỤC THIẾT BỊ",
        "table of contents": "MỤC LỤC",
        "notes": "GHI CHÚ",
    }
    for k, v in rules.items():
        if k in low:
            return re.sub(re.escape(k), v, en, flags=re.I)
    m = re.match(r"^(\d+(?:\.\d+)*)\s*(.*)", en.strip(), re.I | re.S)
    if m:
        num, rest = m.group(1), m.group(2).strip()
        for k, v in rules.items():
            if k in rest.lower():
                return f"{num} {v}"
    return ""


def _add_bilingual_toc(
    doc,
    document,
    block_vi: Dict[str, str],
    pdf_page_map: Optional[Dict[str, int]] = None,
    prebuilt_entries: Optional[List[TocEntry]] = None,
) -> None:
    """TOC song ngữ EN | VI; cột Trang sổ tay = trang PDF VI.

    Nếu prebuilt_entries được cung cấp (từ toc_entries.json của lần build PDF),
    dùng trực tiếp — số trang chính xác nhất. Fallback: tính lại từ document.
    """
    if prebuilt_entries is not None:
        entries = prebuilt_entries
    else:
        entries = collect_toc_entries(document, block_vi)
        if pdf_page_map:
            for e in entries:
                if e.manual_ref and e.manual_ref in pdf_page_map:
                    e.output_page = pdf_page_map[e.manual_ref]
                elif "appendix" in e.en.lower() and "appendix" in pdf_page_map:
                    e.output_page = pdf_page_map["appendix"]

    doc.add_heading(TOC_TITLE, level=1)
    note = doc.add_paragraph(
        f"{TOC_TITLE_VI}. Cột Trang sổ tay = số trang đóng góc phải PDF bản Việt; "
        "Mục gốc = tham chiếu 23-xx / 43-xx trong manual nguồn."
    )
    for r in note.runs:
        r.font.size = Pt(9)
        r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    tbl = doc.add_table(rows=1, cols=4)
    tbl.style = "Table Grid"
    _set_fixed_table(tbl, [2.5, 2.5, 0.9, 0.7])
    for i, h in enumerate(TOC_COL_HEADERS):
        tbl.rows[0].cells[i].text = h
        for p in tbl.rows[0].cells[i].paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(8)
    for entry in entries:
        cells = toc_row_cells(entry)
        row = tbl.add_row().cells
        for i, t in enumerate(cells):
            row[i].text = t
            for p in row[i].paragraphs:
                for r in p.runs:
                    r.font.size = Pt(8)
    doc.add_page_break()


def _write_lines_in_paragraph(paragraph, lines: List[str], font_size: int = 11, bold: bool = False):
    """Ghi nhiều dòng trong 1 paragraph — xuống dòng giống PDF."""
    for i, line in enumerate(lines):
        if i > 0:
            paragraph.add_run().add_break(WD_BREAK.LINE)
        run = paragraph.add_run(clean_for_xml(line))
        run.font.size = Pt(font_size)
        run.bold = bold


def _add_structured_text(doc: DocxDocument, text: str, font_size: int = 11, bold: bool = False, btype_str: str = "paragraph"):
    """Giữ paragraph break và line break; format list đánh số và cảnh báo."""
    text = clean_for_xml(text)
    if not text or text.startswith(SKIP_VI):
        return
    is_warning = btype_str == "warning"
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        lines = [ln for ln in para.split("\n")]
        if not any(ln.strip() for ln in lines):
            continue

        # Cảnh báo — màu đỏ đậm
        if is_warning:
            p = doc.add_paragraph()
            for i, line in enumerate(lines):
                if not line.strip():
                    continue
                if i > 0:
                    p.add_run().add_break(WD_BREAK.LINE)
                run = p.add_run(clean_for_xml(line.strip()))
                run.font.size = Pt(font_size)
                run.bold = True
                run.font.color.rgb = RGBColor(0xA5, 0x27, 0x00)
            p.paragraph_format.space_after = Pt(4)
            continue

        # Kiểm tra xem paragraph này có phải list không (tất cả dòng bắt đầu bằng số/ký hiệu)
        stripped_lines = [ln.strip() for ln in lines if ln.strip()]
        list_matches = [re.match(r"^(\d+[.\)]|\([a-z]\)|[a-z][.\)]|[-•▪])\s+(.*)", ln) for ln in stripped_lines]
        is_list_block = stripped_lines and all(m is not None for m in list_matches)

        if is_list_block:
            for m in list_matches:
                if not m:
                    continue
                p = doc.add_paragraph(style="List Number" if m.group(1)[0].isdigit() else "List Bullet")
                run = p.add_run(clean_for_xml(m.group(2).strip()))
                run.font.size = Pt(font_size)
                p.paragraph_format.space_after = Pt(2)
        else:
            # Kiểm tra từng dòng — một số dòng là list item, còn lại là prose
            p = doc.add_paragraph()
            first_in_para = True
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    if not first_in_para:
                        pass  # giữ nguyên xuống dòng
                    continue
                if not first_in_para:
                    p.add_run().add_break(WD_BREAK.LINE)
                m_num = re.match(r"^(\d+[.\)]|\([a-z]\)|[a-z][.\)]|[-•▪])\s+(.*)", stripped)
                if m_num:
                    run_marker = p.add_run(m_num.group(1) + "  ")
                    run_marker.font.size = Pt(font_size)
                    run_content = p.add_run(clean_for_xml(m_num.group(2).strip()))
                    run_content.font.size = Pt(font_size)
                    p.paragraph_format.left_indent = Pt(8)
                else:
                    run = p.add_run(clean_for_xml(stripped))
                    run.font.size = Pt(font_size)
                    run.bold = bold
                first_in_para = False
            p.paragraph_format.space_after = Pt(4)


def _add_bilingual_heading(doc: DocxDocument, en: str, vi: str, manual_ref: Optional[str]):
    table = doc.add_table(rows=1, cols=2)
    table.alignment = WD_TABLE_ALIGNMENT.CENTER
    table.style = "Table Grid"
    en_lines = en.strip().split("\n")
    vi_lines = (vi or "—").strip().split("\n")
    for col, lines, bold in [(0, en_lines, False), (1, vi_lines, True)]:
        cell = table.rows[0].cells[col]
        p = cell.paragraphs[0]
        _write_lines_in_paragraph(p, lines, font_size=11 if col == 0 else 12, bold=bold)
        if col == 0:
            for run in p.runs:
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
    if manual_ref:
        ref = doc.add_paragraph()
        r = ref.add_run(f"Trang sổ tay: {manual_ref}")
        r.font.size = Pt(8)
        r.font.color.rgb = RGBColor(0x99, 0x99, 0x99)


def _add_docx_page_numbers(doc: DocxDocument) -> None:
    """Thêm số trang vào footer (căn phải) cho mọi section trong DOCX."""
    for section in doc.sections:
        section.different_first_page_header_footer = False
        footer = section.footer
        if footer.paragraphs:
            para = footer.paragraphs[0]
        else:
            para = footer.add_paragraph()
        para.clear()
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        run = para.add_run()
        run.font.size = Pt(8)
        run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)
        # Trường PAGE (số trang tự động)
        fldChar1 = OxmlElement("w:fldChar")
        fldChar1.set(qn("w:fldCharType"), "begin")
        run._r.append(fldChar1)
        instrText = OxmlElement("w:instrText")
        instrText.set(qn("xml:space"), "preserve")
        instrText.text = " PAGE "
        run._r.append(instrText)
        fldChar_sep = OxmlElement("w:fldChar")
        fldChar_sep.set(qn("w:fldCharType"), "separate")
        run._r.append(fldChar_sep)
        # Placeholder visible
        t = OxmlElement("w:t")
        t.text = "1"
        run._r.append(t)
        fldChar2 = OxmlElement("w:fldChar")
        fldChar2.set(qn("w:fldCharType"), "end")
        run._r.append(fldChar2)


def _add_figure_block(
    doc: DocxDocument,
    workspace: Path,
    image_path: Optional[str],
    figure_number: Optional[str],
    caption_en: str,
    caption_vi: str,
    labels: list,
    full_page: bool = False,
    figure_id: str = "",
    source_pdf_doc=None,  # fitz.Document | None
    pdf_page_num: int = 0,
):
    import io

    if source_pdf_doc is not None and pdf_page_num > 0:
        # Render toàn trang nguồn PDF — đầy đủ như bản gốc (ưu tiên cao nhất)
        try:
            import fitz as _fitz
            idx = pdf_page_num - 1
            if 0 <= idx < len(source_pdf_doc):
                src_page = source_pdf_doc[idx]
                mat = _fitz.Matrix(150 / 72.0, 150 / 72.0)
                pix = src_page.get_pixmap(matrix=mat, alpha=False)
                buf = io.BytesIO(pix.tobytes("png"))
                doc.add_picture(buf, width=Inches(6.5))
            else:
                doc.add_paragraph(f"[Trang nguồn không hợp lệ: {pdf_page_num}]")
        except Exception as e:
            doc.add_paragraph(f"[Lỗi render trang nguồn {pdf_page_num}: {e}]")
    elif image_path:
        # Dùng ảnh đã trích (full-page vector hoặc không có source PDF)
        img = workspace / image_path
        if not img.exists():
            img = workspace.parent / image_path
        if img.exists():
            width = Inches(6.5) if full_page else Inches(5.5)
            try:
                doc.add_picture(str(img), width=width)
            except Exception:
                doc.add_paragraph(f"[Không tải được ảnh: {image_path}]")

    if figure_number or caption_vi or caption_en:
        cap = doc.add_paragraph()
        title = caption_vi or caption_en or ""
        # Luôn hiển thị số hình
        if figure_number:
            cap_text = f"Hình {figure_number}. {title}".strip(" .") if title else f"Hình {figure_number}"
        else:
            cap_text = title or "—"
        cap_run = cap.add_run(clean_for_xml(cap_text))
        cap_run.bold = True
        cap_run.font.size = Pt(11)
        if caption_en and caption_vi and caption_en != caption_vi:
            sub = doc.add_paragraph()
            sub_run = sub.add_run(clean_for_xml(f"(Fig. {figure_number} — {caption_en})"))
            sub_run.font.size = Pt(8)
            sub_run.font.color.rgb = RGBColor(0x88, 0x88, 0x88)

    labels = labels or []
    # Vision legend first (best quality), then OCR structured labels
    vision_sections = build_harness_callout_section(
        caption_en=caption_en or "",
        figure_id=figure_id or "",
        workspace=workspace,
    )
    ocr_sections = build_structured_legend(labels) if labels else []
    # Dedup: skip OCR rows whose EN key already appears in vision rows
    if vision_sections and ocr_sections:
        vision_en = {
            r.name_en.strip().upper()
            for sec in vision_sections
            for r in sec.rows
            if r.name_en
        }
        from src.assemble.label_table import LegendSection
        deduped = []
        for sec in ocr_sections:
            kept_rows = [
                r for r in sec.rows
                if not (r.code == "—" and (r.name_en or "").strip().upper() in vision_en)
            ]
            if kept_rows:
                deduped.append(LegendSection(title=sec.title, rows=kept_rows))
        ocr_sections = deduped
    sections = vision_sections + ocr_sections

    if labels or sections:
        # Trang danh mục thiết bị (23.1.2) — nhiều mã C-1, E-14 không mô tả
        equip_n = sum(1 for lb in labels if is_equipment_index_code(lb.text_en.strip()))
        is_equip_page = bool(labels) and equip_n >= 10 and equip_n > len(labels) * 0.3

        components, _wires, _pins = partition_labels(labels) if labels else ([], [], [])

        note = doc.add_paragraph()
        if is_equip_page:
            note_run = note.add_run("Danh mục mã thiết bị điện (23.1.2):")
            note_run.bold = True
            note_run.font.size = Pt(10)
            intro = doc.add_paragraph()
            ir = intro.add_run(EQUIPMENT_LIST_INTRO_VI)
            ir.font.size = Pt(9)
            ir.font.color.rgb = RGBColor(0x44, 0x44, 0x44)
        else:
            note_run = note.add_run("Chú thích tra cứu trên hình (theo mã):")
            note_run.bold = True
            note_run.font.size = Pt(10)
            tip = doc.add_paragraph()
            tip_r = tip.add_run(FIGURE_WIRE_NOTE)
            tip_r.font.size = Pt(8)
            tip_r.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

        def _add_3col_rows(rows_src):
            tbl = doc.add_table(rows=1, cols=3)
            tbl.style = "Table Grid"
            _set_fixed_table(tbl, [1.4, 2.0, 2.35])
            hdr = tbl.rows[0].cells
            for i, title in enumerate(["Mã", "Tên (EN)", "Nghĩa tiếng Việt"]):
                hdr[i].text = title
                for p in hdr[i].paragraphs:
                    for r in p.runs:
                        r.bold = True
                        r.font.size = Pt(9)
            for row_data in rows_src:
                if hasattr(row_data, "code"):
                    code, desc, meaning = row_data.code, row_data.name_en, row_data.meaning_vi
                    sus = row_data.suspicion
                else:
                    code, desc, full_en = split_label(row_data.text_en)
                    meaning = format_vi_meaning(row_data)
                    desc = desc or full_en
                    sus = getattr(row_data, "suspicion", False)
                row = tbl.add_row().cells
                row[0].text = clean_for_xml(code or "—")
                row[1].text = clean_for_xml(desc or "—")
                row[2].text = clean_for_xml(meaning)
                for ci, cell in enumerate(row):
                    for p in cell.paragraphs:
                        p.alignment = WD_ALIGN_PARAGRAPH.LEFT
                        for r in p.runs:
                            r.font.size = Pt(9)
                            if ci == 0:
                                r.bold = True
                            if ci == 2 and sus:
                                r.font.color.rgb = RGBColor(0xBF, 0x5A, 0x00)
            return tbl

        if is_equip_page:
            sorted_lb = components if components else sort_labels(labels)
            _add_3col_rows(sorted_lb)
        elif sections:
            for sec in sections:
                if sec.title and (
                    "Nhãn chữ" in sec.title
                    or "Vision" in sec.title
                    or "Nhãn đọc" in sec.title
                ):
                    h = doc.add_paragraph()
                    hr = h.add_run(sec.title)
                    hr.bold = True
                    hr.font.size = Pt(9)
                _add_3col_rows(sec.rows)
        else:
            sorted_lb = components if components else sort_labels(labels)
            tbl = doc.add_table(rows=1, cols=2)
            tbl.style = "Table Grid"
            _set_fixed_table(tbl, [2.5, 3.25])
            hdr = tbl.rows[0].cells
            hdr[0].text = "Ký hiệu gốc"
            hdr[1].text = "Nghĩa tiếng Việt"
            for lb in sorted_lb:
                if not lb.text_en.strip():
                    continue
                row = tbl.add_row().cells
                row[0].text = clean_for_xml(lb.text_en)
                row[1].text = format_vi_cell(lb)
                for cell in row:
                    for p in cell.paragraphs:
                        for r in p.runs:
                            r.font.size = Pt(9)

        # Mã dây không list dưới từng hình — xem Phụ lục A
    doc.add_paragraph()


def _add_wire_color_appendix(doc) -> None:
    """Phụ lục A — bảng mã màu dây (mục TOC)."""
    doc.add_heading(APPENDIX_TITLE, level=1)
    p = doc.add_paragraph(appendix_intro())
    for r in p.runs:
        r.font.size = Pt(9)
    tbl = doc.add_table(rows=1, cols=3)
    tbl.style = "Table Grid"
    _set_fixed_table(tbl, [0.8, 1.8, 2.5])
    hdr = tbl.rows[0].cells
    for i, t in enumerate(["Mã", "English", "Tiếng Việt"]):
        hdr[i].text = t
        for p in hdr[i].paragraphs:
            for r in p.runs:
                r.bold = True
                r.font.size = Pt(9)
    for code, en, vi in color_table_rows():
        row = tbl.add_row().cells
        row[0].text = code
        row[1].text = en
        row[2].text = vi
        for cell in row:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
    notes_h = doc.add_paragraph()
    nr = notes_h.add_run("Ghi chú (từ sổ tay gốc):")
    nr.bold = True
    nr.font.size = Pt(9)
    for line in APPENDIX_NOTES_VI:
        np = doc.add_paragraph(line)
        for r in np.runs:
            r.font.size = Pt(9)
    doc.add_page_break()


def build_docx(
    workspace: Path,
    output_path: Path,
    title: str,
    source_pdf_path: Optional[Path] = None,
) -> Path:
    document = load_document(workspace)
    chunks = load_chunks(workspace)
    block_vi = _build_block_vi_map(chunks)
    block_en = _build_block_en_map(chunks)
    registry = document.figures_registry

    # Tải toc_entries.json (kết quả chính xác từ lần build PDF) nếu có
    import json

    prebuilt_toc: Optional[List[TocEntry]] = None
    toc_entries_json = workspace / "qa" / "toc_entries.json"
    if toc_entries_json.exists():
        try:
            raw = json.loads(toc_entries_json.read_text(encoding="utf-8"))
            prebuilt_toc = [TocEntry(**row) for row in raw]
        except Exception:
            prebuilt_toc = None

    # Fallback: map trang PDF VI nếu không có toc_entries.json (toc_pages.json)
    pdf_page_map: Optional[Dict[str, int]] = None
    toc_json = workspace / "qa" / "toc_pages.json"
    if prebuilt_toc is None and toc_json.exists():
        try:
            pdf_page_map = json.loads(toc_json.read_text(encoding="utf-8"))
        except Exception:
            pdf_page_map = None

    # Mở source PDF để render ảnh toàn trang (nếu có)
    source_pdf_doc = None
    if source_pdf_path is None:
        candidate = Path("data") / document.source_file
        if candidate.exists():
            source_pdf_path = candidate
    if source_pdf_path and source_pdf_path.exists():
        try:
            import fitz as _fitz
            source_pdf_doc = _fitz.open(str(source_pdf_path))
        except Exception:
            source_pdf_doc = None

    doc = DocxDocument()
    doc.add_heading(title, 0)
    intro = doc.add_paragraph(
        "Đề mục song ngữ Anh–Việt; nội dung văn bản chỉ tiếng Việt; "
        "giữ xuống dòng và số tham chiếu (Fig., mục 23.2.5…) như sổ tay gốc."
    )
    intro.runs[0].font.size = Pt(10)

    _add_bilingual_toc(doc, document, block_vi, pdf_page_map, prebuilt_entries=prebuilt_toc)
    _add_wire_color_appendix(doc)

    rendered_source_pages: set[int] = set()

    for page in document.pages:
        sec = page.manual_ref or f"PDF-{page.pdf_page}"
        hdr = doc.add_paragraph()
        hdr_run = hdr.add_run(f"── {sec} ──")
        hdr_run.bold = True
        hdr_run.font.size = Pt(9)
        hdr_run.font.color.rgb = RGBColor(0x44, 0x44, 0x44)

        rendered_figures: set[str] = set()

        # ── Page 43-7: BILINGUAL TABLE (for-loop, replaces unreadable PNG) ───────
        if (page.manual_ref or "") == "43-7":
            from src.translate.section43_table import load_json_cache, save_json_cache
            save_json_cache(workspace)
            items_43_7 = load_json_cache(workspace)
            # Write headings (bilingual)
            for block in page.blocks:
                if block.type == BlockType.HEADING:
                    en_h = (block.text_en or "").strip()
                    if not en_h:
                        continue
                    from src.translate.section43_map import lookup_section43
                    vi_h = lookup_section43(en_h) or en_h
                    _add_bilingual_heading(doc, en_h, vi_h, manual_ref=None)
            # Intro paragraph
            for block in page.blocks:
                if block.type in (BlockType.PARAGRAPH, BlockType.LIST):
                    en_p = (block.text_en or "").strip()
                    if not en_p or len(en_p) < 30:
                        continue
                    from src.translate.section43_map import lookup_section43
                    vi_p = lookup_section43(en_p)
                    if vi_p and vi_p != en_p:
                        p = doc.add_paragraph(vi_p)
                        p.runs[0].font.size = Pt(10)
                    break
            # Label
            lbl = doc.add_paragraph()
            lr = lbl.add_run("▶ Bảng Chẩn đoán Dịch vụ — SW00 đến SW36 (song ngữ EN | VI)")
            lr.bold = True
            lr.font.size = Pt(9)
            lr.font.color.rgb = RGBColor(0x00, 0x55, 0x20)
            # Build DOCX table: SW | English | Vietnamese | Normal value
            tbl = doc.add_table(rows=1, cols=4)
            tbl.style = "Table Grid"
            # Column widths: 0.5 | 2.5 | 2.5 | 1.0 inches
            col_widths = [0.5, 2.5, 2.7, 1.0]
            _set_fixed_table(tbl, col_widths)
            # Header row
            hdr_cells = tbl.rows[0].cells
            for cell, txt in zip(
                hdr_cells,
                ["SW", "Service Diagnosis Item (EN)", "Hạng mục chẩn đoán (VI)", "Giá trị bt."],
            ):
                cell.text = txt
                run = cell.paragraphs[0].runs[0]
                run.bold = True
                run.font.size = Pt(8)
                cell.paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.CENTER
                # Blue header background
                from docx.oxml.ns import qn as _qn
                from docx.oxml import OxmlElement as _OE
                shd = _OE("w:shd")
                shd.set(_qn("w:val"), "clear")
                shd.set(_qn("w:color"), "auto")
                shd.set(_qn("w:fill"), "2E609A")
                cell._tc.get_or_add_tcPr().append(shd)
                run.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
            # Data rows — for-loop over items
            for idx, item in enumerate(items_43_7):
                row_cells = tbl.add_row().cells
                for cell, txt in zip(
                    row_cells,
                    [item["sw"], item["en_item"], item["vi_item"], item.get("normal", "—")],
                ):
                    cell.text = txt
                    run = cell.paragraphs[0].runs[0]
                    run.font.size = Pt(8)
                    cell.paragraphs[0].paragraph_format.space_after = Pt(1)
                    if idx % 2 == 0:
                        from docx.oxml.ns import qn as _qn
                        from docx.oxml import OxmlElement as _OE
                        shd = _OE("w:shd")
                        shd.set(_qn("w:val"), "clear")
                        shd.set(_qn("w:color"), "auto")
                        shd.set(_qn("w:fill"), "EDF2FA")
                        cell._tc.get_or_add_tcPr().append(shd)
            continue  # skip normal block rendering for this page

        # --- Ảnh bối cảnh trang nguồn cho trang bảng / lưu đồ Sec 43 không có figure block ---
        fig_blocks_on_page = [b for b in page.blocks if b.type == BlockType.FIGURE]
        is_sec43 = (page.manual_ref or "").startswith("43")
        if not fig_blocks_on_page and is_sec43:
            prose_on_page = [
                b for b in page.blocks
                if b.type in PROSE_TYPES and (b.text_en or "").strip()
            ]
            avg_len = (
                sum(len(b.text_en) for b in prose_on_page) / len(prose_on_page)
                if prose_on_page else 999
            )
            is_table_like = len(prose_on_page) >= 3 and avg_len < 120
            if is_table_like and page.pdf_page not in rendered_source_pages:
                rendered_source_pages.add(page.pdf_page)

                # ── Ưu tiên: ảnh lưu đồ VI đã vẽ lại ─────────────────────────────
                vi_fc_dir = workspace / "assets" / "flowcharts_vi"
                vi_png = vi_fc_dir / f"{page.manual_ref}.png"
                fc_json_path = vi_fc_dir / f"{page.manual_ref}.json"

                # For cramped flowcharts (>= 10 nodes), render bilingual decision table
                import json as _json
                fc_nodes_docx: list = []
                fc_title_vi_docx = sec
                _use_table_docx = False
                if fc_json_path.exists():
                    with open(fc_json_path, encoding="utf-8") as _fh:
                        _fc_data = _json.load(_fh)
                    fc_nodes_docx = _fc_data.get("nodes", [])
                    fc_title_vi_docx = _fc_data.get("title_vi") or _fc_data.get("title_en") or sec
                    _use_table_docx = len(fc_nodes_docx) >= 10

                if _use_table_docx and fc_nodes_docx:
                    # Write bilingual decision table
                    lbl_tbl = doc.add_paragraph()
                    lr_tbl = lbl_tbl.add_run(
                        f"▶ Lưu đồ chẩn đoán — {sec} (bảng song ngữ EN | VI)"
                    )
                    lr_tbl.bold = True
                    lr_tbl.font.size = Pt(9)
                    lr_tbl.font.color.rgb = RGBColor(0x00, 0x55, 0x20)
                    title_p = doc.add_paragraph(fc_title_vi_docx)
                    title_p.runs[0].bold = True
                    title_p.runs[0].font.size = Pt(9)
                    # Decision table: # | EN | VI
                    fc_tbl = doc.add_table(rows=1, cols=3)
                    fc_tbl.style = "Table Grid"
                    _set_fixed_table(fc_tbl, [0.4, 3.1, 3.2])
                    # Header
                    for hc, ht in zip(fc_tbl.rows[0].cells, ["#", "Bước (EN)", "Bước (VI)"]):
                        hc.text = ht
                        hr = hc.paragraphs[0].runs[0]
                        hr.bold = True
                        hr.font.size = Pt(8)
                        hr.font.color.rgb = RGBColor(0xFF, 0xFF, 0xFF)
                        _shd = OxmlElement("w:shd")
                        _shd.set(qn("w:val"), "clear")
                        _shd.set(qn("w:color"), "auto")
                        _shd.set(qn("w:fill"), "2E609A")
                        hc._tc.get_or_add_tcPr().append(_shd)
                    TYPE_MARKS = {"check": "?", "action": "→", "terminal": "✓", "decision": "?"}
                    _FILLS = {"check": "FFFEE0", "decision": "FFFEE0", "action": "EDF2FA", "terminal": "E8F5E9"}
                    for _nidx, _node in enumerate(fc_nodes_docx):
                        _ntype = _node.get("type", "action")
                        _branch = _node.get("branch_label", "")
                        _en = _node.get("en", "").strip()
                        _vi = _node.get("vi", "").strip()
                        if _branch:
                            _en = f"[{_branch}] {_en}"
                            _vi = f"[{_branch}] {_vi}"
                        _row = fc_tbl.add_row().cells
                        for _ci, (_ct, _val) in enumerate(zip(
                            _row, [TYPE_MARKS.get(_ntype, "•"), _en, _vi]
                        )):
                            _ct.text = _val
                            _cr = _ct.paragraphs[0].runs[0]
                            _cr.font.size = Pt(8)
                            _ct.paragraphs[0].paragraph_format.space_after = Pt(1)
                            _fill = _FILLS.get(_ntype, "FFFFFF")
                            _shd2 = OxmlElement("w:shd")
                            _shd2.set(qn("w:val"), "clear")
                            _shd2.set(qn("w:color"), "auto")
                            _shd2.set(qn("w:fill"), _fill)
                            _ct._tc.get_or_add_tcPr().append(_shd2)

                elif vi_png.exists():
                    lbl_vi = doc.add_paragraph()
                    lr_vi = lbl_vi.add_run(f"▶ Lưu đồ tiếng Việt — {sec}")
                    lr_vi.bold = True
                    lr_vi.font.size = Pt(9)
                    lr_vi.font.color.rgb = RGBColor(0x00, 0x66, 0x33)
                    try:
                        doc.add_picture(str(vi_png), width=Inches(6.5))
                    except Exception:
                        doc.add_paragraph(f"[Không tải được ảnh VI: {vi_png.name}]")

                    # Ảnh gốc EN nhỏ kèm theo (tham khảo)
                    if source_pdf_doc is not None:
                        import io, fitz as _fitz
                        try:
                            idx = page.pdf_page - 1
                            if 0 <= idx < len(source_pdf_doc):
                                src_p = source_pdf_doc[idx]
                                mat = _fitz.Matrix(80 / 72.0, 80 / 72.0)
                                pix = src_p.get_pixmap(matrix=mat, alpha=False)
                                buf = io.BytesIO(pix.tobytes("png"))
                                lbl_en = doc.add_paragraph()
                                lr_en = lbl_en.add_run(f"(Tài liệu gốc EN — {sec})")
                                lr_en.font.size = Pt(7)
                                lr_en.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
                                doc.add_picture(buf, width=Inches(4.5))
                        except Exception:
                            pass

                # ── Fallback: chỉ có ảnh gốc EN khi chưa vẽ VI ───────────────────
                elif source_pdf_doc is not None:
                    import io, fitz as _fitz
                    try:
                        idx = page.pdf_page - 1
                        if 0 <= idx < len(source_pdf_doc):
                            src_p = source_pdf_doc[idx]
                            mat = _fitz.Matrix(120 / 72.0, 120 / 72.0)
                            pix = src_p.get_pixmap(matrix=mat, alpha=False)
                            buf = io.BytesIO(pix.tobytes("png"))
                            lbl = doc.add_paragraph()
                            lr = lbl.add_run(f"▶ Trang gốc (EN) — {sec}  [chưa có bản vẽ VI]")
                            lr.bold = True
                            lr.font.size = Pt(8)
                            lr.font.color.rgb = RGBColor(0xAA, 0x44, 0x00)
                            doc.add_picture(buf, width=Inches(6.5))
                            lbl2 = doc.add_paragraph()
                            lr2 = lbl2.add_run("▼ Bản dịch tiếng Việt:")
                            lr2.bold = True
                            lr2.font.size = Pt(8)
                            lr2.font.color.rgb = RGBColor(0x00, 0x66, 0x33)
                    except Exception:
                        pass

        for block in page.blocks:
            btype = block.type
            if btype == BlockType.FOOTER:
                continue

            if btype in HEADING_TYPES or (
                btype == BlockType.LIST
                and re.match(r"^\d{2}\.", block.text_en.strip().split("\n")[0])
            ):
                if btype == BlockType.PAGE_REF:
                    continue
                en = block.text_en
                vi = block_vi.get(block.block_id) or _translate_heading(
                    en, block_vi, block.block_id
                )
                if (not vi or vi == en) and (page.manual_ref or "").startswith("43"):
                    vi = _resolve_block_vi(block, block_vi, page.manual_ref or "")
                _add_bilingual_heading(doc, en, vi, manual_ref=None)
                continue

            if btype == BlockType.FIGURE:
                if block.block_id in rendered_figures:
                    continue
                rendered_figures.add(block.block_id)
                fig_num = cap_en = cap_vi = None
                if block.figure_id and block.figure_id in registry:
                    entry = registry[block.figure_id]
                    fig_num = entry.figure_number
                    cap_en = entry.caption_en
                    cap_vi = entry.caption_vi or ""
                # Fallback figure number từ figure_id
                if not fig_num and block.figure_id:
                    m_fid = re.match(r"fig-(.+)", block.figure_id)
                    if m_fid:
                        fig_num = m_fid.group(1)
                labels = block.labels or (
                    registry[block.figure_id].labels
                    if block.figure_id and block.figure_id in registry
                    else []
                )
                use_source = (
                    not block.full_page
                    and source_pdf_doc is not None
                    and page.pdf_page not in rendered_source_pages
                )
                if use_source:
                    rendered_source_pages.add(page.pdf_page)
                _add_figure_block(
                    doc,
                    workspace,
                    block.image_path if (block.full_page or source_pdf_doc is None) else None,
                    fig_num,
                    cap_en or "",
                    cap_vi or "",
                    labels,
                    full_page=block.full_page or use_source,
                    figure_id=block.figure_id or "",
                    source_pdf_doc=None if (block.full_page or not use_source) else source_pdf_doc,
                    pdf_page_num=page.pdf_page if use_source else 0,
                )
                continue

            if btype == BlockType.CAPTION:
                continue

            if btype in PROSE_TYPES:
                en = block.text_en
                vi = _resolve_block_vi(block, block_vi, page.manual_ref or "")
                vi = apply_structure_from_en(en, vi)
                _add_structured_text(doc, vi, btype_str=btype.value)
                continue

        doc.add_paragraph()

    if source_pdf_doc is not None:
        source_pdf_doc.close()

    _add_docx_page_numbers(doc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(output_path))
    return output_path
