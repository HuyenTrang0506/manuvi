"""Xuất PDF — ưu tiên PyMuPDF builder; fallback docx2pdf (Word) nếu được yêu cầu."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.assemble.pdf_builder import build_pdf


def export_pdf_from_docx(docx_path: Path, pdf_path: Path) -> Path:
    """Chuyển DOCX → PDF qua Microsoft Word (macOS). Có thể timeout với file lớn."""
    import platform

    if platform.system() != "Darwin":
        raise RuntimeError("docx2pdf chỉ hỗ trợ macOS + Microsoft Word")
    try:
        from docx2pdf import convert
    except ImportError:
        raise RuntimeError("Thiếu docx2pdf. Chạy: pip install docx2pdf")

    convert(str(docx_path.resolve()), str(pdf_path.resolve()))
    if not pdf_path.exists():
        raise RuntimeError("docx2pdf không tạo được PDF")
    return pdf_path


def export_pdf(
    workspace: Path,
    docx_path: Path,
    pdf_path: Path,
    title: str,
    source_pdf_path: Optional[Path] = None,
) -> Path:
    """Xuất PDF từ document model (PyMuPDF)."""
    return build_pdf(workspace, pdf_path, title, source_pdf_path=source_pdf_path)
