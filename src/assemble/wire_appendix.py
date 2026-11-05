"""Phụ lục chung — bảng mã màu dây (WIRE COLOR CODING TABLE)."""

from __future__ import annotations

from typing import List, Tuple

from src.translate.wire_labels import WIRE_COLORS, WIRE_INTRO_VI

APPENDIX_TITLE = "Phụ lục A. Bảng mã màu dây (WIRE COLOR CODING)"
APPENDIX_TITLE_SHORT = "Phụ lục A — Mã màu dây"

APPENDIX_NOTES_VI = [
    "1. Loại dây: AVSS/AVX cho 0,75 và 1,25 mm²; AVS cho 2 và 5 mm²; AV cho các cỡ khác.",
    "2. Mặc định tiết diện 0,75 mm² nếu sơ đồ không ghi số.",
    "3. Tiếp đất không ghi màu = tiếp đất qua vỏ thiết bị.",
    "4. Số trong ô vuông trên sơ đồ = số dây (line No.).",
    "5. Hai chữ cái = dây hai màu (màu nền + màu sọc), ví dụ WV = trắng–tím.",
]

# Thứ tự hiển thị giống sổ tay
COLOR_ORDER = ["B", "G", "L", "R", "W", "Y", "P", "O", "BR", "LG", "GR", "SB", "V"]


def color_table_rows() -> List[Tuple[str, str, str]]:
    """(mã, EN, VI)"""
    rows = []
    for code in COLOR_ORDER:
        en, vi = WIRE_COLORS[code]
        rows.append((code, en, vi))
    return rows


def appendix_intro() -> str:
    return (
        "Bảng này dùng chung toàn bộ sơ đồ mạch trong tài liệu. "
        "Trên hình, nhãn dạng 1.25 WV / 0.5 BrY = [tiết diện mm²] + [mã màu]. "
        + WIRE_INTRO_VI
    )


FIGURE_WIRE_NOTE = "Mã màu dây: xem Phụ lục A (đầu tài liệu)."
