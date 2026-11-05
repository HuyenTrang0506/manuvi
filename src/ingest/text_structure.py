"""Giữ cấu trúc text gốc: xuống dòng, khoảng trắng, ref."""

from __future__ import annotations

import re
from typing import Any

SOFT_BREAKS = re.compile(r"[\x0b\x0c\x1f\x7f]")

PART_NUM = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
FIG_REF = re.compile(r"Fig\.?\s*\d+-\d+", re.I)


def _join_spans_smart(parts: list[str]) -> str:
    if not parts:
        return ""
    out = parts[0]
    for nxt in parts[1:]:
        if not nxt:
            continue
        if not out:
            out = nxt
            continue
        if out[-1].isalnum() and nxt[0].isalnum():
            if not out.endswith("-") and not nxt.startswith((",", ".", ")", ";", ":")):
                out += " "
        out += nxt
    return out


def extract_text_from_block(block: dict[str, Any]) -> str:
    """Trích text từ block PyMuPDF, giữ xuống dòng theo line gốc."""
    if block.get("type") != 0:
        return ""
    lines_out: list[str] = []
    for line in block.get("lines", []):
        span_parts = [span.get("text", "") for span in line.get("spans", [])]
        line_text = _join_spans_smart(span_parts)
        if line_text.strip():
            lines_out.append(line_text.rstrip())
    return "\n".join(lines_out)


def normalize_pdf_text(text: str) -> str:
    if not text:
        return ""
    text = SOFT_BREAKS.sub("\n", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in text.split("\n"):
        line = re.sub(r"[ \t]+", " ", line).strip()
        lines.append(line)
    text = "\n".join(lines)
    text = fix_glued_tokens(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"mm\s*2\b", "mm²", text)
    text = re.sub(r"mm\s*²", "mm²", text)
    return text.strip()


def fix_glued_tokens(text: str) -> str:
    placeholders: dict[str, str] = {}

    def protect(m):
        key = f"@@P{len(placeholders)}@@"
        placeholders[key] = m.group(0)
        return key

    for pat in [PART_NUM, FIG_REF]:
        text = pat.sub(protect, text)

    text = re.sub(r"(\d\.\d+(?:\.\d+)*)([A-Z])", r"\1 \2", text)
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    text = re.sub(r"(\d{4})([A-Z]{2}\d)", r"\1 \2", text)
    text = re.sub(r"(\))([A-Za-z])", r"\1 \2", text)

    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


def apply_structure_from_en(text_en: str, text_vi: str) -> str:
    """Căn xuống dòng VI theo EN nếu VI bị dính một cục."""
    if not text_vi:
        return ""
    if "\n" in text_vi:
        return normalize_pdf_text(text_vi)
    en_lines = [l for l in text_en.split("\n") if l.strip()]
    if len(en_lines) <= 1:
        return text_vi
    vi = text_vi
    vi = re.sub(r"(?<=[\.\)])(\s*)(\d+\))", r"\n\2", vi)
    vi = re.sub(r"(?<=[a-zà-ỹ])(\d+\.)", r"\n\1", vi)
    return vi


def clean_for_xml(text: str) -> str:
    """Giữ \\n, bỏ control char khác."""
    if not text:
        return ""
    text = SOFT_BREAKS.sub("\n", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
