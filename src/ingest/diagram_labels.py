"""Trích và dịch nhãn trên sơ đồ mạch."""

from __future__ import annotations

import re
from typing import List, Optional

import fitz

from src.glossary.manager import load_glossary
from src.ingest.noise_filter import strip_noise
from src.models import Glossary, LabelNote
from src.translate.label_translator import split_and_translate_label, translate_label_text
from src.translate.wire_labels import try_merge_gauge_color

SKIP_PATTERN = re.compile(
    r"^(Copyright|Fig\.|^\d{2}-\d+$|S5YT\d+|0207Cs|ELECTRICAL SYSTEM$)",
    re.I,
)
LABELED_CODE = re.compile(r"([A-Z]{1,4}-\d+)\s*:\s*([A-Z0-9][\w\s\./\-]{1,80})", re.I)
# Mã thuần + mô tả không có dấu :
CODE_SPACE = re.compile(r"^([A-Z]{1,4}-\d+)\s+([A-Z].+)$", re.I)
PART_NUM = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
PIN_ONLY = re.compile(r"^\d{1,3}$")

# Không cắt sớm — sơ đồ mạch có rất nhiều nhãn
MAX_LABELS = 500


def translate_label(text_en: str, glossary: Glossary) -> str:
    return translate_label_text(text_en, glossary)


def _line_texts_from_page(page: fitz.Page) -> List[str]:
    """Mỗi dòng PDF = 1 chuỗi (không dính span thành block dài)."""
    lines: List[str] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        for line in b.get("lines", []):
            parts = []
            for span in line.get("spans", []):
                t = span.get("text", "")
                if t:
                    parts.append(t)
            if not parts:
                continue
            text = strip_noise("".join(parts)).strip()
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                lines.append(text)
    return lines


def _line_texts_in_region(page: fitz.Page, region: fitz.Rect) -> List[str]:
    lines: List[str] = []
    for b in page.get_text("dict")["blocks"]:
        if b.get("type") != 0:
            continue
        bx0, by0, bx1, by1 = b["bbox"]
        if not fitz.Rect(bx0, by0, bx1, by1).intersects(region):
            continue
        for line in b.get("lines", []):
            parts = [span.get("text", "") for span in line.get("spans", [])]
            text = strip_noise("".join(parts)).strip()
            text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", text)
            text = re.sub(r"\s+", " ", text).strip()
            if text:
                lines.append(text)
    return lines


def _merge_multiline_codes(lines: List[str]) -> List[str]:
    """
    Gộp:
      SE-13: + ENG.SPEED + SENSOR → SE-13: ENG.SPEED SENSOR
      1.25 + WV → 1.25 WV
      C-2: + GAUGE CLUSTER → C-2: GAUGE CLUSTER
    """
    out: List[str] = []
    i = 0
    while i < len(lines):
        cur = lines[i]
        # Mã kết thúc bằng ':' — gộp các dòng chữ hoa tiếp theo
        if re.match(r"^[A-Z]{1,4}-\d+\s*:?\s*$", cur, re.I) or (
            cur.endswith(":") and re.match(r"^[A-Z]{1,4}-\d+", cur, re.I)
        ):
            code = cur.rstrip(":").strip()
            parts: List[str] = []
            j = i + 1
            while j < len(lines) and j < i + 5:
                nxt = lines[j]
                if re.match(r"^[A-Z]{1,4}-\d+", nxt, re.I):
                    break
                if re.match(r"^\d+(?:\.\d+)?$", nxt):
                    break
                if len(nxt) > 40:
                    break
                # Chữ mô tả / viết tắt kỹ thuật
                if re.match(r"^[A-Z0-9][A-Z0-9\s\./\-]{0,40}$", nxt, re.I):
                    parts.append(nxt)
                    j += 1
                    continue
                break
            if parts:
                out.append(f"{code}: {' '.join(parts)}")
                i = j
                continue
        # Gộp tiết diện + màu
        if i + 1 < len(lines):
            merged = try_merge_gauge_color(cur, lines[i + 1])
            if merged:
                out.append(merged)
                i += 2
                continue
        out.append(cur)
        i += 1
    return out


def _should_keep(raw: str) -> bool:
    raw = raw.strip()
    if not raw or len(raw) < 1:
        return False
    if SKIP_PATTERN.search(raw):
        return False
    if "Kobelco" in raw or "All rights" in raw:
        return False
    if len(raw) > 120 and " " in raw and not LABELED_CODE.search(raw):
        return False
    return True


def _priority(text: str) -> int:
    """Ưu tiên giữ: thiết bị có tên > đèn báo > mã dây > số chân."""
    t = text.strip()
    if LABELED_CODE.match(t) or CODE_SPACE.match(t):
        return 0
    if re.match(r"^[A-Z]{1,4}-\d+", t, re.I):
        return 1
    if re.search(
        r"ILLUMINATION|CHARGE|OIL PRESS|WATER TEMP|AIR CLEANER|FUEL LEVEL|"
        r"FUEL METER|GLOW|TRAVEL TWO|SWING PARKING|CPU ALLERGY|OVERLOAD|"
        r"COOLANT THERMO|CONTROLLER EARTH|GAUGE CLUSTER",
        t,
        re.I,
    ):
        return 2
    if try_merge_gauge_color(t, "W") or re.match(r"^\d+(?:\.\d+)?\s*[A-Za-z]+$", t):
        return 3
    if PIN_ONLY.match(t):
        return 5
    return 4


def _notes_from_lines(lines: List[str], glossary: Glossary) -> List[LabelNote]:
    merged = _merge_multiline_codes(lines)
    # Thêm regex LABELED từ toàn bộ text (phòng sót)
    blob = "\n".join(lines)
    for m in LABELED_CODE.finditer(blob):
        cand = f"{m.group(1).upper()}:{m.group(2).strip()}"
        if cand not in merged:
            merged.append(cand)

    ranked = sorted(
        [x for x in merged if _should_keep(x)],
        key=lambda x: (_priority(x), x.lower()),
    )

    seen: set[str] = set()
    notes: List[LabelNote] = []
    for raw in ranked:
        for split_lb in split_and_translate_label(LabelNote(text_en=raw), glossary):
            key = re.sub(r"\s+", " ", split_lb.text_en.lower()).strip()
            if key in seen or not key:
                continue
            seen.add(key)
            notes.append(split_lb)
            if len(notes) >= MAX_LABELS:
                return notes
    return notes


def extract_labels_from_page(page: fitz.Page, glossary: Optional[Glossary] = None) -> list[LabelNote]:
    """Trích nhãn text trên trang sơ đồ — từng dòng, không cắt sớm."""
    glossary = glossary or Glossary()
    return _notes_from_lines(_line_texts_from_page(page), glossary)


def extract_labels_for_bbox(
    page: fitz.Page,
    bbox: list[float],
    glossary: Optional[Glossary] = None,
    padding: float = 15,
) -> list[LabelNote]:
    """Nhãn nằm trong hoặc gần vùng hình (theo tọa độ)."""
    x0, y0, x1, y1 = bbox
    region = fitz.Rect(x0 - padding, y0 - padding, x1 + padding, y1 + padding)
    glossary = glossary or Glossary()
    return _notes_from_lines(_line_texts_in_region(page, region), glossary)
