"""Khôi phục bản dịch từ chunks_vi + translations.json."""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.ingest.text_structure import apply_structure_from_en, normalize_pdf_text
from src.models import BlockStatus
from src.translate.chunker import load_chunks, load_chunks_backup, save_chunk


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", normalize_pdf_text(s).strip())


def remap_all(workspace: Path) -> int:
    backup = load_chunks_backup(workspace)
    chunks = load_chunks(workspace)

    by_block: dict[str, str] = {}
    by_en: dict[str, str] = {}
    for old in backup:
        if not old.text_vi or old.text_vi.startswith("[GIỮ NGUYÊN"):
            continue
        for bid in old.source_block_ids:
            by_block[bid] = old.text_vi
        by_en[_norm(old.text_en)] = old.text_vi

    trans_path = workspace / "translations.json"
    if trans_path.exists():
        trans = json.loads(trans_path.read_text(encoding="utf-8"))
        # translations.json keyed by old chunk_id — pair with backup text_en
        for old in backup:
            if old.chunk_id in trans:
                by_en[_norm(old.text_en)] = trans[old.chunk_id]

    count = 0
    for c in chunks:
        if c.status == BlockStatus.DONE and c.text_vi:
            continue
        vi = None
        for bid in c.source_block_ids:
            if bid in by_block:
                vi = by_block[bid]
                break
        if not vi:
            vi = by_en.get(_norm(c.text_en))
        # Không fuzzy-match theo substring — gây gán nhầm blob TOC gộp nhiều mục
        if vi:
            c.text_vi = apply_structure_from_en(c.text_en, vi)
            c.status = BlockStatus.DONE
            save_chunk(workspace, c)
            count += 1
    return count
