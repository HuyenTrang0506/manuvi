"""Tự kiểm tra output DOCX/PDF sau mỗi lần assemble."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

import fitz

VI_CHARS = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.I)
BOX_CHAR = re.compile(r"[\ufffd\u25a1]")  # replacement / tofu


@dataclass
class OutputCheck:
    name: str
    passed: bool
    message: str


@dataclass
class OutputReport:
    checks: List[OutputCheck] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.passed for c in self.checks)

    def add(self, name: str, ok: bool, msg: str):
        self.checks.append(OutputCheck(name=name, passed=ok, message=msg))


def _check_pdf_vietnamese(pdf_path: Path) -> OutputCheck:
    if not pdf_path.exists():
        return OutputCheck("pdf_exists", False, f"Thiếu file: {pdf_path}")

    doc = fitz.open(str(pdf_path))
    sample = ""
    for i in range(min(5, len(doc))):
        sample += doc[i].get_text()
    doc.close()

    if not sample.strip():
        return OutputCheck("pdf_text", False, "PDF không trích được text")

    vi_count = len(VI_CHARS.findall(sample))
    box_count = len(BOX_CHAR.findall(sample))

    if vi_count < 10:
        return OutputCheck(
            "pdf_vietnamese_font",
            False,
            f"PDF thiếu ký tự tiếng Việt ({vi_count} dấu) — có thể lỗi font",
        )
    if box_count > 0:
        return OutputCheck(
            "pdf_no_tofu",
            False,
            f"PDF có {box_count} ký tự lỗi (□/replacement)",
        )
    return OutputCheck(
        "pdf_vietnamese_font",
        True,
        f"OK — {vi_count} ký tự có dấu trong 5 trang đầu",
    )


def _check_fig_labels(workspace: Path, fig_number: str = "23-3") -> OutputCheck:
    doc = json.loads((workspace / "document_model.json").read_text(encoding="utf-8"))
    entry = None
    for e in doc.get("figures_registry", {}).values():
        if e.get("figure_number") == fig_number:
            entry = e
            break
    if not entry:
        return OutputCheck(f"fig_{fig_number}", False, "Không tìm thấy hình")

    labels = entry.get("labels", [])
    if not labels:
        return OutputCheck(f"fig_{fig_number}", False, "Không có nhãn")

    en_only = 0
    for lb in labels:
        en = lb.get("text_en") or ""
        vi = lb.get("text_vi") or ""
        if lb.get("suspicion"):
            continue
        # Mã đầu nối / màu dây — giữ nguyên là đúng
        if re.match(r"^[A-Z]-\d+[A-Z]?$", en.strip()):
            continue
        if re.match(r"^[WGRBY]{1,3}$", en.strip()):
            continue
        if re.match(r"^[A-Z0-9/':\.\-\(\)]+$", en.strip()) and len(en) < 25:
            continue
        if vi == en or vi.lower() == en.lower():
            en_only += 1

    suspicion = sum(1 for lb in labels if lb.get("suspicion"))
    msg = f"{len(labels)} nhãn | {suspicion} OCR nghi ngờ | {en_only} mô tả EN chưa dịch"
    ok = en_only < max(8, len(labels) * 0.15)
    return OutputCheck(f"fig_{fig_number}_labels", ok, msg)


def _check_docx_exists(docx_path: Path) -> OutputCheck:
    if not docx_path.exists():
        return OutputCheck("docx_exists", False, f"Thiếu: {docx_path}")
    size = docx_path.stat().st_size
    if size < 100_000:
        return OutputCheck("docx_size", False, f"DOCX quá nhỏ ({size} bytes)")
    return OutputCheck("docx_exists", True, f"OK — {size / 1_048_576:.1f} MB")


def run_output_tests(workspace: Path, doc_id: str) -> OutputReport:
    report = OutputReport()
    out = Path("output") / doc_id / "vi"
    pdf = out / "section_electrical_vi.pdf"
    docx = out / "section_electrical_vi.docx"

    for check_fn in [
        lambda: _check_docx_exists(docx),
        lambda: _check_pdf_vietnamese(pdf),
        lambda: _check_fig_labels(workspace, "23-3"),
    ]:
        c = check_fn()
        report.add(c.name, c.passed, c.message)

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)
    payload = {
        "passed": report.passed,
        "checks": [
            {"name": c.name, "passed": c.passed, "message": c.message}
            for c in report.checks
        ],
    }
    (qa_dir / "output_test.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return report
