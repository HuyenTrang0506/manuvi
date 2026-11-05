from __future__ import annotations

import re

# Pattern cho nhãn dây/wire trên wiring diagram (không phải prose cần dịch)
WIRE_LABEL_PATTERN = re.compile(
    r"(\d+\.\d+[A-Za-z]{1,3}\b|"
    r"\b[BRGYVLW]{1,2}\b\s*\(|"
    r"\bYT\d{2}[A-Z]\d{5}|"
    r"\bR-\d+:|SV-\d+:|SW-\d+:|CN-\d+)",
    re.I,
)
CODE_LABEL_PATTERN = re.compile(r"\b[A-Z]{1,4}-\d+:", re.I)


def is_figure_only_chunk(text: str) -> bool:
    t = text.strip()
    if re.match(r"^Fig\.?\s*\d+-\d+\s*\.{0,3}\s*$", t, re.I):
        return True
    if re.match(r"^Fig\.?\s*\d+-\d+\s{2,}.+$", t, re.I) and len(t) < 120:
        return False  # có title — cần dịch
    return False


def is_diagram_scrap(text: str) -> bool:
    """Phát hiện text trích từ wiring diagram — không nên đưa vào LLM dịch."""
    if not text or len(text.strip()) < 15:
        return True
    if is_figure_only_chunk(text):
        return False
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    if len(lines) < 2:
        # Caption ngắn OK
        if re.match(r"^Fig\.?\s*\d+-\d+", text.strip(), re.I):
            return False
        if len(text) < 40:
            return True

    wire_hits = len(WIRE_LABEL_PATTERN.findall(text))
    code_labels = len(CODE_LABEL_PATTERN.findall(text))
    words = re.findall(r"\b\w+\b", text)
    if not words:
        return True

    wire_ratio = wire_hits / max(len(words), 1)
    # Nhiều mã dây/relay, ít câu hoàn chỉnh
    has_sentence = bool(
        re.search(
            r"\b(the|and|for|with|when|before|after|remove|install|note|wire|used|check|refer)\b",
            text,
            re.I,
        )
    )
    if code_labels >= 2 and not has_sentence:
        return True
    if wire_ratio > 0.12 and not has_sentence:
        return True
    if wire_hits >= 6 and not has_sentence:
        return True

    # Dòng toàn mã ngắn kiểu "0.5YR", "BL (GND)"
    short_codes = sum(1 for l in lines if re.match(r"^[\d\.\w\s\(\)/:-]{1,25}$", l) and WIRE_LABEL_PATTERN.search(l))
    if short_codes >= 5 and short_codes / len(lines) > 0.5:
        return True

    return False


def is_translatable_prose(text: str, block_type: str) -> bool:
    if block_type == "caption":
        return True
    if is_diagram_scrap(text):
        return False
    # TOC lines
    if re.match(r"^[\d.]+\s*[A-Z][A-Z\s\.]{10,}$", text.strip()) and "..." in text:
        return True
    return len(re.findall(r"\b\w+\b", text)) >= 8
