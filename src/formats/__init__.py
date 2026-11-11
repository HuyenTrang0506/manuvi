"""Document format adapters: Markdown, DOCX, PPTX (PDF keeps its own intake/render path)."""
from __future__ import annotations

from pathlib import Path

from .base import Adapter, FormatError, RebuildReport, Segment
from .detect import detect_format

__all__ = ["Adapter", "FormatError", "RebuildReport", "Segment", "detect_format", "get_adapter", "out_name"]


def get_adapter(fmt: str) -> Adapter:
    if fmt == "md":
        from .md import MdAdapter
        return MdAdapter()
    if fmt == "docx":
        from .docx import DocxAdapter
        return DocxAdapter()
    if fmt == "pptx":
        from .pptx import PptxAdapter
        return PptxAdapter()
    raise FormatError("UNSUPPORTED_FORMAT", f"no adapter for {fmt!r}")


def out_name(path: Path | str, fmt: str) -> str:
    """`<name>.vi.<ext>`; the name is reduced to store-safe characters."""
    import re
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", Path(path).stem).strip("_")[:60] or "document"
    return f"{stem}.vi.{'md' if fmt == 'md' else fmt}"
