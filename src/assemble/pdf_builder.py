"""
Xuất PDF từ document model bằng PyMuPDF — font Unicode cho tiếng Việt.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional

import fitz

from src.assemble.pdf_fonts import UnicodePdfFonts
from src.ingest.parser import load_document
from src.ingest.text_structure import apply_structure_from_en, clean_for_xml
from src.models import BlockType, Glossary
from src.translate.chunker import load_chunks
from src.translate.flowchart import translate_flowchart_text

_EMPTY_GLOSSARY = Glossary(terms={})


def _resolve_block_vi(block, block_vi: Dict[str, str], manual_ref: str = "") -> str:
    en = (block.text_en or "").strip()
    if not en:
        return ""
    # Section 43: prefer section43_map lookup → translate_flowchart_text → chunk → EN fallback
    if str(manual_ref).startswith("43"):
        from src.translate.section43_map import lookup_section43
        map_vi = lookup_section43(en)
        if map_vi:
            return map_vi
        fc_vi = translate_flowchart_text(en, _EMPTY_GLOSSARY)
        if fc_vi:
            return fc_vi
        vi = block_vi.get(block.block_id, "")
        return vi or en
    vi = block_vi.get(block.block_id, "")
    if vi:
        return vi
    return en

MARGIN = 50
PAGE_W, PAGE_H = fitz.paper_size("a4")
FS_BODY = 10
FS_HEAD = 11
FS_SMALL = 8
FS_TITLE = 14
LINE_H = 14
SKIP_VI = "[GIỮ NGUYÊN — SƠ ĐỒ MẠCH"
HEADING_TYPES = {BlockType.HEADING, BlockType.PAGE_REF}
PROSE_TYPES = {BlockType.PARAGRAPH, BlockType.LIST, BlockType.WARNING, BlockType.TABLE}
SUSPICION_COLOR = (0.75, 0.35, 0.05)

# ── Section 43 page mode classification ──────────────────────────────────────
# Locked format (Phase 1 audit):
#   MATRIX    43-3..5  → Show source PDF image (complex cross-ref matrix, unre-renderable)
#   ERR_TABLE 43-6     → VI heading + VI intro para + source PDF image (error-code table + figure)
#   SVC_DIAG  43-7     → VI heading + VI intro para + VI flowchart PNG (long table, PNG is best)
#   FLOWCHART 43-8..22 → VI flowchart PNG only; NO paragraph dump below
SEC43_MATRIX_REFS: set[str] = {"43-3", "43-4", "43-5"}
SEC43_ERR_TABLE_REF = "43-6"
SEC43_SVC_DIAG_REF = "43-7"
SEC43_FLOWCHART_REFS: set[str] = {f"43-{i}" for i in range(8, 23)}


class _PdfWriter:
    def __init__(self, title: str):
        self.doc = fitz.open()
        self.fonts: Optional[UnicodePdfFonts] = None
        self.page: Optional[fitz.Page] = None
        self.y = MARGIN
        self.title = title

    def _new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        if self.fonts is None:
            self.fonts = UnicodePdfFonts(self.doc)
        else:
            self.fonts.ensure_page(self.page)
        self.y = MARGIN

    def _ensure_space(self, needed: float):
        if self.page is None:
            self._new_page()
        if self.y + needed > PAGE_H - MARGIN:
            self._new_page()

    def _write_line(
        self,
        text: str,
        size: int = FS_BODY,
        bold: bool = False,
        color=(0, 0, 0),
    ):
        text = clean_for_xml(text)
        if not text:
            return
        # Bọc dòng dài — textbox cao 1 dòng sẽ cắt mất chữ
        max_chars = max(40, int((PAGE_W - 2 * MARGIN) / (size * 0.55)))
        if len(text) > max_chars and " " in text:
            words = text.split(" ")
            buf = ""
            for w in words:
                trial = f"{buf} {w}".strip()
                if buf and len(trial) > max_chars:
                    self._write_line(buf, size=size, bold=bold, color=color)
                    buf = w
                else:
                    buf = trial
            if buf:
                self._write_line(buf, size=size, bold=bold, color=color)
            return
        self._ensure_space(LINE_H)
        fn = self.fonts.fontname if self.fonts else "helv"
        rect = fitz.Rect(MARGIN, self.y - size * 0.8, PAGE_W - MARGIN, self.y + LINE_H)
        self.page.insert_textbox(
            rect,
            text,
            fontname=fn,
            fontsize=size,
            color=color,
            align=fitz.TEXT_ALIGN_LEFT,
        )
        self.y += LINE_H

    def _write_columns(
        self,
        cells: List[tuple[str, float, float]],
        size: int = FS_BODY,
        color=(0, 0, 0),
        bold: bool = False,
    ):
        """Ghi từng cột tại tọa độ X cố định — font tỷ lệ không lệch cột."""
        if not any(c[0] for c in cells):
            return
        self._ensure_space(LINE_H)
        fn = self.fonts.fontname if self.fonts else "helv"
        top = self.y - size * 0.85
        bottom = self.y + LINE_H
        for text, x0, x1 in cells:
            t = clean_for_xml(text)
            if not t:
                continue
            rect = fitz.Rect(x0, top, x1, bottom)
            self.page.insert_textbox(
                rect,
                t,
                fontname=fn,
                fontsize=size,
                color=color,
                align=fitz.TEXT_ALIGN_LEFT,
            )
        self.y += LINE_H

    def _write_block(self, text: str, size: int = FS_BODY, bold: bool = False):
        text = clean_for_xml(text)
        if not text or text.startswith(SKIP_VI):
            return
        for para in re.split(r"\n\s*\n", text):
            for line in para.split("\n"):
                if line.strip():
                    self._write_line(line, size=size, bold=bold)
            self.y += 4

    # ── Bilingual table renderer ──────────────────────────────────────────────

    def _cell_line_count(self, text: str, col_w: float, size: int) -> int:
        """Estimate how many PDF lines a cell text will occupy."""
        if not text:
            return 1
        chars_per_line = max(8, int(col_w / (size * 0.52)))
        lines = 0
        for physical_line in text.split("\n"):
            if not physical_line:
                lines += 1
                continue
            lines += max(1, -(-len(physical_line) // chars_per_line))  # ceil div
        return max(1, lines)

    def _write_table_row(
        self,
        cells: List[tuple[str, float, float]],  # (text, x0, x1)
        size: int = FS_BODY,
        bold: bool = False,
        header: bool = False,
        shaded: bool = False,
    ) -> None:
        """Write one table row, auto-sizing height for wrapped text."""
        if not any(c[0] for c in cells):
            return
        fn = self.fonts.fontname if self.fonts else "helv"
        row_h_lines = max(
            self._cell_line_count(t, x1 - x0, size) for t, x0, x1 in cells
        )
        row_h = row_h_lines * (size + 3) + 6  # 3pt inter-line, 3pt top+bot padding
        self._ensure_space(row_h)

        if shaded or header:
            bg_color = (0.22, 0.38, 0.60) if header else (0.93, 0.95, 0.98)
            r = fitz.Rect(MARGIN - 2, self.y - 2, PAGE_W - MARGIN + 2, self.y + row_h - 2)
            self.page.draw_rect(r, color=None, fill=bg_color)

        txt_color = (1.0, 1.0, 1.0) if header else (0.0, 0.0, 0.0)
        for text, x0, x1 in cells:
            t = clean_for_xml(text)
            if not t:
                continue
            rect = fitz.Rect(x0 + 2, self.y, x1 - 1, self.y + row_h - 2)
            self.page.insert_textbox(
                rect,
                t,
                fontname=fn,
                fontsize=size,
                color=txt_color,
                align=fitz.TEXT_ALIGN_LEFT,
            )
        self.y += row_h
        # Thin separator line
        if not header:
            self.page.draw_line(
                fitz.Point(MARGIN, self.y - 1),
                fitz.Point(PAGE_W - MARGIN, self.y - 1),
                color=(0.8, 0.8, 0.8),
                width=0.4,
            )

    def _write_flowchart_decision_table(
        self, nodes: List[dict], title_vi: str, ref: str
    ) -> None:
        """Render a flowchart as a readable bilingual step-by-step decision table."""
        FS_TBL = 8
        # Column widths: Step | EN | VI
        x_step0, x_step1 = MARGIN, MARGIN + 28
        x_en0,   x_en1   = MARGIN + 30, MARGIN + 252
        x_vi0,   x_vi1   = MARGIN + 254, PAGE_W - MARGIN

        def _cells(step: str, en: str, vi: str):
            return [(step, x_step0, x_step1), (en, x_en0, x_en1), (vi, x_vi0, x_vi1)]

        # Page header
        self._write_line(
            f"Trang {ref} — {title_vi}",
            size=FS_BODY, bold=True, color=(0.12, 0.28, 0.55),
        )
        self.y += 3

        # Table header
        self._write_table_row(
            _cells("#", "Bước kiểm tra (EN)", "Bước kiểm tra (VI)"),
            size=FS_TBL, bold=True, header=True,
        )

        # Node type colours (shading flag): check→True, action→False(alt), terminal→False
        TYPE_LABELS = {
            "check": "?",
            "action": "→",
            "terminal": "✓",
            "condition": "?",
            "decision": "?",
        }
        for idx, node in enumerate(nodes):
            ntype = node.get("type", "action")
            step_label = TYPE_LABELS.get(ntype, "•")
            branch = node.get("branch_label", "")
            en_text = node.get("en", "").strip()
            vi_text = node.get("vi", "").strip()
            if branch:
                en_text = f"[{branch}] {en_text}"
                vi_text = f"[{branch}] {vi_text}"
            # Alternate shading: even=light, odd=white; checks slightly different
            shaded = (ntype in ("check", "condition", "decision")) or (idx % 2 == 0)
            self._write_table_row(
                _cells(step_label, en_text, vi_text),
                size=FS_TBL,
                shaded=shaded,
            )

    def _write_bilingual_table_43_7(self, items: List[dict]) -> None:
        """Render a bilingual 4-col table for page 43-7 SERVICE DIAGNOSIS items."""
        # Column layout (pts): SW | English item | Vietnamese item | Normal value
        # Usable width = PAGE_W - 2*MARGIN ≈ 495pt
        x_sw0, x_sw1       = MARGIN,          MARGIN + 30
        x_en0, x_en1       = MARGIN + 31,     MARGIN + 200
        x_vi0, x_vi1       = MARGIN + 201,    MARGIN + 405
        x_nv0, x_nv1       = MARGIN + 406,    PAGE_W - MARGIN

        FS_TBL = 8  # slightly smaller for dense content

        def _row_cells(sw: str, en: str, vi: str, nv: str):
            return [
                (sw,  x_sw0, x_sw1),
                (en,  x_en0, x_en1),
                (vi,  x_vi0, x_vi1),
                (nv,  x_nv0, x_nv1),
            ]

        # Header row
        self._write_table_row(
            _row_cells("SW", "Service Diagnosis Item", "Hạng mục chẩn đoán dịch vụ", "Giá trị bt."),
            size=FS_TBL, bold=True, header=True,
        )

        for idx, item in enumerate(items):
            self._write_table_row(
                _row_cells(
                    item["sw"],
                    item["en_item"],
                    item["vi_item"],
                    item.get("normal", "—"),
                ),
                size=FS_TBL,
                shaded=(idx % 2 == 0),
            )

    def _write_image(
        self,
        path: Path,
        max_width: float = PAGE_W - 2 * MARGIN,
        max_height_ratio: float = 0.88,
    ):
        if not path.exists():
            self._write_line(f"[Ảnh thiếu: {path.name}]", size=FS_SMALL, color=(0.5, 0, 0))
            return
        try:
            # Load pixmap directly from file (handles PNG/JPEG without needing a PDF wrapper)
            pix = fitz.Pixmap(str(path))
            # Remove alpha channel if present
            if pix.alpha:
                pix = fitz.Pixmap(fitz.csRGB, pix)
            iw, ih = pix.width, pix.height
            usable_h = PAGE_H * max_height_ratio
            scale = min(max_width / iw, usable_h / ih, 1.0)
            w, h = iw * scale, ih * scale
            self._ensure_space(h + 10)
            rect_img = fitz.Rect(MARGIN, self.y, MARGIN + w, self.y + h)
            self.page.insert_image(rect_img, pixmap=pix)
            self.y += h + 8
        except Exception as e:
            self._write_line(f"[Lỗi ảnh: {e}]", size=FS_SMALL, color=(0.5, 0, 0))

    def _write_source_page(
        self,
        source_doc: fitz.Document,
        pdf_page_num: int,
        max_width: float = PAGE_W - 2 * MARGIN,
        max_height_ratio: float = 0.65,
    ) -> None:
        """Render toàn trang nguồn PDF — đảm bảo hình đầy đủ như bản gốc."""
        try:
            idx = pdf_page_num - 1
            if idx < 0 or idx >= len(source_doc):
                self._write_line(
                    f"[Trang nguồn không hợp lệ: {pdf_page_num}]",
                    size=FS_SMALL,
                    color=(0.5, 0, 0),
                )
                return
            src_page = source_doc[idx]
            dpi = 150
            mat = fitz.Matrix(dpi / 72.0, dpi / 72.0)
            pix = src_page.get_pixmap(matrix=mat, alpha=False)
            iw, ih = pix.width, pix.height
            scale = min(max_width / iw, (PAGE_H * max_height_ratio) / ih, 1.0)
            w, h = iw * scale, ih * scale
            self._ensure_space(h + 10)
            rect_img = fitz.Rect(MARGIN, self.y, MARGIN + w, self.y + h)
            self.page.insert_image(rect_img, pixmap=pix)
            self.y += h + 8
        except Exception as e:
            self._write_line(
                f"[Lỗi render trang nguồn {pdf_page_num}: {e}]",
                size=FS_SMALL,
                color=(0.5, 0, 0),
            )

    def _write_structured_block(self, text: str, size: int = FS_BODY, btype_str: str = "paragraph"):
        """Ghi khối văn bản với cấu trúc: list đánh số, cảnh báo, ghi chú."""
        text = clean_for_xml(text)
        if not text or text.startswith(SKIP_VI):
            return
        is_warning = btype_str == "warning"
        fn = self.fonts.fontname if self.fonts else "helv"

        for para in re.split(r"\n\s*\n", text):
            lines = [ln for ln in para.split("\n") if ln.strip()]
            if not lines:
                continue
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                # Cảnh báo / thận trọng
                if is_warning:
                    self._write_line(stripped, size=size, bold=True, color=(0.65, 0.15, 0.0))
                    continue
                # Mục danh sách đánh số: "1." / "1)" / "a." / "-" / "•"
                m_num = re.match(r"^(\d+[.\)]|\([a-z]\)|[a-z][.\)]|[-•▪])\s+(.*)", stripped)
                if m_num:
                    marker, content = m_num.group(1), m_num.group(2)
                    self._ensure_space(LINE_H)
                    top = self.y - size * 0.85
                    bot = self.y + LINE_H
                    # Marker tại MARGIN, nội dung lùi vào 18pt
                    self.page.insert_textbox(
                        fitz.Rect(MARGIN, top, MARGIN + 16, bot),
                        marker,
                        fontname=fn,
                        fontsize=size,
                        color=(0, 0, 0),
                        align=fitz.TEXT_ALIGN_LEFT,
                    )
                    self.page.insert_textbox(
                        fitz.Rect(MARGIN + 18, top, PAGE_W - MARGIN, bot),
                        content,
                        fontname=fn,
                        fontsize=size,
                        color=(0, 0, 0),
                        align=fitz.TEXT_ALIGN_LEFT,
                    )
                    self.y += LINE_H
                else:
                    self._write_line(stripped, size=size)
            self.y += 4

    def save(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.doc.save(str(path), garbage=4, deflate=True)
        self.doc.close()


def _build_block_vi_map(chunks) -> Dict[str, str]:
    m: Dict[str, str] = {}
    for c in chunks:
        if c.text_vi:
            for bid in c.source_block_ids:
                m[bid] = c.text_vi
    return m


from src.assemble.label_table import (
    PDF_LABEL_COLS,
    PDF_LABEL_HEADERS,
    build_harness_callout_section,
    build_structured_legend,
    format_vi_meaning,
    sort_labels,
    split_label,
)
from src.assemble.toc import (
    TOC_COL_HEADERS,
    TOC_TITLE,
    TOC_TITLE_VI,
    assign_output_pages,
    collect_toc_entries,
    estimate_toc_pages,
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

# Cột TOC PDF (EN | VI | Trang sổ tay | Mục gốc)
PDF_TOC_COLS = [
    (MARGIN, 245),
    (247, 415),
    (417, 465),
    (467, PAGE_W - MARGIN),
]


def _label_vi_display(lb) -> str:
    return format_vi_meaning(lb)


def _label_row_parts(lb) -> tuple[str, str, str]:
    code, desc, _ = split_label(lb.text_en)
    return code or "—", desc or "—", _label_vi_display(lb)


def _label_row_cells(lb) -> List[tuple[str, float, float]]:
    code, desc_en, vi = _label_row_parts(lb)
    cols = PDF_LABEL_COLS
    return [
        (code, cols[0][0], cols[0][1]),
        (desc_en, cols[1][0], cols[1][1]),
        (vi, cols[2][0], cols[2][1]),
    ]


def _legend_row_cells(row) -> List[tuple[str, float, float]]:
    cols = PDF_LABEL_COLS
    return [
        (row.code, cols[0][0], cols[0][1]),
        (row.name_en or "—", cols[1][0], cols[1][1]),
        (row.meaning_vi, cols[2][0], cols[2][1]),
    ]


def _current_page_num(w: _PdfWriter) -> int:
    """Số trang 1-based trong doc hiện tại."""
    return len(w.doc) if w.page is not None else max(1, len(w.doc))


def _write_wire_appendix(w: _PdfWriter) -> int:
    """Ghi Phụ lục A; trả về số trang bắt đầu (1-based)."""
    w._ensure_space(40)
    start = _current_page_num(w)
    w._write_line(APPENDIX_TITLE, size=FS_HEAD, bold=True)
    w._write_line(appendix_intro(), size=FS_SMALL, color=(0.3, 0.3, 0.3))
    w._write_columns(
        [
            ("Mã", PDF_LABEL_COLS[0][0], PDF_LABEL_COLS[0][1]),
            ("English", PDF_LABEL_COLS[1][0], PDF_LABEL_COLS[1][1]),
            ("Tiếng Việt", PDF_LABEL_COLS[2][0], PDF_LABEL_COLS[2][1]),
        ],
        size=FS_SMALL,
        bold=True,
        color=(0.2, 0.2, 0.2),
    )
    for code, en, vi in color_table_rows():
        w._write_columns(
            [
                (code, PDF_LABEL_COLS[0][0], PDF_LABEL_COLS[0][1]),
                (en, PDF_LABEL_COLS[1][0], PDF_LABEL_COLS[1][1]),
                (vi, PDF_LABEL_COLS[2][0], PDF_LABEL_COLS[2][1]),
            ],
            size=FS_SMALL,
        )
    w.y += 4
    w._write_line("Ghi chú:", size=FS_SMALL, bold=True)
    for line in APPENDIX_NOTES_VI:
        w._write_line(line, size=FS_SMALL, color=(0.25, 0.25, 0.25))
    w.y += 12
    w._new_page()
    return start


def _stamp_page_numbers(doc: fitz.Document) -> None:
    """Đóng số trang vào góc phải dưới mỗi trang (Helvetica built-in)."""
    n = len(doc)
    for i in range(n):
        page = doc[i]
        pnum = str(i + 1)
        rect = fitz.Rect(
            PAGE_W - MARGIN - 36,
            PAGE_H - MARGIN + 6,
            PAGE_W - MARGIN,
            PAGE_H - 4,
        )
        page.insert_textbox(
            rect,
            pnum,
            fontname="helv",
            fontsize=8,
            color=(0.4, 0.4, 0.4),
            align=fitz.TEXT_ALIGN_RIGHT,
        )


def _handle_sec43_page(
    w: _PdfWriter,
    page,
    block_vi: Dict[str, str],
    workspace: Path,
    source_doc: Optional[fitz.Document],
    rendered_source_pages: set,
) -> bool:
    """Render one Section 43 page per locked format spec. Returns True if handled."""
    ref = page.manual_ref or ""
    vi_fc_dir = workspace / "assets" / "flowcharts_vi"

    def _write_sec43_headings():
        """Write heading + subheading blocks (VI) for this page."""
        for block in page.blocks:
            if block.type == BlockType.HEADING:
                en = (block.text_en or "").strip()
                if not en:
                    continue
                vi = _resolve_block_vi(block, block_vi, ref)
                w._write_line(en, size=FS_HEAD, color=(0.4, 0.4, 0.4))
                w._write_line(vi or en, size=FS_HEAD, bold=True)
                w.y += 4

    def _write_sec43_intro():
        """Write first translatable paragraph as VI intro."""
        for block in page.blocks:
            if block.type in (BlockType.PARAGRAPH, BlockType.LIST):
                en = (block.text_en or "").strip()
                if not en or len(en) < 30:
                    continue
                vi = _resolve_block_vi(block, block_vi, ref)
                if vi and vi != en:
                    w._write_structured_block(vi, btype_str=block.type.value)
                    return

    # ── MATRIX TABLE PAGES (43-3, 43-4, 43-5) ────────────────────────────────
    # Original: complex 2D cross-reference matrix (cannot be re-rendered from chunks)
    # Format: VI section heading + note + source page image (full-width)
    if ref in SEC43_MATRIX_REFS:
        _write_sec43_headings()
        w._write_line(
            "▸ Bảng chẩn đoán điện — xem bảng gốc tiếng Anh dưới đây:",
            size=FS_SMALL, bold=True, color=(0.15, 0.25, 0.55),
        )
        if source_doc and page.pdf_page not in rendered_source_pages:
            rendered_source_pages.add(page.pdf_page)
            w._write_source_page(source_doc, page.pdf_page, max_height_ratio=0.82)
        return True

    # ── ERROR CODE TABLE PAGE (43-6) ─────────────────────────────────────────
    # Original: intro para + 2-col error-code table + figure
    # Format: VI heading + VI intro para + source page image (preserves table + figure)
    if ref == SEC43_ERR_TABLE_REF:
        _write_sec43_headings()
        _write_sec43_intro()
        w._write_line(
            "▸ Bảng mã lỗi — xem bảng gốc tiếng Anh dưới đây:",
            size=FS_SMALL, bold=True, color=(0.15, 0.25, 0.55),
        )
        if source_doc and page.pdf_page not in rendered_source_pages:
            rendered_source_pages.add(page.pdf_page)
            w._write_source_page(source_doc, page.pdf_page, max_height_ratio=0.80)
        return True

    # ── SERVICE DIAGNOSIS TABLE PAGE (43-7) ──────────────────────────────────
    # Original: intro para + 4-col SERVICE DIAGNOSIS table (50+ rows)
    # Format: VI heading + VI intro para + BILINGUAL TABLE (SW | EN | VI | Normal)
    # PNG is too cramped to read — replaced by programmatic table via for-loop.
    if ref == SEC43_SVC_DIAG_REF:
        from src.translate.section43_table import load_json_cache, save_json_cache
        _write_sec43_headings()
        _write_sec43_intro()
        # Ensure JSON cache exists
        save_json_cache(workspace)
        items = load_json_cache(workspace)
        w._write_line(
            "▸ Bảng chẩn đoán dịch vụ — SW00 đến SW36 (song ngữ EN | VI):",
            size=FS_SMALL, bold=True, color=(0.0, 0.4, 0.15),
        )
        w.y += 4
        w._write_bilingual_table_43_7(items)
        return True

    # ── FLOWCHART PAGES (43-8 to 43-22) ─────────────────────────────────────
    # Original: symptom label + YES/NO flowchart tree
    # Format: if PNG has >= 10 nodes → cramped → replace with bilingual decision table.
    #         If PNG has < 10 nodes → small enough → embed VI PNG directly.
    if ref in SEC43_FLOWCHART_REFS:
        import json as _json
        vi_png = vi_fc_dir / f"{ref}.png"
        fc_json = vi_fc_dir / f"{ref}.json"
        # Determine rendering mode: table for cramped diagrams
        use_table = False
        fc_nodes: List[dict] = []
        fc_title_vi = ref
        if fc_json.exists():
            with open(fc_json, encoding="utf-8") as _fh:
                fc_data = _json.load(_fh)
            fc_nodes = fc_data.get("nodes", [])
            fc_title_vi = fc_data.get("title_vi") or fc_data.get("title_en") or ref
            use_table = len(fc_nodes) >= 10  # cramped above this threshold

        if use_table and fc_nodes:
            w._write_flowchart_decision_table(fc_nodes, fc_title_vi, ref)
            # Optionally embed small EN source reference
            if source_doc and page.pdf_page not in rendered_source_pages:
                rendered_source_pages.add(page.pdf_page)
                w._write_line(
                    f"(Sơ đồ gốc EN — {ref})",
                    size=FS_SMALL - 1, color=(0.6, 0.6, 0.6),
                )
        elif vi_png.exists():
            w._write_image(vi_png, max_height_ratio=0.88)
        elif source_doc and page.pdf_page not in rendered_source_pages:
            rendered_source_pages.add(page.pdf_page)
            w._write_line(
                f"▸ Lưu đồ chẩn đoán — {ref} (chưa có bản vẽ VI)",
                size=FS_SMALL, bold=True, color=(0.2, 0.2, 0.55),
            )
            w._write_source_page(source_doc, page.pdf_page, max_height_ratio=0.82)
        return True

    return False


def _write_body(
    w: _PdfWriter,
    document,
    block_vi: Dict[str, str],
    registry,
    workspace: Path,
    source_doc: Optional[fitz.Document] = None,
) -> tuple[Dict[str, int], Dict[str, int], int]:
    """Ghi phụ lục + nội dung; trả (map mục sổ tay, map số mục 23.x.y, trang phụ lục)."""
    section_pages: Dict[str, int] = {}
    heading_pages: Dict[str, int] = {}
    appendix_page = _write_wire_appendix(w)
    sec_num_re = re.compile(r"^(\d{2}(?:\.\d+)+)")
    # Theo dõi trang nguồn đã render toàn trang (tránh render nhiều lần cho cùng trang)
    rendered_source_pages: set[int] = set()

    for page in document.pages:
        sec = page.manual_ref or f"PDF-{page.pdf_page}"
        w._ensure_space(30)
        if sec not in section_pages:
            section_pages[sec] = _current_page_num(w)
        w._write_line(f"── {sec} ──", size=FS_SMALL, bold=True, color=(0.3, 0.3, 0.3))
        rendered: set[str] = set()

        # ── Section 43 pages: dispatch to locked-format handler ───────────────
        ref = page.manual_ref or ""
        if ref.startswith("43-"):
            handled = _handle_sec43_page(
                w, page, block_vi, workspace, source_doc, rendered_source_pages
            )
            if handled:
                w.y += 8
                continue

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
                en = block.text_en.strip()
                vi = block_vi.get(block.block_id, "") or translate_heading_text(en)
                if (not vi or vi == en) and (page.manual_ref or "").startswith("43"):
                    vi = _resolve_block_vi(block, block_vi, page.manual_ref or "")
                # Chỉ ghi nhận số mục từ heading nội dung — bỏ dòng TOC gốc (.... 23-xx)
                is_source_toc = bool(re.search(r"\.{3,}", en)) or (
                    btype == BlockType.LIST
                    and re.match(r"^\d{2}\.", en.split("\n")[0].strip())
                    and "...." in en
                )
                if not is_source_toc:
                    head_line = en.split("\n")[0].strip()
                    en_lines = [ln.strip() for ln in en.split("\n") if ln.strip()]
                    if len(en_lines) >= 2 and re.match(r"^\d{2}(?:\.\d+)*$", en_lines[0]):
                        head_line = f"{en_lines[0]} {en_lines[1]}"
                    m = sec_num_re.match(head_line)
                    if m and m.group(1) not in heading_pages:
                        heading_pages[m.group(1)] = _current_page_num(w)
                w._write_line(en, size=FS_HEAD, color=(0.4, 0.4, 0.4))
                w._write_line(vi or "—", size=FS_HEAD, bold=True)
                w.y += 4
                continue

            if btype == BlockType.FIGURE:
                if block.block_id in rendered:
                    continue
                rendered.add(block.block_id)
                if block.full_page:
                    # Trang sơ đồ vector đã được render đầy đủ khi ingest — dùng trực tiếp
                    if block.image_path:
                        img = workspace / block.image_path
                        if not img.exists():
                            img = workspace.parent / block.image_path
                        w._write_image(img)
                elif source_doc is not None:
                    # Render toàn trang nguồn PDF — tránh ảnh cắt xén
                    if page.pdf_page not in rendered_source_pages:
                        rendered_source_pages.add(page.pdf_page)
                        w._write_source_page(source_doc, page.pdf_page)
                    # Các figure block khác cùng trang nguồn → chỉ hiện legend, bỏ ảnh
                else:
                    # Fallback: dùng ảnh đã trích (cropped) khi không có source PDF
                    if block.image_path:
                        img = workspace / block.image_path
                        if not img.exists():
                            img = workspace.parent / block.image_path
                        w._write_image(img)
                fig_num = cap_vi = ""
                if block.figure_id and block.figure_id in registry:
                    entry = registry[block.figure_id]
                    fig_num = entry.figure_number
                    cap_vi = entry.caption_vi or entry.caption_en or ""
                # Luôn hiển thị số hình — fallback từ figure_id nếu không có trong registry
                if not fig_num and block.figure_id:
                    m_fid = re.match(r"fig-(.+)", block.figure_id)
                    if m_fid:
                        fig_num = m_fid.group(1)
                cap_label = f"Hình {fig_num}. {cap_vi}".strip(" .") if cap_vi else f"Hình {fig_num}"
                if fig_num:
                    w._write_line(cap_label, size=FS_BODY, bold=True)
                labels = block.labels or (
                    registry[block.figure_id].labels
                    if block.figure_id and block.figure_id in registry
                    else []
                )
                cap_en = ""
                if block.figure_id and block.figure_id in registry:
                    cap_en = registry[block.figure_id].caption_en or ""
                # Vision legend first (best quality), then OCR structured labels
                vision_sections = build_harness_callout_section(
                    caption_en=cap_en,
                    figure_id=block.figure_id or "",
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
                    deduped = []
                    for sec in ocr_sections:
                        kept_rows = [
                            r for r in sec.rows
                            if not (r.code == "—" and (r.name_en or "").strip().upper() in vision_en)
                        ]
                        if kept_rows:
                            from src.assemble.label_table import LegendSection
                            deduped.append(LegendSection(title=sec.title, rows=kept_rows))
                    ocr_sections = deduped
                sections = vision_sections + ocr_sections
                if sections:
                    w._write_line(
                        "Chú thích tra cứu (theo mã):",
                        size=FS_SMALL,
                        bold=True,
                    )
                    w._write_line(FIGURE_WIRE_NOTE, size=FS_SMALL, color=(0.4, 0.4, 0.4))
                    hdr_cells = [
                        (PDF_LABEL_HEADERS[i], PDF_LABEL_COLS[i][0], PDF_LABEL_COLS[i][1])
                        for i in range(3)
                    ]
                    w._write_columns(hdr_cells, size=FS_SMALL, color=(0.2, 0.2, 0.2), bold=True)
                    for sec_leg in sections:
                        if sec_leg.title and (
                            "Nhãn chữ" in sec_leg.title
                            or "Vision" in sec_leg.title
                            or "Nhãn đọc" in sec_leg.title
                        ):
                            w._write_line(sec_leg.title, size=FS_SMALL, bold=True, color=(0.2, 0.2, 0.45))
                        for row in sec_leg.rows:
                            color = SUSPICION_COLOR if row.suspicion else (0, 0, 0)
                            w._write_columns(_legend_row_cells(row), size=FS_SMALL, color=color)
                w.y += 6
                continue

            if btype == BlockType.CAPTION:
                continue

            if btype in PROSE_TYPES:
                en = block.text_en
                vi = _resolve_block_vi(block, block_vi, page.manual_ref or "")
                vi = apply_structure_from_en(en, vi)
                w._write_structured_block(vi, btype_str=btype.value)
                continue

        w.y += 8

    return section_pages, heading_pages, appendix_page


def _write_toc_pages(w: _PdfWriter, title: str, entries) -> None:
    w._new_page()
    w._write_line(title, size=FS_TITLE, bold=True)
    w._write_line(
        "Nội dung tiếng Việt — font Arial Unicode. Nhãn [⚠ OCR] cần KTV xác minh.",
        size=FS_SMALL,
        color=(0.4, 0.4, 0.4),
    )
    w.y += 6
    w._write_line(TOC_TITLE, size=FS_HEAD, bold=True)
    w._write_line(TOC_TITLE_VI, size=FS_SMALL, color=(0.35, 0.35, 0.35))
    w._write_line(
        "Mỗi mục: English — Tiếng Việt | Trang sổ tay = số trang đóng góc phải bản VI | Mục gốc = 23-xx / 43-xx",
        size=FS_SMALL,
        color=(0.4, 0.4, 0.4),
    )
    w.y += 4
    hdr = [
        (TOC_COL_HEADERS[i], PDF_TOC_COLS[i][0], PDF_TOC_COLS[i][1])
        for i in range(4)
    ]
    w._write_columns(hdr, size=FS_SMALL, bold=True, color=(0.2, 0.2, 0.2))
    for entry in entries:
        cells = toc_row_cells(entry)
        w._write_columns(
            [(cells[i], PDF_TOC_COLS[i][0], PDF_TOC_COLS[i][1]) for i in range(4)],
            size=FS_SMALL,
        )


def build_pdf(
    workspace: Path,
    output_path: Path,
    title: str,
    source_pdf_path: Optional[Path] = None,
) -> Path:
    """
    Hai lượt: (1) ghi body + map trang mục sổ tay,
    (2) ghi TOC song ngữ với số trang PDF VI, ghép TOC + body.

    source_pdf_path: đường dẫn PDF gốc để render ảnh đầy đủ (thay ảnh cắt xén).
    """
    import json

    document = load_document(workspace)
    chunks = load_chunks(workspace)
    block_vi = _build_block_vi_map(chunks)
    registry = document.figures_registry

    toc_entries = collect_toc_entries(document, block_vi)

    # Mở source PDF để render ảnh toàn trang (nếu có)
    source_doc: Optional[fitz.Document] = None
    if source_pdf_path is None:
        # Thử tự tìm theo tên lưu trong document model
        candidate = Path("data") / document.source_file
        if candidate.exists():
            source_pdf_path = candidate
    if source_pdf_path and source_pdf_path.exists():
        try:
            source_doc = fitz.open(str(source_pdf_path))
        except Exception:
            source_doc = None

    # Lượt 1 — body (phụ lục + nội dung)
    body = _PdfWriter(title)
    body._new_page()
    section_pages, heading_pages, appendix_page = _write_body(
        body, document, block_vi, registry, workspace, source_doc=source_doc
    )

    # Ước trang TOC → ghi thử → chỉnh nếu lệch
    toc_pages_est = estimate_toc_pages(len(toc_entries))
    assign_output_pages(
        toc_entries,
        section_pages,
        appendix_page=appendix_page,
        toc_page_offset=toc_pages_est,
        heading_pages=heading_pages,
    )
    front = _PdfWriter(title)
    _write_toc_pages(front, title, toc_entries)
    actual_toc = len(front.doc)
    if actual_toc != toc_pages_est:
        assign_output_pages(
            toc_entries,
            section_pages,
            appendix_page=appendix_page,
            toc_page_offset=actual_toc,
            heading_pages=heading_pages,
        )
        front.doc.close()
        front = _PdfWriter(title)
        _write_toc_pages(front, title, toc_entries)
        actual_toc = len(front.doc)

    final = fitz.open()
    final.insert_pdf(front.doc)
    final.insert_pdf(body.doc)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    _stamp_page_numbers(final)
    final.save(str(output_path), garbage=4, deflate=True)
    final.close()
    front.doc.close()
    body.doc.close()
    if source_doc is not None:
        source_doc.close()

    # Lưu map trang cho DOCX / QA
    page_map = {ref: pg + actual_toc for ref, pg in section_pages.items()}
    page_map["appendix"] = appendix_page + actual_toc
    qa = workspace / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    (qa / "toc_pages.json").write_text(
        json.dumps(page_map, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    toc_rows = [
        {
            "en": e.en,
            "vi": e.vi,
            "manual_ref": e.manual_ref,
            "output_page": e.output_page,
            "level": e.level,
        }
        for e in toc_entries
    ]
    (qa / "toc_entries.json").write_text(
        json.dumps(toc_rows, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path
