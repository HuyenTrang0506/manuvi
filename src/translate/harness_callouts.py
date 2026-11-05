"""Nhãn chữ trên sơ đồ bộ dây / kết nối — thường vẽ trong ảnh, khó OCR.

Đưa vào bảng chú thích (không sửa pixel ảnh gốc).
"""

from __future__ import annotations

from typing import Dict, List, Tuple

# (EN hiển thị, VI nghĩa)
HARNESS_CALLOUTS: List[Tuple[str, str]] = [
    ("SPEAKER (R)", "Loa (phải)"),
    ("SPEAKER (L)", "Loa (trái)"),
    ("SPEAKER RIGHT", "Loa phải"),
    ("SPEAKER LEFT", "Loa trái"),
    ("SPEAKER", "Loa"),
    ("WIPER MOTOR", "Mô-tơ gạt mưa"),
    ("ROOF WIPER MOTOR", "Mô-tơ gạt mưa trần"),
    ("TO CAB", "Đến cabin"),
    ("FROM CAB", "Từ cabin"),
    ("CAB WORK LIGHT", "Đèn làm việc cabin"),
    ("CAB WORKING LIGHT", "Đèn làm việc cabin"),
    ("CAB WORK LIGHT 1, 2, 3", "Đèn làm việc cabin 1, 2, 3"),
    ("CAB WORKING LIGHT 4", "Đèn làm việc cabin 4"),
    ("WORK LIGHT", "Đèn làm việc"),
    ("RED TAPING", "Quấn băng keo đỏ"),
    ("TAPING", "Quấn băng keo"),
    ("SUPPLY WITH SUMITOMO", "Cấp kèm đầu nối Sumitomo"),
    ("GROUND", "Mass / tiếp đất"),
    ("TO FRAME", "Đến khung"),
    ("TO ENGINE", "Đến động cơ"),
    ("TO BATTERY", "Đến ắc quy"),
]

HARNESS_PHRASES: Dict[str, str] = {en.lower(): vi for en, vi in HARNESS_CALLOUTS}
# biến thể thêm
HARNESS_PHRASES.update(
    {
        "cab work light 1, 2, 3": "đèn làm việc cabin 1, 2, 3",
        "cab working light 4": "đèn làm việc cabin 4",
        "red taping": "quấn băng keo đỏ",
        "taping": "quấn băng keo",
        "to cab": "đến cabin",
        "from cab": "từ cabin",
        "speaker (r)": "loa (phải)",
        "speaker (l)": "loa (trái)",
        "wiper motor": "mô-tơ gạt mưa",
        "supply with sumitomo": "cấp kèm đầu nối Sumitomo",
        "cab harness assy": "lắp ráp bộ dây cabin",
        "cab harness assy (1/2)": "lắp ráp bộ dây cabin (1/2)",
        "cab harness assy (2/2)": "lắp ráp bộ dây cabin (2/2)",
        "work light (r.h)": "đèn làm việc (bên phải)",
        "work light (l.h)": "đèn làm việc (bên trái)",
    }
)


def is_harness_figure(caption_en: str = "", figure_id: str = "") -> bool:
    blob = f"{caption_en} {figure_id}".upper()
    keys = (
        "HARNESS",
        "CONNECTION",
        "CIRCUIT",
        "CAB ",
        "UPPER",
        "ENGINE",
        "FLOOR",
        "MAIN HARNESS",
        "INSTRUMENT",
    )
    return any(k in blob for k in keys)
