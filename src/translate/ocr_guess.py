"""Phát hiện nhãn OCR dính chữ và đoán nghĩa từ ngữ cảnh."""

from __future__ import annotations

import re
from typing import Optional, Tuple

PART_NUM = re.compile(r"(YT\d{2}[A-Z]\d{5}[A-Z]\d)")

# OCR / BOM table noise
OCR_GARBLED = re.compile(
    r"(NamePart|No\.Q'ty|Q'ty|Name\s*Part|ASSY[A-Z]{2}\d|"
    r"\d{6,}|No\.\d+Name|1PAMYAZAKI|EEE100)",
    re.I,
)
GLUED_LONG = re.compile(r"^[A-Za-z0-9/':]{35,}$")
LOW_SPACE_RATIO = re.compile(r"^[A-Z0-9/':]{20,}$")


def is_ocr_suspicious(text: str) -> bool:
    t = text.strip()
    if not t:
        return False
    if re.match(r"^[A-Z]{2,}\d{2,}[A-Z0-9() /-]+$", t) and len(t) > 14:
        return True
    if len(t) > 35 and t.count(" ") < 2:
        return True
    if PART_NUM.search(t) and len(t) > 25 and t.count(" ") <= 4:
        return True
    if re.search(r"HARNESS[A-Z]{4,}", t.replace(" ", "")):
        return True
    # Chữ hoa dính liền: CABHARNESSASSY
    if re.search(r"[a-z][A-Z]{3,}", t) is None and re.search(r"[A-Z]{4,}[A-Z]{4,}", t.replace(" ", "")):
        compact = t.replace(" ", "")
        if len(compact) > 20 and " " not in t:
            return True
    return False


def guess_from_context(text_en: str) -> Tuple[str, str]:
    """
    Trả về (text_vi đoán, guess_note giải thích ngữ cảnh).
    """
    raw = text_en.strip()
    low = raw.lower()
    parts = PART_NUM.findall(raw)
    part_str = parts[0] if parts else ""

    # Vendor/copyright noise from diagram
    if re.search(r"1pamyazaki|kobelco|copyright", low):
        return f"[⚠ OCR] Thông tin nhà sản xuất/bản quyền — bỏ qua", "Dòng metadata sơ đồ, không cần dịch"

    if re.match(r"^[A-Z]{2,}\d+[A-Z0-9]+", raw) and re.search(r"\d{2,}", raw):
        note = "Mã dây/connector từ sơ đồ — giữ mã gốc"
        if "back up" in low or "backup" in low:
            return f"[⚠ OCR] {raw} (dự phòng/backup)", note
        return f"[⚠ OCR] Mã dây: {raw}", note

    if re.search(r"namepart|q'ty|no\.\d+name", low):
        ctx = []
        if "harness" in low:
            ctx.append("bộ dây điện")
        if "assy" in low:
            ctx.append("lắp ráp")
        if "cab" in low:
            ctx.append("cabin")
        if "fuel" in low and "pump" in low:
            ctx.append("bơm nhiên liệu")
        if "battery" in low:
            ctx.append("ắc quy")
        if "mounting" in low:
            ctx.append("chi tiết lắp ráp")
        ctx_str = " + ".join(ctx) if ctx else "danh mục linh kiện"
        vi = f"[⚠ OCR] Bảng BOM/danh mục: {ctx_str}"
        if part_str:
            vi += f" — mã {part_str}"
        note = "PDF OCR dính cột Name/Part No./Q'ty — đoán từ từ khóa lân cận"
        return vi, note

    # Section ref + harness + part: 23.2.8-1 UNDER FLOOR HARNESS : YT...
    m = re.match(
        r"(\d+\.\d+(?:\.\d+)?-\d+)\s+(.+?)\s*:\s*(YT\d{2}[A-Z]\d{5}[A-Z]\d)",
        raw,
        re.I,
    )
    if m:
        sec, desc, pn = m.group(1), m.group(2).strip(), m.group(3)
        desc_low = desc.lower()
        desc_map = {
            "under floor harness": "bộ dây sàn dưới",
            "ground cable": "cáp tiếp đất",
            "cab instrument harness": "bộ dây đồng hồ cabin",
            "left instrument harness": "bộ dây đồng hồ trái",
            "right instrument harness": "bộ dây đồng hồ phải",
            "floor plate harness": "bộ dây tấm sàn",
            "engine harness": "bộ dây động cơ",
        }
        desc_vi = desc
        for en, vi in desc_map.items():
            if en in desc_low:
                desc_vi = vi
                break
        vi = f"[⚠ OCR] {sec} {desc_vi}: {pn}"
        return vi, "Đoán từ mẫu mục sổ tay + mã phụ tùng"

    # Glued: UNDER FLOOR HARNESSGROUND CABLEYT13E...
    compact = low.replace(" ", "")
    if "harnessground" in compact or ("harness" in low and "groundcable" in compact):
        pn = parts[0] if parts else ""
        vi = f"[⚠ OCR] Bộ dây sàn dưới + cáp tiếp đất"
        if pn:
            vi += f" — mã {pn}"
        return vi, "OCR dính HARNESS/GROUND CABLE + mã phụ tùng"

    if "harness" in low and "assy" in low and part_str:
        kind = "cabin" if "cab" in low else "điện"
        vi = f"[⚠ OCR] Lắp ráp bộ dây {kind} — mã {part_str}"
        return vi, "OCR dính chữ ASSY/HARNESS — đoán từ ngữ cảnh sơ đồ"

    if "mounting" in low and part_str:
        thing = "linh kiện"
        if "fuel" in low:
            thing = "bơm nhiên liệu"
        elif "light" in low:
            thing = "đèn"
        vi = f"[⚠ OCR] Lắp {thing} — mã {part_str}"
        return vi, "OCR dính chữ MOUNTING — đoán từ ngữ cảnh"

    # Generic long glued
    if len(raw) > 30 and raw.count(" ") < 2:
        keywords = []
        for kw, vi_kw in [
            ("harness", "bộ dây"),
            ("relay", "rơ-le"),
            ("battery", "ắc quy"),
            ("switch", "công tắc"),
            ("sensor", "cảm biến"),
            ("pump", "bơm"),
            ("light", "đèn"),
            ("motor", "mô-tơ"),
        ]:
            if kw in low:
                keywords.append(vi_kw)
        if keywords:
            vi = f"[⚠ OCR] Đoán: {' / '.join(keywords)}"
            if part_str:
                vi += f" ({part_str})"
            return vi, "OCR dính chữ — đoán từ từ khóa có trong chuỗi"
        return f"[⚠ OCR] Không đọc rõ — giữ gốc, cần đối chiếu hình", "OCR không giải mã được"

    return raw, "Chưa dịch — cần review"
