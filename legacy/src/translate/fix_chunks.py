"""Phát hiện và sửa chunk bị remap sai / dịch lỗi."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Tuple

from src.glossary.manager import load_glossary
from src.ingest.text_structure import normalize_pdf_text
from src.models import BlockStatus, Chunk
from src.translate.chunker import load_chunks, save_chunk
from src.translate.engine import translate_chunk
from src.translate.flowchart import translate_flowchart_chunk
from src.translate.list_translator import is_bom_block, is_equipment_list, translate_list_chunk
from src.qa.mix_detector import is_mixed_vi_en, scan_text_lines

CORRUPT_SIG = re.compile(
    r"gover\s*KHÔNGr|KHÔNGr\s*mô|STT\.\d+TênMã|máy khởi động cáp|ắc quy rơ-le cáp|Part KHÔNG|"
    r"KHÔNG\s*motions|KHÔNGt\b|drnếuts|Only\s+gầu|Fine\s+vận\s+hành|"
    r"Chẩn đoán sự cố item|selector van|tháo grommet|công tắces",
    re.I,
)
BOM_GLUE = re.compile(r"STT\.\d+Tên|LẮP RÁP.*Y[A-Z]\d{2}", re.I)


def _line_count(text: str) -> int:
    return len([ln for ln in text.split("\n") if ln.strip()])


def is_corrupted(chunk: Chunk) -> bool:
    if not chunk.text_vi:
        return False
    en, vi = chunk.text_en, chunk.text_vi
    if CORRUPT_SIG.search(vi) or BOM_GLUE.search(vi):
        return True
    # Lẫn Anh–Việt (detector mới)
    if is_mixed_vi_en(vi) or scan_text_lines(vi):
        return True
    # BOM/list còn header EN hoặc mounting chưa dịch
    if is_bom_block(en) or is_equipment_list(en):
        if re.search(r"\b(Name|Part No\.?|Q'?ty|MOUNTING|WASHER TANK|CIGALETTE|KYE SWITCH)\b", vi, re.I):
            return True
    if re.search(r"\bMounting\b", vi) and re.search(r"\b(cab|light|boom)\b", vi, re.I):
        return True
    if vi.count("\n\n") >= 3 and len(en) < 200:
        return True
    en_l, vi_l = _line_count(en), _line_count(vi)
    ref = chunk.manual_ref or ""

    if ref.startswith("43"):
        if en_l >= 3 and abs(en_l - vi_l) > max(2, en_l * 0.4):
            return True
        if en_l <= 6 and vi_l > en_l * 2 and vi_l >= 6:
            return True

    if ref.startswith("23"):
        if is_bom_block(en) or is_equipment_list(en):
            if en_l >= 3 and vi_l < en_l * 0.5:
                return True
            if BOM_GLUE.search(vi):
                return True
        if en_l >= 3 and abs(en_l - vi_l) > max(2, en_l * 0.4):
            if is_equipment_list(en) or en_l <= 12:
                return True
    return False


def should_refresh_list(chunk: Chunk) -> bool:
    """Luôn làm mới BOM/list khi chạy fix-chunks (cụm phrase cập nhật)."""
    en = chunk.text_en or ""
    return is_bom_block(en) or is_equipment_list(en)


def fix_all_corrupted(
    workspace: Path,
    *,
    apply_rebatch: bool = True,
    force_section43: bool = True,
) -> Tuple[int, List[str]]:
    glossary = load_glossary(workspace)
    rebatch = _load_rebatch(workspace) if apply_rebatch else {}
    fixed_ids: List[str] = []

    for chunk in load_chunks(workspace):
        ref = chunk.manual_ref or ""
        needs_fix = is_corrupted(chunk) or should_refresh_list(chunk)
        if force_section43 and ref.startswith("43"):
            needs_fix = True
        if not needs_fix and chunk.chunk_id in rebatch:
            if rebatch[chunk.chunk_id] != chunk.text_vi:
                needs_fix = True
        if not needs_fix:
            continue

        vi = fix_chunk(chunk, glossary, rebatch)
        if not vi:
            continue
        if vi == chunk.text_vi and not is_corrupted(chunk) and not (
            force_section43 and ref.startswith("43")
        ):
            # refresh list unchanged — vẫn ok, không đếm
            if should_refresh_list(chunk) and vi == chunk.text_vi:
                continue
        chunk.text_vi = vi
        chunk.status = BlockStatus.DONE
        save_chunk(workspace, chunk)
        fixed_ids.append(chunk.chunk_id)

    return len(fixed_ids), fixed_ids


def _is_numeric_block(text: str) -> bool:
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    return bool(lines) and all(re.match(r"^[\d.~\-]+$", ln) for ln in lines)


def _load_rebatch(workspace: Path) -> dict[str, str]:
    merged: dict[str, str] = {}
    rebatch_dir = workspace / "translations" / "rebatch"
    if not rebatch_dir.exists():
        return merged
    for path in sorted(rebatch_dir.glob("batch_*_output.json")):
        merged.update(json.loads(path.read_text(encoding="utf-8")))
    return merged


def fix_chunk(chunk: Chunk, glossary, rebatch: dict[str, str]) -> str:
    en_norm = normalize_pdf_text(chunk.text_en)
    if _is_numeric_block(en_norm):
        return en_norm

    # Section 43: ưu tiên dịch flowchart/map (rebatch cũ hay word-by-word sai)
    if chunk.manual_ref and chunk.manual_ref.startswith("43"):
        vi = translate_flowchart_chunk(chunk, glossary)
        if vi:
            return vi

    if chunk.chunk_id in rebatch:
        return rebatch[chunk.chunk_id]

    if is_bom_block(chunk.text_en) or is_equipment_list(chunk.text_en):
        return translate_list_chunk(chunk, glossary)

    return translate_chunk(chunk, glossary)
