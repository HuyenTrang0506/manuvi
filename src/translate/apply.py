"""Áp dụng bản dịch từ dict {chunk_id: text_vi} vào workspace."""

from __future__ import annotations

import json
from pathlib import Path

from src.models import BlockStatus
from src.translate.chunker import load_chunks, save_chunk


def apply_translations(
    workspace: Path,
    translations: dict[str, str],
    *,
    only_pending: bool = True,
) -> int:
    applied = 0
    for chunk in load_chunks(workspace):
        if chunk.chunk_id not in translations:
            continue
        if only_pending and chunk.status != BlockStatus.PENDING:
            continue
        chunk.text_vi = translations[chunk.chunk_id]
        chunk.status = BlockStatus.DONE
        save_chunk(workspace, chunk)
        applied += 1
    return applied


def apply_from_file(
    workspace: Path,
    path: Path,
    *,
    only_pending: bool = True,
) -> int:
    data = json.loads(path.read_text(encoding="utf-8"))
    return apply_translations(workspace, data, only_pending=only_pending)
