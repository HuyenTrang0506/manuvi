"""Phân loại trang theo category — xử lý batch thay vì từng trang."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional

from src.ingest.parser import load_document
from src.models import BlockType
from src.translate.chunker import load_chunks
from src.translate.list_translator import is_bom_block, is_equipment_list

# Category → pipeline handler (mô tả cho KTV/dev)
CATEGORY_HANDLERS: Dict[str, str] = {
    "wiring_diagram": "retranslate-labels + assemble",
    "wiring_diagram_small": "retranslate-labels + assemble",
    "harness_list": "fix-chunks (list_translator)",
    "bom_table": "fix-chunks (list_translator)",
    "panel_labels": "fix-chunks (list_translator)",
    "flowchart": "fix-chunks (flowchart)",
    "prose_spec": "translate-all / engine",
    "section_toc": "engine heading phrases",
    "caption_page": "auto-captions",
    "service_diagnosis": "flowchart + label_translator",
    "mixed": "review thủ công / Excel",
}


@dataclass
class PageCategory:
    pdf_page: int
    manual_ref: Optional[str]
    section: Optional[str]
    category: str
    handler: str
    label_count: int
    figure_count: int
    chunk_count: int
    signals: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pdf_page": self.pdf_page,
            "manual_ref": self.manual_ref,
            "section": self.section,
            "category": self.category,
            "handler": self.handler,
            "label_count": self.label_count,
            "figure_count": self.figure_count,
            "chunk_count": self.chunk_count,
            "signals": self.signals,
        }


def _count_labels(page, document) -> int:
    n = 0
    fig_ids: set[str] = set()
    for b in page.blocks:
        n += len(b.labels or [])
        if b.figure_id:
            fig_ids.add(b.figure_id)
    for fid in fig_ids:
        if fid in document.figures_registry:
            n += len(document.figures_registry[fid].labels)
    return n


def classify_page(page, document, chunks) -> PageCategory:
    page_chunks = [c for c in chunks if c.pdf_page == page.pdf_page]
    labels = _count_labels(page, document)
    figures = sum(1 for b in page.blocks if b.type == BlockType.FIGURE)
    prose = sum(1 for b in page.blocks if b.type in (BlockType.PARAGRAPH, BlockType.WARNING))
    headings = sum(1 for b in page.blocks if b.type == BlockType.HEADING)
    signals: List[str] = []

    ref = page.manual_ref or ""
    sec = page.section or ""

    # Section 43 flowchart
    if ref.startswith("43") or "TROUBLESHOOTING" in sec.upper():
        if len(page_chunks) >= 5:
            cat = "flowchart"
            signals.append(f"{len(page_chunks)} chunks flowchart")
        elif labels >= 3:
            cat = "service_diagnosis"
            signals.append("nhãn chẩn đoán dịch vụ")
        else:
            cat = "prose_spec"
    # Sơ đồ mạch nhiều nhãn
    elif figures >= 1 and labels >= 20:
        cat = "wiring_diagram"
        signals.append(f"{labels} nhãn sơ đồ")
    elif figures >= 1 and labels >= 5:
        cat = "wiring_diagram_small"
        signals.append(f"{labels} nhãn")
    # BOM / danh sách
    elif any(is_bom_block(c.text_en) for c in page_chunks):
        cat = "bom_table"
        signals.append("bảng Name/Part No./Q'ty")
    elif any(is_equipment_list(c.text_en) for c in page_chunks):
        if any("MODE SELECT" in c.text_en or "GAUGE CLUSTER" in c.text_en for c in page_chunks):
            cat = "panel_labels"
            signals.append("nhãn panel instrument")
        else:
            cat = "harness_list"
            signals.append("danh sách cáp/harness")
    # TOC / heading
    elif headings >= 2 or any("CONTENTS" in (c.text_en or "").upper() for c in page_chunks):
        cat = "section_toc"
        signals.append("mục lục / tiêu đề")
    # Ghi chú kỹ thuật
    elif prose >= 1 and any("NOTES" in (c.text_en or "").upper() or "mm" in (c.text_en or "") for c in page_chunks):
        cat = "prose_spec"
        signals.append("ghi chú / quy cách dây")
    # Chủ yếu hình + caption
    elif figures >= 1 and labels < 5:
        cat = "caption_page"
        signals.append("hình + chú thích")
    else:
        cat = "mixed"
        signals.append("không khớp pattern rõ")

    return PageCategory(
        pdf_page=page.pdf_page,
        manual_ref=page.manual_ref,
        section=page.section,
        category=cat,
        handler=CATEGORY_HANDLERS.get(cat, "mixed"),
        label_count=labels,
        figure_count=figures,
        chunk_count=len(page_chunks),
        signals=signals,
    )


def run_page_classification(workspace: Path) -> tuple[List[PageCategory], Path, Path, dict]:
    document = load_document(workspace)
    chunks = load_chunks(workspace)
    pages = [p for p in document.pages if any(b.type != BlockType.FOOTER for b in p.blocks)]
    classified = [classify_page(p, document, chunks) for p in sorted(pages, key=lambda x: x.pdf_page)]

    by_cat: Dict[str, int] = {}
    for c in classified:
        by_cat[c.category] = by_cat.get(c.category, 0) + 1

    summary = {
        "date": str(date.today()),
        "total_pages": len(classified),
        "by_category": by_cat,
        "handlers": CATEGORY_HANDLERS,
    }

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)
    json_path = qa_dir / "page_categories.json"
    md_path = qa_dir / "page_categories.md"

    payload = {"summary": summary, "pages": [c.to_dict() for c in classified]}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Phân loại trang — {workspace.name}",
        "",
        f"**Ngày:** {date.today()} | **Tổng:** {len(classified)} trang",
        "",
        "## Category (xử lý chung)",
        "",
        "| Category | Số trang | Pipeline |",
        "|----------|----------|----------|",
    ]
    for cat, n in sorted(by_cat.items(), key=lambda x: -x[1]):
        lines.append(f"| `{cat}` | {n} | {CATEGORY_HANDLERS.get(cat, '?')} |")

    lines.extend([
        "",
        "## Chi tiết từng trang",
        "",
        "| PDF p. | Ref | Category | Nhãn | Hình | Chunks | Ghi chú |",
        "|--------|-----|----------|------|------|--------|---------|",
    ])
    for c in classified:
        sig = "; ".join(c.signals)
        lines.append(
            f"| {c.pdf_page} | {c.manual_ref or '?'} | `{c.category}` | "
            f"{c.label_count} | {c.figure_count} | {c.chunk_count} | {sig} |"
        )

    lines.extend([
        "",
        "## Hướng dẫn batch",
        "",
        "1. **wiring_diagram** (~20+ nhãn): `retranslate-labels` → kiểm tra bảng SW-xx trong Excel",
        "2. **flowchart** (S43): `fix-chunks` — giữ cấu trúc dòng",
        "3. **harness_list / bom_table / panel_labels**: `fix-chunks` — list_translator",
        "4. **prose_spec**: sửa chunk NOTES trong Excel",
        "5. **caption_page**: `auto-captions`",
        "",
    ])
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return classified, json_path, md_path, summary
