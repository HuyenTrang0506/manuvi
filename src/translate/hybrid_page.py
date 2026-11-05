"""Hybrid page extract: PyMuPDF text + (tuỳ chọn) VLM cấu trúc bảng/sơ đồ.

Mục tiêu: trang dense (43-x bảng chẩn đoán, flowchart) lấy cấu trúc tốt hơn
chỉ dựa vào thứ tự span PDF. Embedding ColPali dùng cho tra cứu sau —
không thay bước dịch.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import fitz

from src.ingest.text_structure import normalize_pdf_text
from src.translate.flowchart import translate_flowchart_text
from src.translate.section43_map import lookup_section43
from src.models import Glossary

HARD_PAGE = re.compile(r"^43-\d+$")


@dataclass
class HybridBlock:
    kind: str  # prose | table_row | flowchart_node | heading
    text_en: str
    text_vi: str = ""
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class HybridPageResult:
    manual_ref: str
    pdf_page: int
    source: str  # pymupdf | vlm | map
    blocks: List[HybridBlock]
    notes: List[str] = field(default_factory=list)


def render_page_png(pdf_path: Path, pdf_page: int, out_png: Path, dpi: int = 180) -> Path:
    doc = fitz.open(pdf_path)
    page = doc[pdf_page - 1]
    pix = page.get_pixmap(matrix=fitz.Matrix(dpi / 72, dpi / 72), alpha=False)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(out_png))
    doc.close()
    return out_png


def extract_pymupdf_blocks(pdf_path: Path, pdf_page: int) -> List[str]:
    doc = fitz.open(pdf_path)
    page = doc[pdf_page - 1]
    text = page.get_text("text")
    doc.close()
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return lines


def _translate_line(en: str, glossary: Glossary) -> str:
    mapped = lookup_section43(en)
    if mapped:
        return mapped
    return translate_flowchart_text(en, glossary) or en


def build_from_pymupdf(
    manual_ref: str,
    pdf_page: int,
    lines: List[str],
    glossary: Glossary,
) -> HybridPageResult:
    blocks: List[HybridBlock] = []
    for ln in lines:
        kind = "heading" if re.match(r"^\d{2}\.", ln) else "prose"
        if len(ln.split()) <= 12 and any(
            k in ln.lower()
            for k in ("fault on", "replace ", "sensor", "valve", "switch", "motion")
        ):
            kind = "flowchart_node"
        blocks.append(
            HybridBlock(kind=kind, text_en=ln, text_vi=_translate_line(ln, glossary))
        )
    return HybridPageResult(
        manual_ref=manual_ref,
        pdf_page=pdf_page,
        source="pymupdf+map",
        blocks=blocks,
        notes=["VLM chưa bật — dùng map/flowchart. Set OPENAI_API_KEY để bật."],
    )


def try_vlm_structure(image_path: Path) -> Optional[List[Dict[str, Any]]]:
    """Gọi VLM nếu có API key. Trả list {en, kind} hoặc None."""
    api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    # Hook — triển khai đầy đủ khi có key; giữ stub an toàn
    return None


def hybrid_extract_page(
    pdf_path: Path,
    *,
    manual_ref: str,
    pdf_page: int,
    workspace: Path,
    glossary: Optional[Glossary] = None,
) -> HybridPageResult:
    glossary = glossary or Glossary(terms={})
    probe = workspace / "qa" / "vision_probe"
    png = probe / f"page_{pdf_page}_hybrid.png"
    render_page_png(pdf_path, pdf_page, png)

    vlm = try_vlm_structure(png)
    if vlm:
        blocks = [
            HybridBlock(
                kind=row.get("kind", "prose"),
                text_en=row.get("en", ""),
                text_vi=_translate_line(row.get("en", ""), glossary),
                meta=row,
            )
            for row in vlm
            if row.get("en")
        ]
        return HybridPageResult(
            manual_ref=manual_ref,
            pdf_page=pdf_page,
            source="vlm+map",
            blocks=blocks,
            notes=["Cấu trúc từ VLM; dịch bằng map/flowchart."],
        )

    lines = extract_pymupdf_blocks(pdf_path, pdf_page)
    return build_from_pymupdf(manual_ref, pdf_page, lines, glossary)


def write_hybrid_report(workspace: Path, results: List[HybridPageResult]) -> Path:
    qa = workspace / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    path = qa / "hybrid_page_probe.json"
    payload = [
        {
            "manual_ref": r.manual_ref,
            "pdf_page": r.pdf_page,
            "source": r.source,
            "notes": r.notes,
            "blocks": [
                {"kind": b.kind, "en": b.text_en, "vi": b.text_vi} for b in b_list
            ],
        }
        for r in results
        for b_list in [r.blocks]
    ]
    # fix awkward comprehension
    payload = []
    for r in results:
        payload.append(
            {
                "manual_ref": r.manual_ref,
                "pdf_page": r.pdf_page,
                "source": r.source,
                "notes": r.notes,
                "n_blocks": len(r.blocks),
                "sample": [
                    {"kind": b.kind, "en": b.text_en[:80], "vi": b.text_vi[:80]}
                    for b in r.blocks[:12]
                ],
            }
        )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
