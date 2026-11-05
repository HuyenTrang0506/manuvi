"""
SERVICE DIAGNOSIS TABLE — Section 43-7 (SK70SR-1E)

Bilingual for-loop table: SW code | English item | Vietnamese item | Normal value.
Items SW00–SW36 reconstructed from PDF block data and section43_map translations.

Usage:
    from src.translate.section43_table import get_table_43_7, save_json_cache
    rows = get_table_43_7()          # list of dicts
    save_json_cache(workspace)       # → workspace/translations/section43_tables/43-7.json
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict

# ─────────────────────────────────────────────────────────────────────────────
# Table data: each dict has keys:
#   sw        – hardware check SW setting ("00" … "36")
#   en_item   – service diagnosis item (English)
#   vi_item   – service diagnosis item (Vietnamese)
#   en_display – brief display content description (English), may be ""
#   vi_display – brief display content description (Vietnamese), may be ""
#   normal    – normal value string (may be "—")
# ─────────────────────────────────────────────────────────────────────────────

ITEMS_43_7: List[Dict[str, str]] = []

def _row(
    sw: str,
    en_item: str,
    vi_item: str,
    normal: str = "—",
    en_display: str = "",
    vi_display: str = "",
) -> Dict[str, str]:
    return {
        "sw": sw,
        "en_item": en_item,
        "vi_item": vi_item,
        "normal": normal,
        "en_display": en_display,
        "vi_display": vi_display,
    }


# ── SW00–SW05: Basic status items ────────────────────────────────────────────
ITEMS_43_7 += [
    _row(
        "00",
        "Normal (no service diagnosis) / Trouble diagnosis (error code)",
        "Bình thường (không chẩn đoán dịch vụ) / Chẩn đoán sự cố (mã lỗi)",
        normal="—",
        en_display="Error code displayed when fault exists",
        vi_display="Hiển thị mã lỗi khi có sự cố",
    ),
    _row(
        "01",
        "A set value of engine rpm",
        "Giá trị cài đặt tốc độ động cơ",
        normal="1020~2250",
        en_display="Displayed in 3 digits by 10 rpm. [230] = 2300 rpm.",
        vi_display="Hiển thị 3 chữ số theo bước 10 vg/ph. [230] = 2300 vg/ph.",
    ),
    _row(
        "02",
        "An actual engine rpm",
        "Tốc độ động cơ thực tế",
        normal="Khoảng 1020~2250",
        en_display="Displayed in 3 digits by 10 rpm. [230] = 2300 rpm.",
        vi_display="Hiển thị 3 chữ số theo bước 10 vg/ph. [230] = 2300 vg/ph.",
    ),
    _row(
        "03",
        "Key switch input / option switch input",
        "Đầu vào công tắc khóa / đầu vào công tắc tùy chọn",
        normal="—",
        en_display="Key SW: 10th digit (ON[1]/OFF[0]); Battery relay: 1st digit (ON[1]/OFF[0]).",
        vi_display="Công tắc khóa: chữ số thứ 10 (BẬT[1]/TẮT[0]); Rơ-le ắc quy: chữ số thứ 1.",
    ),
    _row(
        "04",
        "Mode switch / washer switch / work light switch",
        "Công tắc chế độ / công tắc rửa kính / công tắc đèn làm việc",
        normal="—",
        en_display="Mode SW: 10th digit ([H][S][F]); Option SW: 1st digit (ON[1]/OFF[0]).",
        vi_display="Công tắc chế độ: chữ số thứ 10 ([H][S][F]); Công tắc tùy chọn: chữ số thứ 1.",
    ),
    _row(
        "05",
        "Wiper switch / washer switch / work light switch",
        "Công tắc gạt mưa / công tắc rửa kính / công tắc đèn làm việc",
        normal="—",
        en_display="Wiper SW: 100th digit; Washer SW: 10th digit; Work lamp SW: 1st digit. push[1]/OFF[0].",
        vi_display="Công tắc gạt mưa: chữ số thứ 100; Rửa kính: chữ số thứ 10; Đèn làm việc: chữ số thứ 1.",
    ),
]

# ── SW06–SW09: Accel dial / governor motor position ──────────────────────────
ITEMS_43_7 += [
    _row(
        "06",
        "Voltage of accel dial",
        "Điện áp núm ga",
        normal="0.4~4.6 V",
        en_display="Whole numbers to 2nd decimal. 2.34 V displayed as [234].",
        vi_display="Số nguyên đến 2 thập phân. 2,34 V hiển thị là [234].",
    ),
    _row(
        "07",
        "Accel dial position",
        "Vị trí núm ga",
        normal="0~100 %",
        en_display="Governor motor position (from accel dial voltage) in per cents.",
        vi_display="Vị trí mô-tơ điều tốc (quy đổi từ điện áp núm ga) theo phần trăm.",
    ),
    _row(
        "08",
        "Step number of governor motor",
        "Số bước mô-tơ điều tốc",
        normal="0~630",
        en_display="Present step number displayed in three whole numbers.",
        vi_display="Số bước hiện tại hiển thị bằng ba số nguyên.",
    ),
    _row(
        "09",
        "Governor motor position",
        "Vị trí mô-tơ điều tốc",
        normal="0~100 %",
        en_display="Percent position of step number between low idle and high idle.",
        vi_display="Phần trăm vị trí của số bước giữa không tải thấp và không tải cao.",
    ),
]

# ── SW10–SW12: Governor motor current / limit switch ─────────────────────────
ITEMS_43_7 += [
    _row(
        "10",
        "Phase current of governor motor A",
        "Dòng điện pha A mô-tơ điều tốc",
        normal="Khoảng 150 mA",
        en_display="Whole numbers to 2nd decimal. 2.34 A displayed as [234].",
        vi_display="Số nguyên đến 2 thập phân. 2,34 A hiển thị là [234].",
    ),
    _row(
        "11",
        "Phase current of governor motor B",
        "Dòng điện pha B mô-tơ điều tốc",
        normal="Khoảng 150 mA",
        en_display="Whole numbers to 2nd decimal. 2.34 A displayed as [234].",
        vi_display="Số nguyên đến 2 thập phân. 2,34 A hiển thị là [234].",
    ),
    _row(
        "12",
        "Governor motor limit switch",
        "Công tắc giới hạn mô-tơ điều tốc",
        normal="—",
        en_display="Status on 1st digit: ON[1] / OFF[0].",
        vi_display="Trạng thái chữ số thứ 1: BẬT[1] / TẮT[0].",
    ),
]

# ── SW13–SW23: Pressure sensors & travel boost-up valve ─────────────────────
_SENSOR_DISPLAY_EN = (
    "Output command: 10th digit (ON[1]/OFF[0]); "
    "Feedback: 1st digit (ON[1]/OFF[0])."
)
_SENSOR_DISPLAY_VI = (
    "Lệnh đầu ra: chữ số thứ 10 (BẬT[1]/TẮT[0]); "
    "Phản hồi: chữ số thứ 1 (BẬT[1]/TẮT[0])."
)

ITEMS_43_7 += [
    _row("13", "Boom raise pressure sensor",      "Cảm biến áp nâng cần",          "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("14", "Boom down pressure sensor",       "Cảm biến áp hạ cần",            "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("15", "Arm out pressure sensor",         "Cảm biến áp tay gầu ra",        "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("16", "Arm in pressure sensor",          "Cảm biến áp tay gầu vào",       "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("17", "Bucket digging pressure sensor",  "Cảm biến áp đào gầu",           "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("18", "Bucket dump pressure sensor",     "Cảm biến áp đổ gầu",            "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("19", "Swing pressure sensor",           "Cảm biến áp quay toa",          "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("20", "P3 pump pressure sensor",         "Cảm biến áp bơm P3",            "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("21", "Travel right pressure sensor",    "Cảm biến áp di chuyển phải",    "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row("22", "Travel left pressure sensor",     "Cảm biến áp di chuyển trái",    "0.4~5.0 V", _SENSOR_DISPLAY_EN, _SENSOR_DISPLAY_VI),
    _row(
        "23",
        "Travel boost-up selector valve output command",
        "Lệnh đầu ra van chọn tăng lực di chuyển",
        "—",
        _SENSOR_DISPLAY_EN,
        _SENSOR_DISPLAY_VI,
    ),
]

# ── SW24: Swing parking selector valve / battery relay ───────────────────────
ITEMS_43_7 += [
    _row(
        "24",
        "Output command of swing parking selector valve / feedback; Battery relay output command / feedback",
        "Lệnh đầu ra van chọn phanh quay toa / phản hồi; Lệnh đầu ra rơ-le ắc quy / phản hồi",
        "—",
        "Output command: 10th digit (ON[1]/OFF[0]); Feedback: 1st digit (ON[1]/OFF[0]).",
        "Lệnh đầu ra: chữ số thứ 10 (BẬT[1]/TẮT[0]); Phản hồi: chữ số thứ 1 (BẬT[1]/TẮT[0]).",
    ),
]

# ── SW25–SW30: Proportional valve currents ───────────────────────────────────
_CURRENT_DISPLAY_EN = "Current displayed in mA."
_CURRENT_DISPLAY_VI = "Dòng điện hiển thị theo mA."

ITEMS_43_7 += [
    _row("25", "Command current for P1 bypass cut proportional valve",   "Dòng lệnh van tỷ lệ cắt bypass P1",    "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
    _row("26", "Feedback current of P1 bypass cut proportional valve",   "Dòng phản hồi van tỷ lệ cắt bypass P1", "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
    _row("27", "Command current of P2 bypass cut proportional valve",    "Dòng lệnh van tỷ lệ cắt bypass P2",    "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
    _row("28", "Feedback current of P2 bypass cut proportional valve",   "Dòng phản hồi van tỷ lệ cắt bypass P2", "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
    _row("29", "Command current of travel straight proportional valve",  "Dòng lệnh van tỷ lệ đi thẳng",         "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
    _row("30", "Feedback current of travel straight valve",              "Dòng phản hồi van đi thẳng",            "280~760 mA", _CURRENT_DISPLAY_EN, _CURRENT_DISPLAY_VI),
]

# ── SW31: Wiper motor rise-up LS / reverse LS ────────────────────────────────
ITEMS_43_7 += [
    _row(
        "31",
        "Wiper motor rise-up LS / reverse LS",
        "LS nâng mô-tơ gạt mưa / LS lùi",
        "—",
        "LS status: 10th digit (ON[1]/OFF[0]); Rise-up LS: 1st digit (ON[1]/OFF[0]).",
        "Trạng thái LS: chữ số thứ 10 (BẬT[1]/TẮT[0]); LS nâng: chữ số thứ 1 (BẬT[1]/TẮT[0]).",
    ),
]

# ── SW32: (gap — not in source data, reserved) ───────────────────────────────
# SW32 is not present in parsed block data; skip to SW33.

# ── SW33–SW36: Wiper/washer relay commands & internal switch ─────────────────
ITEMS_43_7 += [
    _row(
        "33",
        "Wiper motor arc preventive relay / forward relay / reverse relay command",
        "Rơ-le chống hồ quang mô-tơ gạt mưa / lệnh rơ-le tiến / rơ-le lùi",
        "—",
        "Arc prev. relay: 100th digit; Forward relay: 10th digit; Reverse relay: 1st digit (ON[1]/OFF[0]).",
        "Rơ-le chống hồ quang: chữ số thứ 100; Rơ-le tiến: chữ số thứ 10; Rơ-le lùi: chữ số thứ 1.",
    ),
    _row(
        "34",
        "Washer motor relay / work light relay / CPU trouble lamp command",
        "Rơ-le mô-tơ rửa kính / rơ-le đèn làm việc / lệnh đèn báo lỗi CPU",
        "—",
        "Washer relay: 100th digit; Work light relay: 10th digit; CPU lamp: 1st digit (ON[1]/OFF[0]).",
        "Rơ-le rửa kính: chữ số thứ 100; Đèn làm việc: chữ số thứ 10; Đèn báo CPU: chữ số thứ 1.",
    ),
    _row(
        "35",
        "Wiper operating command / power window operating input",
        "Lệnh vận hành gạt mưa / đầu vào vận hành cửa sổ điện",
        "—",
        "Wiper command: 10th digit (ON[1]/OFF[0]); Window input: 1st digit (ON[1]/OFF[0]).",
        "Lệnh gạt mưa: chữ số thứ 10 (BẬT[1]/TẮT[0]); Đầu vào cửa sổ: chữ số thứ 1.",
    ),
    _row(
        "36",
        "Internal adjust switch",
        "Công tắc điều chỉnh nội bộ",
        "—",
        "Status: 1st digit TEST[1] / RUN[0].",
        "Trạng thái: chữ số thứ 1 TEST[1] / RUN[0].",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

def get_table_43_7() -> List[Dict[str, str]]:
    """Return all service diagnosis items for page 43-7.

    Each item is a dict with keys: sw, en_item, vi_item, normal,
    en_display, vi_display.
    """
    return list(ITEMS_43_7)


def save_json_cache(workspace: Path) -> Path:
    """Write items to JSON cache.  Returns the path written."""
    out_dir = workspace / "translations" / "section43_tables"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "43-7.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "page": "43-7",
                "title_en": "SERVICE DIAGNOSIS DISPLAY ITEMS",
                "title_vi": "CÁC MỤC HIỂN THỊ CHẨN ĐOÁN DỊCH VỤ",
                "columns": ["sw", "en_item", "vi_item", "normal"],
                "items": ITEMS_43_7,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    return out_path


def load_json_cache(workspace: Path) -> List[Dict[str, str]]:
    """Load cached table (falls back to in-memory ITEMS_43_7)."""
    cache = workspace / "translations" / "section43_tables" / "43-7.json"
    if cache.exists():
        with open(cache, encoding="utf-8") as f:
            return json.load(f)["items"]
    return get_table_43_7()
