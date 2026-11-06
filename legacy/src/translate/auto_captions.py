"""Tự động dịch caption hình từ figures_registry."""

from __future__ import annotations

import re
from pathlib import Path

from src.ingest.parser import load_document
from src.models import BlockStatus
from src.translate.chunker import load_chunks, save_chunk

FIG_NUM = re.compile(r"Fig\.?\s*(\d+-\d+)", re.I)

CAPTION_MAP = {
    "upper electric assy": "lắp ráp điện phần trên",
    "instrument panel harness": "bộ dây bảng điều khiển",
    "controller assy": "lắp ráp bộ điều khiển",
    "e/g room light harness": "bộ dây đèn phòng E/G",
    "battery": "ắc quy",
    "starter": "máy khởi động",
    "alternator": "máy phát điện",
    "wiring diagram": "sơ đồ mạch điện",
    "electrical circuit diagram": "sơ đồ mạch điện",
    "travel": "di chuyển",
    "swing": "xoay",
    "boom": "cần",
    "arm": "thanh",
}


def _translate_caption_title(title: str) -> str:
    low = title.lower().strip()
    for en, vi in CAPTION_MAP.items():
        if en in low:
            return vi
    return title


def auto_figure_captions(workspace: Path) -> int:
    doc = load_document(workspace)
    count = 0
    for chunk in load_chunks(workspace):
        if chunk.status != BlockStatus.PENDING:
            continue
        m = FIG_NUM.search(chunk.text_en)
        if not m:
            continue
        fig_num = m.group(1)
        fig_id = f"fig-{fig_num}"
        entry = doc.figures_registry.get(fig_id)
        title_en = ""
        if entry:
            title_en = entry.caption_en or ""
            # Lấy từ text chunk nếu có sau số fig
            rest = FIG_NUM.sub("", chunk.text_en).strip(" .:-\n")
            if len(rest) > 3:
                title_en = rest

        if title_en:
            vi_title = _translate_caption_title(title_en)
            chunk.text_vi = f"Hình {fig_num}. {vi_title}"
            if entry:
                entry.caption_vi = vi_title
        else:
            chunk.text_vi = f"Hình {fig_num}."
        chunk.status = BlockStatus.DONE
        save_chunk(workspace, chunk)
        count += 1

    # persist updated captions
    from src.ingest.parser import save_document

    save_document(doc, workspace)
    return count
