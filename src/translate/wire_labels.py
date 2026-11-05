"""
Ký hiệu dây trên sơ đồ mạch Kobelco:
  [tiết diện mm²] + [mã màu]
Ví dụ: 1.25 WV = dây 1.25 mm² trắng–tím

Bảng màu từ mục NOTES / WIRE COLOR CODING TABLE (23-5).
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

# Sign → (EN, VI) — từ WIRE COLOR CODING TABLE
WIRE_COLORS: Dict[str, Tuple[str, str]] = {
    "B": ("Black", "đen"),
    "G": ("Green", "xanh lá"),
    "L": ("Blue", "xanh dương"),
    "R": ("Red", "đỏ"),
    "W": ("White", "trắng"),
    "Y": ("Yellow", "vàng"),
    "P": ("Pink", "hồng"),
    "O": ("Orange", "cam"),
    "BR": ("Brown", "nâu"),
    "LG": ("Light Green", "xanh lá nhạt"),
    "GR": ("Gray", "xám"),
    "SB": ("Sky Blue", "xanh trời"),
    "V": ("Violet", "tím"),
}

# Tiết diện + màu (dính hoặc tách): 1.25WV | 0.5 BrY | 1.25 W
WIRE_FULL = re.compile(
    r"^(\d+(?:\.\d+)?)\s*([A-Za-z]{1,4})$",
    re.I,
)
# Chỉ màu / màu kép: WB, BrY, LgB, WGR (+ biến thể 2–3 chữ cái màu)
WIRE_COLOR_ONLY = re.compile(
    r"^(?:B|G|L|R|W|Y|P|O|V|Br|Lg|Gr|Sb|"
    r"[BGRWYLPOV]{2,3}|"
    r"Br[YLRBWG]|Lg[BRWY]|Gr[LRBWY]|"
    r"5BW)$",
    re.I,
)
# Cỡ dây thuần: 0.5 / 1.25
WIRE_GAUGE_ONLY = re.compile(r"^(\d+(?:\.\d+)?)$")

WIRE_INTRO_VI = (
    "Ký hiệu dây trên sơ đồ: [tiết diện mm²] + [mã màu]. "
    "Hai chữ = dây hai màu (nền + sọc). "
    "Mặc định 0.75 mm² nếu không ghi số (xem WIRE COLOR CODING TABLE)."
)


def _split_color_parts(code: str) -> List[str]:
    """BrY → ['Br','Y']; WB → ['W','B']; Lg → ['Lg']."""
    c = code.strip()
    if not c:
        return []
    # Thử prefix 2 ký tự đặc biệt trước
    parts: List[str] = []
    i = 0
    upper = c
    while i < len(upper):
        two = upper[i : i + 2]
        if two.upper() in {"BR", "LG", "GR", "SB"} and (
            i + 2 == len(upper) or upper[i + 2 : i + 3].upper() not in {"R"}  # avoid BR from B+R wrong
        ):
            # Br / Lg / Gr / Sb chỉ khi đúng camel hoặc full upper pair
            if two[0].isupper() and (len(two) == 1 or two[1].islower() or two.isupper()):
                if two.upper() in WIRE_COLORS:
                    parts.append(two.upper())
                    i += 2
                    continue
        one = upper[i].upper()
        if one in WIRE_COLORS:
            parts.append(one)
            i += 1
            continue
        # Không khớp — trả nguyên chuỗi
        return [c.upper()]
    return parts or [c.upper()]


def color_code_to_vi(code: str) -> Optional[str]:
    parts = _split_color_parts(code)
    names = []
    for p in parts:
        key = p.upper()
        if key in WIRE_COLORS:
            names.append(WIRE_COLORS[key][1])
        else:
            return None
    if not names:
        return None
    return "–".join(names)


def parse_wire_label(text: str) -> Optional[Tuple[Optional[str], str]]:
    """
    Trả về (gauge_mm2 | None, color_code) nếu là nhãn dây.
    """
    t = re.sub(r"[\x00-\x1f]", " ", text).strip()
    t = re.sub(r"\s+", " ", t)
    if not t or len(t) > 12:
        return None
    m = WIRE_FULL.match(t)
    if m:
        return m.group(1), m.group(2)
    if WIRE_COLOR_ONLY.match(t):
        return None, t
    return None


def is_wire_label(text: str) -> bool:
    return parse_wire_label(text) is not None


def format_wire_vi(text: str) -> Optional[str]:
    """1.25 WV → dây 1.25 mm² trắng–tím"""
    parsed = parse_wire_label(text)
    if not parsed:
        return None
    gauge, color = parsed
    color_vi = color_code_to_vi(color)
    if not color_vi:
        if gauge:
            return f"dây {gauge} mm² ({color})"
        return f"mã màu {color}"
    if gauge:
        return f"dây {gauge} mm² {color_vi}"
    return f"dây màu {color_vi}"


def try_merge_gauge_color(a: str, b: str) -> Optional[str]:
    """Gộp '1.25' + 'WV' → '1.25 WV'."""
    a, b = a.strip(), b.strip()
    if WIRE_GAUGE_ONLY.match(a) and WIRE_COLOR_ONLY.match(b):
        return f"{a} {b}"
    if WIRE_COLOR_ONLY.match(a) and WIRE_GAUGE_ONLY.match(b):
        return f"{b} {a}"
    return None
