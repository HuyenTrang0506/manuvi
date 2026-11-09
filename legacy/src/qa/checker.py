from __future__ import annotations

import json
import re
from pathlib import Path

from src.models import BlockStatus, QAReport, QAIssue
from src.translate.chunker import load_chunks

PART_NUMBER_PATTERN = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
FIGURE_PATTERN = re.compile(r"Fig\.?\s*\d+-\d+", re.I)


def run_qa(workspace: Path, doc_id: str) -> QAReport:
    chunks = load_chunks(workspace)
    issues: list[QAIssue] = []

    translated = [c for c in chunks if c.status == BlockStatus.DONE and c.text_vi]
    pending = [c for c in chunks if c.status == BlockStatus.PENDING]

    for chunk in chunks:
        if not chunk.text_vi:
            if chunk.status == BlockStatus.PENDING:
                continue
            issues.append(
                QAIssue(
                    chunk_id=chunk.chunk_id,
                    issue_type="missing_translation",
                    message="Chunk marked done nhưng thiếu text_vi",
                    severity="error",
                )
            )
            continue

        vi = chunk.text_vi
        for token in chunk.protected_tokens:
            if token not in vi and token.replace("Fig.", "Fig.") in chunk.text_en:
                # Part numbers must be preserved exactly
                if PART_NUMBER_PATTERN.fullmatch(token):
                    issues.append(
                        QAIssue(
                            chunk_id=chunk.chunk_id,
                            issue_type="protected_violation",
                            message=f"Part number bị thay đổi hoặc mất: {token}",
                            severity="error",
                        )
                    )

        for token in chunk.protected_tokens:
            if PART_NUMBER_PATTERN.fullmatch(token) and token not in vi:
                issues.append(
                    QAIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="protected_violation",
                        message=f"Thiếu mã phụ tùng: {token}",
                        severity="error",
                    )
                )

        for m in FIGURE_PATTERN.finditer(chunk.text_en):
            fig = m.group(0)
            num = re.search(r"\d+-\d+", fig)
            if num and num.group(0) not in vi:
                issues.append(
                    QAIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="figure_ref_drift",
                        message=f"Số hình gốc {num.group(0)} không có trong bản dịch",
                        severity="warning",
                    )
                )

        # English leftovers (simple heuristic)
        en_terms = re.findall(r"\b(?:WARNING|CAUTION|DANGER|harness|connector|solenoid)\b", chunk.text_en, re.I)
        for term in set(en_terms):
            if term.upper() in ("WARNING", "CAUTION", "DANGER"):
                continue
            if re.search(rf"\b{re.escape(term)}\b", vi, re.I):
                issues.append(
                    QAIssue(
                        chunk_id=chunk.chunk_id,
                        issue_type="untranslated_term",
                        message=f"Có thể còn sót thuật ngữ EN: {term}",
                        severity="warning",
                    )
                )

    report = QAReport(
        doc_id=doc_id,
        total_chunks=len(chunks),
        translated=len(translated),
        pending=len(pending),
        issues=issues,
    )

    qa_dir = workspace / "qa"
    qa_dir.mkdir(exist_ok=True)
    (qa_dir / "report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")

    review_queue = [
        {
            "chunk_id": c.chunk_id,
            "manual_ref": c.manual_ref,
            "pdf_page": c.pdf_page,
            "requires_review": c.requires_review,
            "reason": "warning/safety" if c.requires_review else "qa_issue",
        }
        for c in chunks
        if c.requires_review
        or any(i.chunk_id == c.chunk_id for i in issues if i.severity == "error")
    ]
    (qa_dir / "review_queue.json").write_text(json.dumps(review_queue, indent=2), encoding="utf-8")

    return report
