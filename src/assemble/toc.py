"""Mục lục song ngữ EN | VI + số trang bản output."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional

from src.models import BlockType, Document
from src.translate.engine import HEADING_PHRASES

TOC_TITLE = "MỤC LỤC / TABLE OF CONTENTS"
TOC_TITLE_VI = "Mục lục (song ngữ Anh–Việt)"

# Tiêu đề section / BOM thường gặp — bổ sung HEADING_PHRASES
EXTRA_HEADINGS: Dict[str, str] = {
    "MOUNTING LIGHT": "LẮP ĐÈN",
    "MOUNTING CAB": "LẮP BUỒNG LÁI",
    "MOUNTING BOOM LIGHT": "LẮP ĐÈN CẦN",
    "WINDOW WASHER TANK & PUMP": "BÌNH NƯỚC RỬA KÍNH & BƠM",
    "WINDOW WASHER TANK AND PUMP": "BÌNH NƯỚC RỬA KÍNH & BƠM",
    "WASHER TANK": "BÌNH NƯỚC RỬA",
    "WASHER TANK & PUMP": "BÌNH NƯỚC RỬA & BƠM",
    "CAB SWITCH INSTAL": "LẮP CÔNG TẮC BUỒNG LÁI",
    "INSTRUMENT PANEL HARNESS": "BỘ DÂY BẢNG ĐỒNG HỒ",
    "CONTROLLER ASSY": "LẮP RÁP BỘ ĐIỀU KHIỂN",
    "BATTERY": "ẮC QUY",
    "CAB ASSY": "LẮP RÁP BUỒNG LÁI",
    "AIR CONDITIONER": "ĐIỀU HÒA",
    "HARNESS": "BỘ DÂY",
}


@dataclass
class TocEntry:
    en: str
    vi: str
    manual_ref: Optional[str] = None
    output_page: Optional[int] = None  # trang trong PDF VI (1-based)
    level: int = 1


def _clean_heading(text: str) -> str:
    t = re.sub(r"\.{2,}.+$", "", text)  # bỏ leader dots + page
    t = re.sub(r"\s+", " ", t).strip()
    return t


def translate_heading_text(en: str) -> str:
    """Dịch tiêu đề bằng phrase map (không word-by-word)."""
    raw = _clean_heading(en)
    if not raw:
        return ""
    # Khớp nguyên cụm (bỏ số mục đầu)
    m = re.match(r"^(\d+(?:\.\d+)*(?:-\d+)*)\s*(.*)$", raw)
    num, rest = ("", raw)
    if m:
        num, rest = m.group(1), m.group(2).strip()

    combined = {**HEADING_PHRASES, **EXTRA_HEADINGS}
    # Exact / contains longest first
    rest_up = rest.upper()
    for en_p, vi_p in sorted(combined.items(), key=lambda x: -len(x[0])):
        if rest_up == en_p.upper() or rest_up.startswith(en_p.upper()):
            return f"{num} {vi_p}".strip() if num else vi_p
        if en_p.upper() in rest_up and len(en_p) >= 8:
            return f"{num} {vi_p}".strip() if num else vi_p

    # Full string match
    for en_p, vi_p in sorted(combined.items(), key=lambda x: -len(x[0])):
        if raw.upper() == en_p.upper() or en_p.upper() in raw.upper():
            if num and en_p.upper() in rest_up:
                return f"{num} {vi_p}".strip()
            if raw.upper() == en_p.upper():
                return vi_p
    return ""


def collect_toc_entries(
    document: Document,
    block_vi: Optional[Dict[str, str]] = None,
) -> List[TocEntry]:
    """Lấy đề mục chính từ document (heading / list mục lục gốc)."""
    block_vi = block_vi or {}
    entries: List[TocEntry] = []
    seen: set[str] = set()

    # Phụ lục A luôn đầu tiên sau title
    from src.assemble.wire_appendix import APPENDIX_TITLE

    entries.append(
        TocEntry(
            en="Appendix A. Wire color coding table",
            vi=APPENDIX_TITLE,
            manual_ref=None,
            level=1,
        )
    )

    target_ref_re = re.compile(r"(\d{2}-\d{1,3})\s*$")

    for page in document.pages:
        ref = page.manual_ref
        for block in page.blocks:
            if block.type == BlockType.FOOTER or block.type == BlockType.PAGE_REF:
                continue
            en = (block.text_en or "").strip()
            if not en:
                continue
            lines = [ln.strip() for ln in en.split("\n") if ln.strip()]
            # TOC gốc thường 2 dòng: "23.2.4" + "INSTRUMENT PANEL ASSY .... 23-13"
            if (
                len(lines) >= 2
                and re.match(r"^\d{2}(?:\.\d+)*(?:-\d+)*$", lines[0])
                and re.search(r"[A-Za-z]{3,}", lines[1])
            ):
                joined = f"{lines[0]} {lines[1]}"
                if len(lines) >= 3 and re.match(r"^\d{2}-\d", lines[2]):
                    joined = f"{joined} {lines[2]}"
                first = joined
            else:
                first = lines[0]

            is_heading = block.type == BlockType.HEADING
            is_toc_line = block.type == BlockType.LIST and re.match(
                r"^\d{2}\.", first
            )
            # Section banners như 23.2.4 INSTRUMENT PANEL ASSY
            is_sec = bool(re.match(r"^23\.\d|^43\.\d", first)) and len(first) < 120
            if not (is_heading or is_toc_line or is_sec):
                continue
            # Bỏ dòng chỉ số trang
            if re.fullmatch(r"\d{2}-\d{1,3}", first):
                continue
            # Mục lục gốc: số trang đích nằm cuối dòng (.... 23-36)
            dest = None
            mref = target_ref_re.search(_clean_heading(first))
            if mref and is_toc_line:
                dest = mref.group(1)
                # Bỏ leader dots + số trang đích khỏi tiêu đề
                title_en = target_ref_re.sub("", first)
                title_en = re.sub(r"\.{2,}\s*$", "", title_en).strip()
                title_en = re.sub(r"\s{2,}", " ", title_en)
            else:
                title_en = _clean_heading(first)
                dest = ref

            key = title_en.upper()
            if key in seen or len(key) < 4:
                continue
            # Bỏ mục chỉ còn số (23.1.1 không có tên)
            if re.fullmatch(r"\d{2}(?:\.\d+)+(?:-\d+)*", key):
                continue
            seen.add(key)
            # Ưu tiên phrase map tiêu đề (ổn định); chunk VI có thể word-by-word
            vi = translate_heading_text(title_en)
            if not vi:
                raw_vi = block_vi.get(block.block_id) or ""
                vi = _clean_heading(raw_vi.split("\n")[0]) if raw_vi else ""
                vi = target_ref_re.sub("", vi)
                vi = re.sub(r"\.{2,}\s*$", "", vi).strip()
            if not vi:
                vi = title_en
            level = 1
            if re.match(r"^\d{2}\.\d+\.\d+", title_en):
                level = 3
            elif re.match(r"^\d{2}\.\d+", title_en):
                level = 2
            entries.append(
                TocEntry(
                    en=title_en[:90],
                    vi=vi[:90],
                    manual_ref=dest,
                    level=level,
                )
            )
    return entries


def format_toc_line(entry: TocEntry, with_page: bool = True) -> str:
    page = f"{entry.output_page}" if (with_page and entry.output_page) else "—"
    ref = entry.manual_ref or "—"
    indent = "  " * max(0, entry.level - 1)
    return f"{indent}{entry.en}  |  {entry.vi}  |  tr.{page}  |  {ref}"


def format_toc_bilingual(entry: TocEntry) -> str:
    """Một dòng TOC: EN — VI (dễ tra)."""
    indent = "  " * max(0, entry.level - 1)
    en = entry.en
    vi = entry.vi
    if vi and vi.upper() != en.upper():
        return f"{indent}{en}  —  {vi}"
    return f"{indent}{en}"


def resolve_manual_ref(dest: Optional[str], known_refs: set[str]) -> Optional[str]:
    """Khớp số trang sổ tay bị OCR cắt (23-3 → 23-3 / 23-30…)."""
    if not dest:
        return None
    if dest in known_refs:
        return dest
    # prefix match ngắn nhất hợp lệ
    cands = sorted(r for r in known_refs if r.startswith(dest))
    if len(cands) == 1:
        return cands[0]
    # 23 → bỏ
    if re.fullmatch(r"\d{2}", dest):
        return None
    return dest if dest in known_refs else None


# Gợi ý trang sổ tay khi OCR cắt số cuối (dùng khi không khớp heading_pages)
SECTION_START_REF: Dict[str, str] = {
    "23.1": "23-3",
    "23.1.1": "23-3",
    "23.1.2": "23-6",
    "23.2": "23-7",
    "23.2.1": "23-7",
    "23.2.2": "23-8",
    "23.2.3": "23-9",
    "23.2.4": "23-13",
    "23.2.5": "23-15",
    "23.2.6": "23-16",
    "23.2.7": "23-20",
    "23.2.8": "23-26",
    "23.2.9": "23-30",
    "23.2.10": "23-33",
    "23.2.11": "23-34",
    "23.2.12": "23-35",
    "23.2.13": "23-36",
    "23.2.14": "23-36",
    "23.2.15": "23-37",
    "23.2.16": "23-39",
    "23.2.17": "23-40",
    "23.2.18": "23-41",
    "43.1": "43-1",
    "43.2": "43-1",
    "43.3": "43-2",
}


def assign_output_pages(
    entries: List[TocEntry],
    section_pages: Dict[str, int],
    *,
    appendix_page: Optional[int] = None,
    toc_page_offset: int = 0,
    heading_pages: Optional[Dict[str, int]] = None,
) -> None:
    """Gán số trang output (1-based toàn PDF) từ map mục sổ tay / số mục 23.x."""
    known = set(section_pages.keys())
    heading_pages = heading_pages or {}
    sec_num_re = re.compile(r"^(\d{2}(?:\.\d+)+)")
    for e in entries:
        if appendix_page and (
            "appendix" in e.en.lower() or "phụ lục" in (e.vi or "").lower()
        ):
            e.output_page = appendix_page + toc_page_offset
            continue
        # Ưu tiên map mục sổ tay đã biết (đầu section trong body)
        m = sec_num_re.match(e.en.strip())
        sec_key = m.group(1) if m else None
        if sec_key and sec_key in SECTION_START_REF:
            hint = SECTION_START_REF[sec_key]
            if hint in section_pages:
                e.manual_ref = hint
                e.output_page = section_pages[hint] + toc_page_offset
                continue
        if sec_key and sec_key in heading_pages:
            e.output_page = heading_pages[sec_key] + toc_page_offset
            continue
        ref = resolve_manual_ref(e.manual_ref, known)
        if ref and ref in section_pages:
            e.manual_ref = ref
            e.output_page = section_pages[ref] + toc_page_offset
        else:
            e.output_page = None


def estimate_toc_pages(n_entries: int, lines_per_page: int = 42) -> int:
    """Ước số trang TOC (title + header + entries)."""
    total_lines = n_entries + 8
    return max(1, (total_lines + lines_per_page - 1) // lines_per_page)


TOC_COL_HEADERS = ("English", "Tiếng Việt", "Trang sổ tay", "Mục gốc")


def toc_row_cells(entry: TocEntry) -> tuple[str, str, str, str]:
    page = str(entry.output_page) if entry.output_page else "—"
    ref = entry.manual_ref or "—"
    indent = "  " * max(0, entry.level - 1)
    return (
        f"{indent}{entry.en}",
        f"{indent}{entry.vi}",
        page,
        ref,
    )
