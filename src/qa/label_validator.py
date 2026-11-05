"""Kiểm tra nhãn sơ đồ — phát hiện EN/VI lẫn lộn, chưa dịch."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import List, Optional, Tuple

from src.ingest.parser import load_document

VI_CHARS = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)
EN_WORD = re.compile(r"\b[A-Za-z]{4,}\b")
# Từ tiếng Việt không dấu — không coi là tiếng Anh
VI_ASCII = {
    "xanh", "trai", "phai", "trung", "binh", "thuong", "den", "cong", "tat", "cam",
    "bien", "ro", "le", "bo", "day", "dien", "cap", "van", "bom", "quay", "toa",
    "phanh", "sau", "truoc", "tren", "duoi", "phong", "buong", "lai", "cung",
}
# Mã thuần — giữ nguyên EN, không báo "chưa dịch"
PURE_LABEL = re.compile(
    r"^(SW|SV|R|M|L|E|C|CN|SE|PSV)-\d+[A-Z]?$|^[WGRBY]{1,3}$|^\d{1,4}$|^[A-Z]{2,4}-\d+$",
    re.I,
)
CODE_PREFIX = re.compile(r"^([A-Z]{1,4}-\d+[A-Z]?)\s*:\s*", re.I)
EQUIP_SEMANTIC = re.compile(
    r"^(C|D|E|L|M|R|SE|SV|PSV|SW|CN)-\d+(?:\.\d+)?[A-Z]?\s*[—-]\s*",
    re.I,
)
WIRE_COLOR_VI = re.compile(r"^[A-Z]{1,3}\s*\([^)]*[àáảãạăâêôơưđ]", re.I)


def _desc_part(text: str) -> str:
    """Phần mô tả sau mã SW-7: / L-1:"""
    m = CODE_PREFIX.match(text.strip())
    return m.group(0).split(":", 1)[-1].strip() if m else text.strip()


def is_mixed_translation(text_vi: str) -> bool:
    """
    Dịch lẫn = có tiếng Việt + còn cụm kỹ thuật tiếng Anh.
    Dùng mix_detector (danh sách EN rộng hơn detector cũ).
    """
    if not text_vi:
        return False
    stripped = text_vi.strip()
    if WIRE_COLOR_VI.match(_desc_part(stripped)):
        return False
    # Cho phép "C-1 — Bộ điều khiển…" nếu phần mô tả không còn EN residual
    if EQUIP_SEMANTIC.match(stripped):
        desc = re.split(r"[—-]", stripped, maxsplit=1)[-1].strip()
        from src.qa.mix_detector import is_mixed_vi_en

        return is_mixed_vi_en(desc)
    from src.qa.mix_detector import is_mixed_vi_en

    return is_mixed_vi_en(_desc_part(stripped))


def is_untranslated(text_en: str, text_vi: Optional[str]) -> bool:
    en = text_en.strip()
    if PURE_LABEL.match(en):
        # Mã thuần OK nếu đã có semantic (C-1 — bộ điều khiển…)
        if text_vi and "—" in text_vi:
            return False
        if text_vi and text_vi.strip() != en:
            return False
        return False  # SW-7, E-14 giữ mã là chuẩn khi chưa có trong danh mục
    if not text_vi:
        return True
    if text_vi.strip().lower() == text_en.strip().lower():
        return True
    body_en = _desc_part(text_en).lower()
    body_vi = _desc_part(text_vi).lower()
    if body_en and body_en == body_vi:
        return True
    return False


@dataclass
class LabelFinding:
    text_en: str
    text_vi: str
    issue: str  # mixed | untranslated | ocr
    figure_id: Optional[str] = None
    pdf_page: Optional[int] = None


@dataclass
class LabelAudit:
    total: int
    mixed: int
    untranslated: int
    ocr: int
    findings: List[LabelFinding] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": self.total,
            "mixed": self.mixed,
            "untranslated": self.untranslated,
            "ocr": self.ocr,
            "findings": [
                {
                    "text_en": f.text_en,
                    "text_vi": f.text_vi,
                    "issue": f.issue,
                    "figure_id": f.figure_id,
                    "pdf_page": f.pdf_page,
                }
                for f in self.findings
            ],
        }


def run_label_audit(workspace: Path) -> Tuple[LabelAudit, Path, Path]:
    document = load_document(workspace)
    seen: set[str] = set()
    findings: List[LabelFinding] = []
    total = mixed = untrans = ocr = 0

    def check(lb, figure_id=None, pdf_page=None):
        nonlocal total, mixed, untrans, ocr
        en = (lb.text_en or "").strip()
        if not en:
            return
        key = en.lower()
        if key in seen:
            return
        seen.add(key)
        total += 1
        vi = lb.text_vi or ""
        if getattr(lb, "suspicion", False):
            ocr += 1
            if is_mixed_translation(vi):
                mixed += 1
                findings.append(LabelFinding(en, vi, "mixed+ocr", figure_id, pdf_page))
            return
        if is_mixed_translation(vi):
            mixed += 1
            findings.append(LabelFinding(en, vi, "mixed", figure_id, pdf_page))
        elif is_untranslated(en, vi):
            untrans += 1
            findings.append(LabelFinding(en, vi or "—", "untranslated", figure_id, pdf_page))

    fig_page: dict[str, int] = {}
    for page in document.pages:
        for b in page.blocks:
            if b.figure_id:
                fig_page[b.figure_id] = page.pdf_page
            for lb in b.labels or []:
                check(lb, b.figure_id, page.pdf_page)

    for fid, entry in document.figures_registry.items():
        pg = fig_page.get(fid) or (entry.pdf_pages[0] if entry.pdf_pages else None)
        for lb in entry.labels:
            check(lb, fid, pg)

    audit = LabelAudit(
        total=total,
        mixed=mixed,
        untranslated=untrans,
        ocr=ocr,
        findings=sorted(findings, key=lambda f: (f.issue, f.text_en)),
    )

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)
    json_path = qa_dir / "label_audit.json"
    md_path = qa_dir / "label_audit.md"

    payload = {"date": str(date.today()), **audit.to_dict()}
    json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        f"# Label audit — {workspace.name}",
        "",
        f"**Ngày:** {date.today()} | **Tổng nhãn:** {total}",
        f"🔴 EN/VI lẫn: **{mixed}** | 🔵 Chưa dịch: **{untrans}** | 🟠 OCR: **{ocr}**",
        "",
    ]
    if not findings:
        lines.append("✅ Không phát hiện nhãn lỗi.")
    else:
        lines.append("| Issue | EN | VI |")
        lines.append("|-------|----|----|")
        for f in findings[:200]:
            en = f.text_en.replace("|", "\\|")[:45]
            vi = (f.text_vi or "").replace("|", "\\|")[:45]
            lines.append(f"| {f.issue} | {en} | {vi} |")
        if len(findings) > 200:
            lines.append(f"| … | +{len(findings)-200} | … |")

    md_path.write_text("\n".join(lines), encoding="utf-8")
    return audit, json_path, md_path
