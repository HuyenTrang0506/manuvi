from __future__ import annotations

import re
from typing import Optional

COPYRIGHT_PATTERN = re.compile(
    r"Copyright\s*©\s*\d{4}\s*Kobelco.*?$",
    re.IGNORECASE | re.MULTILINE | re.DOTALL,
)
BOOK_CODE_PATTERN = re.compile(r"\[S5YT\d+E\d+\]")
DOC_CODE_PATTERN = re.compile(r"\[0207CsCsspWbYs\]")

PART_NUMBER_PATTERN = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
FIGURE_REF_PATTERN = re.compile(r"Fig\.?\s*(\d+-\d+)", re.IGNORECASE)
MANUAL_REF_PATTERN = re.compile(r"\b(\d{2})-(\d{1,3})\b")
SECTION_HEADING_PATTERN = re.compile(
    r"^(\d{2})\.\s+([A-Z][A-Z0-9 /\-&\(\)']+)$",
    re.MULTILINE,
)
WARNING_PATTERN = re.compile(r"\b(WARNING|CAUTION|DANGER)\b", re.IGNORECASE)


def strip_noise(text: str) -> str:
    text = COPYRIGHT_PATTERN.sub("", text)
    text = BOOK_CODE_PATTERN.sub("", text)
    text = DOC_CODE_PATTERN.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_protected_tokens(text: str) -> list[str]:
    tokens: set[str] = set()
    tokens.update(PART_NUMBER_PATTERN.findall(text))
    for m in FIGURE_REF_PATTERN.finditer(text):
        tokens.add(f"Fig. {m.group(1)}")
    return sorted(tokens)


def extract_manual_ref(text: str, section_hint: Optional[str] = None) -> Optional[str]:
    """Lấy manual ref dạng 23-15 từ đầu/cuối trang."""
    head = text[:400]
    tail = text[-400:]
    candidates: list[str] = []
    for chunk in (head, tail):
        for m in MANUAL_REF_PATTERN.finditer(chunk):
            ref = f"{m.group(1)}-{m.group(2)}"
            if section_hint and not ref.startswith(section_hint.split(".")[0]):
                sec = section_hint.split(".")[0].zfill(2)
                if not ref.startswith(sec):
                    continue
            candidates.append(ref)
    return candidates[0] if candidates else None


def is_footer_block(text: str, font_size: Optional[float]) -> bool:
    if font_size is not None and font_size <= 6.5:
        return "Kobelco" in text or "Copyright" in text
    return bool(COPYRIGHT_PATTERN.search(text) and len(text) < 200)


def is_page_ref_block(text: str, font_size: Optional[float]) -> bool:
    t = text.strip()
    if font_size and font_size >= 11.5 and MANUAL_REF_PATTERN.fullmatch(t.replace(" ", "")):
        return True
    return bool(re.fullmatch(r"\d{2}-\d{1,3}", t))


def classify_block(text: str, font_size: Optional[float], has_image: bool = False) -> str:
    from src.models import BlockType

    if has_image:
        return BlockType.FIGURE
    if is_footer_block(text, font_size):
        return BlockType.FOOTER
    if is_page_ref_block(text, font_size):
        return BlockType.PAGE_REF
    if FIGURE_REF_PATTERN.search(text) and len(text) < 120:
        return BlockType.CAPTION
    if WARNING_PATTERN.search(text):
        return BlockType.WARNING
    if SECTION_HEADING_PATTERN.search(text) or (
        font_size and font_size >= 10 and re.match(r"^\d{2}\.", text.strip())
    ):
        return BlockType.HEADING
    if re.search(r"^\(\d+\)", text.strip()) or re.search(r"^\d+\.", text.strip()):
        return BlockType.LIST
    return BlockType.PARAGRAPH
