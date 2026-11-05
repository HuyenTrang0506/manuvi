"""Dịch danh sách dòng ngắn (cáp, BOM, nhãn panel) — giữ 1 dòng EN = 1 dòng VI."""

from __future__ import annotations

import re
from typing import Dict

from src.ingest.text_structure import fix_glued_tokens, normalize_pdf_text
from src.models import Chunk, Glossary
from src.translate.label_translator import translate_label_line

PART_NUM = re.compile(
    r"^(?:Y[A-Z]\d{2}[A-Z]\d{5}[A-Z]\d\d?|Local parts|"
    r"(?:CAP|CB|YAZAKI)[\s\-·]*\d[\d\-]*)$",
    re.I,
)
BOM_MARKERS = re.compile(r"Name|Part No\.|Q'ty|No\.", re.I)
VENDOR_NOISE = re.compile(r"^(?:TARMINAL|TERMINAL|YAZAKI|CAP|CB)\b", re.I)

LIST_PHRASES: Dict[str, str] = {
    # BOM headers
    "no.": "STT.",
    "no": "STT.",
    "name": "Tên",
    "part no.": "Mã phụ tùng",
    "part no": "Mã phụ tùng",
    "q'ty": "SL",
    "qty": "SL",
    # cables / harness
    "starter cable": "Cáp máy khởi động",
    "battery relay cable": "Cáp rơ-le ắc quy",
    "battery cable": "Cáp ắc quy",
    "battery ground cable": "Cáp tiếp đất ắc quy",
    "right guard harness": "Bộ dây tấm chắn phải",
    "main harness": "Bộ dây chính",
    "frame ground cable": "Cáp tiếp đất khung",
    "p3 pump harness": "Bộ dây bơm P3",
    "cab harness assy": "Lắp ráp bộ dây buồng lái",
    "floor plate harness assy": "Lắp ráp bộ dây tấm sàn",
    "instrument panel harness": "Bộ dây bảng đồng hồ",
    # instrument panel BOM
    "instrument panel assy": "Lắp ráp bảng đồng hồ",
    "r/h panel assy": "Lắp ráp bảng bên phải",
    "accel potentio dial": "Núm xoay biến trở gia tốc",
    "accel potentiometer dial": "Núm xoay biến trở gia tốc",
    "accel potentio meter": "Biến trở gia tốc",
    "kye switch": "Công tắc chìa khóa",
    "key switch": "Công tắc chìa khóa",
    "mode select switch": "Công tắc chọn chế độ",
    "speaker": "Loa",
    "cover assy": "Lắp ráp nắp",
    "cigalette lighter": "Bật lửa",
    "cigarette lighter": "Bật lửa",
    "gauge cluster": "Cụm đồng hồ",
    "auto accel release switch": "Công tắc nhả ga tự động",
    "dc-dc converter": "Bộ chuyển đổi DC-DC",
    "socket": "Ổ cắm",
    "overload indicate switch": "Công tắc báo quá tải",
    "rotary light switch": "Công tắc đèn xoay",
    "tuner": "Đầu thu sóng",
    "antenna": "Ăng-ten",
    "hour meter": "Đồng hồ giờ",
    "local parts": "Phụ tùng địa phương",
    # mounting / washer
    "mounting boom light": "Lắp đèn cần",
    "mounting cab": "Lắp buồng lái",
    "mounting light": "Lắp đèn",
    "mounting fuel pump": "Lắp bơm nhiên liệu",
    "mounting cab work light": "Lắp đèn làm việc buồng lái",
    "mounting boom work light": "Lắp đèn làm việc cần",
    "mounting deck light": "Lắp đèn sàn",
    "mounting work light": "Lắp đèn làm việc",
    "fuel pump": "Bơm nhiên liệu",
    "fuel pump assy": "Lắp ráp bơm nhiên liệu",
    "washer tank": "Bình nước rửa",
    "washer tank & pump": "Bình nước rửa & bơm",
    "window washer tank & pump": "Bình nước rửa kính & bơm",
    "window washer tank and pump": "Bình nước rửa kính & bơm",
    "washer": "Phun nước rửa",
    "air conditioner": "Điều hòa",
    "air conditioner (a/c)": "Điều hòa (A/C)",
    "a/c conditoner": "Điều hòa",
    "a/c conditioner": "Điều hòa",
    "receiver tank": "Bình chứa môi chất",
    "counter": "Bộ đếm",
    # OCR typo — dịch nghĩa, giữ mã số nếu có
    "tarmina": "Đầu cực",
    "tarminal": "Đầu cực",
    "terminal": "Đầu cực",
}

BLOCK_LIST: Dict[str, str] = {
    "Starter cable\nBattery relay cable\nBattery cable\nBattery ground cable\nRight guard harness\nMain harness\nFrame ground cable\nP3 pump harness": (
        "Cáp máy khởi động\nCáp rơ-le ắc quy\nCáp ắc quy\nCáp tiếp đất ắc quy\n"
        "Bộ dây tấm chắn phải\nBộ dây chính\nCáp tiếp đất khung\nBộ dây bơm P3"
    ),
    "MODE SELECT SWITCH\nSPEAKER\nCOVER ASSY\nCIGALETTE LIGHTER\nGAUGE CLUSTER": (
        "Công tắc chọn chế độ\nLoa\nLắp ráp nắp\nBật lửa\nCụm đồng hồ"
    ),
}

BLOCK_SORTED = sorted(BLOCK_LIST.items(), key=lambda x: -len(x[0]))
LIST_SORTED = sorted(LIST_PHRASES.items(), key=lambda x: -len(x[0]))


def _strip_trailing_partnum(line: str) -> tuple[str, str]:
    """Tách 'NAME YT…' hoặc 'TARMINAL 7114-2020' → (name, suffix)."""
    m = re.match(r"^(.+?)\s+((?:Y[A-Z0-9]{8,}|Local parts|\d{4,}[\-\d]*))$", line, re.I)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return line, ""


def _translate_list_line(line: str, glossary: Glossary) -> str:
    line = fix_glued_tokens(line.strip())
    if not line:
        return ""
    if re.match(r"^\d+$", line):
        return line
    if PART_NUM.match(line):
        return line  # giữ mã phụ tùng / vendor

    # TARMINAL 7114-2020 → Đầu cực 7114-2020
    name, suffix = _strip_trailing_partnum(line)
    low = name.lower().rstrip(".")
    # OCR
    low = low.replace("tarminal", "terminal").replace("cigalette", "cigarette").replace("kye ", "key ")

    for en, vi in LIST_SORTED:
        en_low = en.lower().rstrip(".")
        if low == en_low:
            return f"{vi} {suffix}".strip() if suffix else vi

    if low in ("no.", "name", "part no.", "q'ty", "no", "part no", "qty"):
        return LIST_PHRASES.get(low, LIST_PHRASES.get(low + ".", line))

    vi = translate_label_line(name, glossary)
    if vi and vi.lower() != name.lower():
        return f"{vi} {suffix}".strip() if suffix else vi
    return line


def translate_line_list(text_en: str, glossary: Glossary) -> str:
    text_en = normalize_pdf_text(text_en)
    for en, vi in BLOCK_SORTED:
        if normalize_pdf_text(en) == text_en:
            return vi
    lines = text_en.split("\n")
    return "\n".join(_translate_list_line(ln, glossary) for ln in lines)


def is_equipment_list(text_en: str) -> bool:
    lines = [ln.strip() for ln in text_en.split("\n") if ln.strip()]
    if len(lines) < 3:
        return False
    if is_bom_block(text_en):
        return True
    avg_len = sum(len(ln) for ln in lines) / len(lines)
    if avg_len > 40:
        return False
    sentence_like = sum(1 for ln in lines if re.search(r"\b(is|are|the|when|if)\b", ln, re.I))
    return sentence_like == 0 and len(lines) >= 3


def is_bom_block(text_en: str) -> bool:
    return bool(BOM_MARKERS.search(text_en)) and bool(
        re.search(r"Y[A-Z]\d{2}|Local parts", text_en, re.I)
    )


def translate_bom_block(text_en: str, glossary: Glossary) -> str:
    text_en = normalize_pdf_text(text_en)
    lines = text_en.split("\n")
    return "\n".join(_translate_list_line(ln, glossary) for ln in lines)


def translate_list_chunk(chunk: Chunk, glossary: Glossary) -> str:
    if is_bom_block(chunk.text_en):
        return translate_bom_block(chunk.text_en, glossary)
    return translate_line_list(chunk.text_en, glossary)
