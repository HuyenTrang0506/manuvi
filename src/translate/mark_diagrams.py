"""Đánh dấu chunk là wiring diagram scrap — giữ ảnh gốc, không dịch text."""

from __future__ import annotations

import re
from pathlib import Path

from src.ingest.diagram_filter import is_diagram_scrap
from src.models import BlockStatus
from src.translate.chunker import load_chunks, save_chunk


def mark_diagram_chunks(workspace: Path) -> int:
    count = 0
    for chunk in load_chunks(workspace):
        if chunk.block_type == "caption":
            continue
        skip = False
        if is_diagram_scrap(chunk.text_en):
            # Fig + title ngắn vẫn dịch
            if re.match(r"^Fig\.?\s*\d+-\d+", chunk.text_en.strip(), re.I) and re.search(
                r"Fig\.?\s*\d+-\d+\s{2,}\w", chunk.text_en, re.I
            ):
                skip = False
            else:
                skip = True
        if skip:
            chunk.status = BlockStatus.DONE
            chunk.text_vi = "[GIỮ NGUYÊN — SƠ ĐỒ MẠCH / WIRING DIAGRAM — XEM ẢNH GỐC]"
            save_chunk(workspace, chunk)
            count += 1
    return count
