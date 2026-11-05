from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class BlockType(str, Enum):
    HEADING = "heading"
    PAGE_REF = "page_ref"
    PARAGRAPH = "paragraph"
    CAPTION = "caption"
    FIGURE = "figure"
    TABLE = "table"
    WARNING = "warning"
    FOOTER = "footer"
    LIST = "list"


class BlockStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"
    REVIEW = "review"


class LabelNote(BaseModel):
    text_en: str
    text_vi: Optional[str] = None
    suspicion: bool = False  # OCR / đoán ngữ cảnh — cần KTV xác minh
    guess_note: Optional[str] = None


class Block(BaseModel):
    block_id: str
    type: BlockType
    text_en: str = ""
    text_vi: Optional[str] = None
    font_size: Optional[float] = None
    bbox: Optional[List[float]] = None
    figure_id: Optional[str] = None
    image_path: Optional[str] = None
    full_page: bool = False
    labels: List[LabelNote] = Field(default_factory=list)
    protected_tokens: List[str] = Field(default_factory=list)
    requires_review: bool = False


class FigureEntry(BaseModel):
    figure_id: str
    figure_number: str
    caption_en: str = ""
    caption_vi: Optional[str] = None
    pdf_pages: List[int] = Field(default_factory=list)
    manual_refs: List[str] = Field(default_factory=list)
    image_paths: List[str] = Field(default_factory=list)
    bbox: Optional[List[float]] = None
    full_page: bool = False
    labels: List[LabelNote] = Field(default_factory=list)


class Page(BaseModel):
    pdf_page: int
    manual_ref: Optional[str] = None
    section: Optional[str] = None
    blocks: List[Block] = Field(default_factory=list)


class Document(BaseModel):
    doc_id: str
    source_file: str
    sections: List[str] = Field(default_factory=list)
    total_pdf_pages: int = 0
    pages: List[Page] = Field(default_factory=list)
    figures_registry: Dict[str, FigureEntry] = Field(default_factory=dict)


class Chunk(BaseModel):
    chunk_id: str
    doc_id: str
    pdf_page: int
    manual_ref: Optional[str] = None
    section: Optional[str] = None
    block_type: str
    text_en: str
    text_vi: Optional[str] = None
    protected_tokens: List[str] = Field(default_factory=list)
    glossary_terms: List[str] = Field(default_factory=list)
    requires_review: bool = False
    status: BlockStatus = BlockStatus.PENDING
    source_block_ids: List[str] = Field(default_factory=list)


class GlossaryEntry(BaseModel):
    id: str
    term_en: str
    term_vi: Optional[str] = None
    variants_en: List[str] = Field(default_factory=list)
    do_not_translate: bool = False
    domain: str = "electrical"
    status: str = "draft"  # draft | approved | protected
    frequency: int = 0
    source: str = "extract"
    notes: str = ""


class Glossary(BaseModel):
    version: str = "0.1"
    entries: List[GlossaryEntry] = Field(default_factory=list)

    def approved_subset(self) -> List[GlossaryEntry]:
        return [e for e in self.entries if e.status in ("approved", "protected")]

    def to_prompt_lines(self, terms_in_text: Optional[List[str]] = None) -> str:
        entries = self.approved_subset()
        if terms_in_text:
            lower = {t.lower() for t in terms_in_text}
            entries = [
                e
                for e in entries
                if e.term_en.lower() in lower
                or any(v.lower() in lower for v in e.variants_en)
            ]
        lines = []
        for e in entries:
            if e.do_not_translate or e.status == "protected":
                lines.append(f"- {e.term_en} → (giữ nguyên)")
            elif e.term_vi:
                lines.append(f"- {e.term_en} → {e.term_vi}")
        return "\n".join(lines) if lines else "(chưa có thuật ngữ approved)"


class QAIssue(BaseModel):
    chunk_id: str
    issue_type: str
    message: str
    severity: str = "warning"


class QAReport(BaseModel):
    doc_id: str
    total_chunks: int = 0
    translated: int = 0
    pending: int = 0
    issues: List[QAIssue] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(i.severity == "error" for i in self.issues)
