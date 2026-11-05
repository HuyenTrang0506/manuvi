"""Lấy mẫu ngẫu nhiên trang PDF và review chất lượng output."""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional

from src.ingest.parser import load_document
from src.models import BlockType
from src.translate.chunker import load_chunks

GLUED_PATTERN = re.compile(r"\d\.\d+(?:\.\d+)*[A-Z]{2,}")
FIG_REF = re.compile(r"Fig\.?\s*\d+-\d+", re.I)
MANUAL_REF = re.compile(r"\d{2}-\d{1,3}")
PART_NUM = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")


@dataclass
class PageFinding:
    severity: str  # bug | risk | nit
    message: str


@dataclass
class PageReview:
    pdf_page: int
    manual_ref: Optional[str]
    block_count: int
    chunk_count: int
    translated_count: int
    structure_ok: bool
    findings: List[PageFinding] = field(default_factory=list)

    @property
    def status(self) -> str:
        if any(f.severity == "bug" for f in self.findings):
            return "fail"
        if any(f.severity == "risk" for f in self.findings):
            return "warn"
        return "pass"


def _chunks_for_page(chunks, pdf_page: int):
    return [c for c in chunks if c.pdf_page == pdf_page]


def _check_glued_text(text: str) -> bool:
    """Phát hiện số mục dính chữ hoa (23.2.5UPPER) — không xóa khoảng trắng."""
    return bool(re.search(r"\d\.\d+(?:\.\d+)*[A-Z]{2,}", text))


def _line_count(text: str) -> int:
    return len([ln for ln in text.split("\n") if ln.strip()])


def _collect_refs(text: str) -> set[str]:
    refs = set()
    for pat in [FIG_REF, PART_NUM, MANUAL_REF]:
        refs.update(pat.findall(text))
    return refs


def review_page(page, chunks) -> PageReview:
    page_chunks = _chunks_for_page(chunks, page.pdf_page)
    done = sum(1 for c in page_chunks if c.status.value == "done" and c.text_vi)
    blocks = [b for b in page.blocks if b.type != BlockType.FOOTER]
    findings: List[PageFinding] = []

    # Glued tokens in source blocks
    for b in blocks:
        if b.text_en and _check_glued_text(b.text_en):
            findings.append(PageFinding("bug", f"Chữ dính liền trong block {b.block_id}: {b.text_en[:60]}"))

    # Translation coverage
    prose_chunks = [
        c for c in page_chunks
        if c.block_type not in ("figure", "caption", "footer", "page_ref")
    ]
    if prose_chunks:
        ratio = done / len(prose_chunks)
        if ratio < 0.5:
            findings.append(
                PageFinding("risk", f"Chỉ {done}/{len(prose_chunks)} chunk prose đã dịch ({ratio:.0%})")
            )
        elif ratio < 1.0:
            findings.append(
                PageFinding("nit", f"{len(prose_chunks) - done} chunk prose còn pending")
            )

    # Structure: line count EN vs VI for done chunks
    structure_ok = True
    for c in page_chunks:
        if not c.text_vi or c.text_vi.startswith("[GIỮ NGUYÊN"):
            continue
        en_lines = _line_count(c.text_en)
        vi_lines = _line_count(c.text_vi)
        if en_lines >= 3 and abs(en_lines - vi_lines) > max(2, en_lines * 0.4):
            structure_ok = False
            findings.append(
                PageFinding(
                    "risk",
                    f"{c.chunk_id}: cấu trúc dòng lệch EN={en_lines} VI={vi_lines}",
                )
            )

    # Protected refs in done chunks
    for c in page_chunks:
        if not c.text_vi:
            continue
        en_refs = _collect_refs(c.text_en)
        for ref in en_refs:
            if PART_NUM.fullmatch(ref) and ref not in c.text_vi:
                findings.append(PageFinding("bug", f"{c.chunk_id}: mất mã phụ tùng {ref}"))
            elif FIG_REF.match(ref):
                if c.block_type == "caption":
                    continue
                num = re.search(r"\d+-\d+", ref)
                if num and num.group(0) not in c.text_vi:
                    findings.append(PageFinding("risk", f"{c.chunk_id}: thiếu ref hình {num.group(0)}"))

    # mm² normalization — chỉ cảnh báo nếu bản dịch chưa chuẩn
    for c in page_chunks:
        if c.text_vi and "mm 2" in c.text_vi:
            findings.append(PageFinding("nit", f"{c.chunk_id}: 'mm 2' chưa chuẩn thành mm²"))

    # Missing images
    for b in blocks:
        if b.type == BlockType.FIGURE and b.image_path:
            # path relative to workspace
            pass  # checked at assemble time

    return PageReview(
        pdf_page=page.pdf_page,
        manual_ref=page.manual_ref,
        block_count=len(blocks),
        chunk_count=len(page_chunks),
        translated_count=done,
        structure_ok=structure_ok,
        findings=findings,
    )


def run_sample_review(
    workspace: Path,
    count: int = 8,
    seed: int = 42,
) -> tuple[list[PageReview], Path, Path]:
    document = load_document(workspace)
    chunks = load_chunks(workspace)
    pages = [p for p in document.pages if any(b.type != BlockType.FOOTER for b in p.blocks)]

    rng = random.Random(seed)
    sample = rng.sample(pages, min(count, len(pages)))
    reviews = [review_page(p, chunks) for p in sorted(sample, key=lambda x: x.pdf_page)]

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)

    json_path = qa_dir / "sample_review.json"
    md_path = qa_dir / "sample_review.md"

    payload = {
        "date": str(date.today()),
        "seed": seed,
        "sample_count": len(reviews),
        "pages": [
            {
                "pdf_page": r.pdf_page,
                "manual_ref": r.manual_ref,
                "status": r.status,
                "block_count": r.block_count,
                "chunk_count": r.chunk_count,
                "translated": r.translated_count,
                "structure_ok": r.structure_ok,
                "findings": [{"severity": f.severity, "message": f.message} for f in r.findings],
            }
            for r in reviews
        ],
    }
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Sample review — {workspace.name}",
        "",
        f"**Ngày:** {date.today()} | **Seed:** {seed} | **Trang mẫu:** {len(reviews)}",
        "",
    ]
    for r in reviews:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[r.status]
        lines.append(f"## {icon} PDF p.{r.pdf_page} — ref {r.manual_ref or '?'}")
        lines.append("")
        lines.append(
            f"- Blocks: {r.block_count} | Chunks: {r.chunk_count} | "
            f"Đã dịch: {r.translated_count} | Cấu trúc: {'OK' if r.structure_ok else 'LỆCH'}"
        )
        if r.findings:
            lines.append("")
            for f in r.findings:
                emoji = {"bug": "🔴", "risk": "🟡", "nit": "🔵"}.get(f.severity, "•")
                lines.append(f"- {emoji} {f.message}")
        else:
            lines.append("- Không có vấn đề phát hiện")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return reviews, json_path, md_path
