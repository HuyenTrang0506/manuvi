"""Kiểm tra chất lượng TỪNG TRANG — 64 trang section 23+43."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz

from src.ingest.parser import load_document
from src.models import BlockType
from src.qa.label_validator import is_mixed_translation
from src.qa.page_sampler import (
    FIG_REF,
    PART_NUM,
    PageFinding,
    _check_glued_text,
    _chunks_for_page,
    _collect_refs,
    _line_count,
    review_page,
)
from src.translate.chunker import load_chunks

VI_CHARS = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìííĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.I)
SEC_NUM = re.compile(r"\b23\.\d+(?:\.\d+)?\b")
MANUAL_HDR = re.compile(r"──\s*(\d{2}-\d{1,3}|PDF-\d+)\s*──")
BOX_CHAR = re.compile(r"[\ufffd\u25a1]")
VENDOR_NOISE = re.compile(r"1PAMYAZAKI|PAMYAZAKI|7122-\d+", re.I)


@dataclass
class PageAudit:
    pdf_page: int
    manual_ref: Optional[str]
    section: Optional[str]
    status: str  # pass | warn | fail
    chunk_total: int
    chunk_done: int
    label_total: int
    label_suspicion: int
    label_untranslated: int
    figures: int
    figures_missing_image: int
    pdf_section_found: bool
    pdf_vi_chars: int
    findings: List[PageFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pdf_page": self.pdf_page,
            "manual_ref": self.manual_ref,
            "section": self.section,
            "status": self.status,
            "chunk_total": self.chunk_total,
            "chunk_done": self.chunk_done,
            "label_total": self.label_total,
            "label_suspicion": self.label_suspicion,
            "label_untranslated": self.label_untranslated,
            "figures": self.figures,
            "figures_missing_image": self.figures_missing_image,
            "pdf_section_found": self.pdf_section_found,
            "pdf_vi_chars": self.pdf_vi_chars,
            "findings": [{"severity": f.severity, "message": f.message} for f in self.findings],
        }


def _status_from_findings(findings: List[PageFinding]) -> str:
    if any(f.severity == "bug" for f in findings):
        return "fail"
    if any(f.severity == "risk" for f in findings):
        return "warn"
    if any(f.severity == "nit" for f in findings):
        return "warn"
    return "pass"


def _check_toc_blob_duplication(page_chunks) -> List[PageFinding]:
    findings: List[PageFinding] = []
    for c in page_chunks:
        if not c.text_vi:
            continue
        en_nums = set(SEC_NUM.findall(c.text_en))
        vi_nums = set(SEC_NUM.findall(c.text_vi))
        if len(vi_nums) > len(en_nums) + 1:
            findings.append(
                PageFinding(
                    "bug",
                    f"{c.chunk_id}: VI gộp nhiều mục TOC ({len(vi_nums)} mục trong 1 chunk)",
                )
            )
        if c.text_vi.count("\n\n") >= 3 and len(c.text_en) < 200:
            findings.append(
                PageFinding(
                    "bug",
                    f"{c.chunk_id}: VI có nhiều đoạn — có thể remap sai",
                )
            )
    return findings


def _check_labels_on_page(page, document, workspace: Path) -> Tuple[int, int, int, List[PageFinding]]:
    findings: List[PageFinding] = []
    total = suspicion = untranslated = 0
    seen: set[str] = set()

    fig_ids = {b.figure_id for b in page.blocks if b.figure_id}
    labels = []
    for b in page.blocks:
        labels.extend(b.labels or [])
    for fid in fig_ids:
        if fid in document.figures_registry:
            labels.extend(document.figures_registry[fid].labels)

    for lb in labels:
        key = lb.text_en.lower()
        if key in seen:
            continue
        seen.add(key)
        total += 1
        if getattr(lb, "suspicion", False):
            suspicion += 1
        en, vi = lb.text_en.strip(), (lb.text_vi or "").strip()
        if VENDOR_NOISE.search(en):
            continue
        if vi and is_mixed_translation(vi):
            if getattr(lb, "suspicion", False) or vi.startswith("[⚠ OCR]"):
                findings.append(PageFinding("risk", f"Nhãn OCR/review: {en[:45]}"))
            else:
                findings.append(PageFinding("bug", f"Nhãn EN/VI lẫn: {en[:45]} → {vi[:45]}"))
            continue
        if not vi or vi == en:
            if re.match(r"^[A-Z]-\d+", en) or re.match(r"^[WGRBY]{1,3}$", en):
                continue
            if re.match(r"^(RXD|TXD|RXDTX|TXDRX)", en, re.I):
                continue
            if len(en) > 8 and re.search(r"[A-Za-z]{4,}", en):
                untranslated += 1
                if not getattr(lb, "suspicion", False):
                    findings.append(PageFinding("nit", f"Nhãn chưa dịch: {en[:50]}"))

    if suspicion > 8:
        findings.append(PageFinding("risk", f"{suspicion} nhãn OCR nghi ngờ trên trang"))
    return total, suspicion, untranslated, findings


def _check_figures(page, workspace: Path) -> Tuple[int, int, List[PageFinding]]:
    findings: List[PageFinding] = []
    figs = [b for b in page.blocks if b.type == BlockType.FIGURE]
    missing = 0
    for b in figs:
        if not b.image_path:
            missing += 1
            findings.append(PageFinding("risk", f"Hình {b.figure_id or b.block_id} thiếu image_path"))
            continue
        p = workspace / b.image_path
        if not p.exists():
            p2 = workspace.parent / b.image_path
            if not p2.exists():
                missing += 1
                findings.append(PageFinding("bug", f"Ảnh không tồn tại: {b.image_path}"))
    return len(figs), missing, findings


def _index_pdf_sections(pdf_path: Path) -> Dict[str, List[dict]]:
    """Map manual_ref → danh sách {page_idx, vi_chars, has_tofu, text_len}."""
    result: Dict[str, List[dict]] = {}
    if not pdf_path.exists():
        return result
    doc = fitz.open(str(pdf_path))
    for i in range(len(doc)):
        text = doc[i].get_text()
        refs = MANUAL_HDR.findall(text)
        vi_n = len(VI_CHARS.findall(text))
        tofu = len(BOX_CHAR.findall(text))
        for ref in refs:
            result.setdefault(ref, []).append(
                {"page_idx": i, "vi_chars": vi_n, "tofu": tofu, "text_len": len(text)}
            )
    doc.close()
    return result


def audit_page(
    page,
    document,
    chunks,
    workspace: Path,
    pdf_sections: Dict[str, List[dict]],
) -> PageAudit:
    base = review_page(page, chunks)
    findings = list(base.findings)
    page_chunks = _chunks_for_page(chunks, page.pdf_page)

    findings.extend(_check_toc_blob_duplication(page_chunks))

    label_total, label_susp, label_untr, label_findings = _check_labels_on_page(
        page, document, workspace
    )
    findings.extend(label_findings)

    fig_n, fig_miss, fig_findings = _check_figures(page, workspace)
    findings.extend(fig_findings)

    ref = page.manual_ref or f"PDF-{page.pdf_page}"
    pdf_hits = pdf_sections.get(ref, [])
    pdf_found = len(pdf_hits) > 0
    pdf_vi = sum(h["vi_chars"] for h in pdf_hits)

    if not pdf_found:
        findings.append(PageFinding("risk", f"Không tìm thấy marker PDF '── {ref} ──'"))
    else:
        for h in pdf_hits:
            if h["tofu"] > 0:
                findings.append(PageFinding("bug", f"PDF trang {h['page_idx']+1} có ký tự lỗi font"))
        # Trang nhiều hình/ nhãn — ít prose trong PDF là bình thường
        prose_chunks = [c for c in page_chunks if c.block_type in ("paragraph", "list", "heading")]
        image_heavy = (label_total >= 20 and len(prose_chunks) <= 2) or fig_n >= 1
        if prose_chunks and pdf_vi < 1 and not image_heavy:
            findings.append(PageFinding("risk", f"PDF thiếu ký tự tiếng Việt ({pdf_vi} dấu)"))

    status = _status_from_findings(findings)
    return PageAudit(
        pdf_page=page.pdf_page,
        manual_ref=page.manual_ref,
        section=page.section,
        status=status,
        chunk_total=base.chunk_count,
        chunk_done=base.translated_count,
        label_total=label_total,
        label_suspicion=label_susp,
        label_untranslated=label_untr,
        figures=fig_n,
        figures_missing_image=fig_miss,
        pdf_section_found=pdf_found,
        pdf_vi_chars=pdf_vi,
        findings=findings,
    )


def run_page_audit(
    workspace: Path,
    doc_id: str,
    pdf_path: Optional[Path] = None,
) -> Tuple[List[PageAudit], Path, Path, dict]:
    document = load_document(workspace)
    chunks = load_chunks(workspace)

    if pdf_path is None:
        pdf_path = Path("output") / doc_id / "vi" / "section_electrical_vi.pdf"

    pdf_sections = _index_pdf_sections(pdf_path)
    pages = [p for p in document.pages if any(b.type != BlockType.FOOTER for b in p.blocks)]
    audits = [
        audit_page(p, document, chunks, workspace, pdf_sections)
        for p in sorted(pages, key=lambda x: x.pdf_page)
    ]

    summary = {
        "date": str(date.today()),
        "total_pages": len(audits),
        "pass": sum(1 for a in audits if a.status == "pass"),
        "warn": sum(1 for a in audits if a.status == "warn"),
        "fail": sum(1 for a in audits if a.status == "fail"),
        "pdf_path": str(pdf_path),
        "pdf_exists": pdf_path.exists(),
    }

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)
    json_path = qa_dir / "page_audit.json"
    md_path = qa_dir / "page_audit.md"

    payload = {"summary": summary, "pages": [a.to_dict() for a in audits]}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Page audit — {workspace.name}",
        "",
        f"**Ngày:** {date.today()} | **Tổng:** {summary['total_pages']} trang",
        f"✅ {summary['pass']} | ⚠️ {summary['warn']} | ❌ {summary['fail']}",
        "",
        "| PDF p. | Ref | Status | Chunks | Labels | OCR? | PDF VI |",
        "|--------|-----|--------|--------|--------|------|--------|",
    ]
    for a in audits:
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[a.status]
        lines.append(
            f"| {a.pdf_page} | {a.manual_ref or '?'} | {icon} | "
            f"{a.chunk_done}/{a.chunk_total} | {a.label_total} | {a.label_suspicion} | {a.pdf_vi_chars} |"
        )

    lines.append("")
    lines.append("## Chi tiết trang có vấn đề")
    lines.append("")
    for a in audits:
        if a.status == "pass":
            continue
        icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}[a.status]
        lines.append(f"### {icon} PDF p.{a.pdf_page} — ref {a.manual_ref}")
        for f in a.findings:
            emoji = {"bug": "🔴", "risk": "🟡", "nit": "🔵"}.get(f.severity, "•")
            lines.append(f"- {emoji} {f.message}")
        lines.append("")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return audits, json_path, md_path, summary
