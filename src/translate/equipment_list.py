"""
Danh mục thiết bị điện SK70SR-1ES — mục 23.1.2 (PDF p.192–193).

Mã dạng X-nn là **chỉ mục tra cứu** trong sổ tay:
  Group | Code | Name | Part No.
Chữ cái = nhóm thiết bị, số = thứ tự trong nhóm.
Dùng trên sơ đồ mạch và flowchart chẩn đoán (vd. "C-1 controller" = bộ điều khiển Mechatro).
"""

from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

# prefix → (tên nhóm EN, tên nhóm VI)
GROUP_PREFIX: Dict[str, Tuple[str, str]] = {
    "C": ("Controller", "Bộ điều khiển"),
    "D": ("Diode", "Điode"),
    "E": ("Electric fitting", "Phụ kiện điện"),
    "L": ("Light", "Đèn"),
    "M": ("Motor", "Mô-tơ"),
    "R": ("Relay", "Rơ-le"),
    "SE": ("Sensor", "Cảm biến"),
    "SV": ("Solenoid valve", "Van điện từ"),
    "PSV": ("Proportional solenoid valve", "Van điện từ tỷ lệ"),
    "SW": ("Switch", "Công tắc"),
    "CN": ("Connector", "Đầu nối"),
}

# Mã cụ thể từ Electric Equipment List (23.1.2) — EN → VI semantic
EQUIPMENT_NAMES: Dict[str, Tuple[str, str]] = {
    # Controller
    "C-1": ("MECHATRO CONTROLLER", "Bộ điều khiển Mechatro"),
    "C-2": ("GAUGE CLUSTER", "Cụm đồng hồ"),
    "C-4": ("AIR CONDITIONER CONTROLLER", "Bộ điều khiển điều hòa"),
    # Diode
    "D-1": ("DIODE", "Điode"),
    "D-2": ("DIODE", "Điode"),
    "D-3": ("DIODE", "Điode"),
    "D-4": ("DIODE", "Điode"),
    "D-5": ("DIODE", "Điode"),
    "D-6": ("DIODE", "Điode"),
    "D-7": ("DIODE", "Điode"),
    # Electric fittings
    "E-1": ("FUSE BOX", "Hộp cầu chì"),
    "E-2": ("ALTERNATOR", "Máy phát điện"),
    "E-3": ("HOUR METER", "Đồng hồ giờ"),
    "E-5": ("HORN", "Còi"),
    "E-6": ("CIGARETTE LIGHTER", "Bật lửa"),
    "E-7": ("TUNER AM & FM", "Dàn thu sóng AM/FM"),
    "E-8": ("SPEAKER LEFT", "Loa trái"),
    "E-9": ("SPEAKER RIGHT", "Loa phải"),
    "E-10": ("RECEIVER DRIER", "Bình sấy môi chất lạnh"),
    "E-11": ("AIR COMPRESSOR CLUTCH", "Ly hợp máy nén khí điều hòa"),
    "E-14": ("BATTERY", "Ắc quy"),
    "E-15": ("POWER SOCKET", "Ổ cắm điện"),
    "E-17": ("CONVERTER", "Bộ chuyển đổi"),
    "E-20": ("BUZZER", "Còi buzzer"),
    "E-21": ("COUNTER", "Bộ đếm"),
    # Light
    "L-1": ("BOOM WORKING LIGHT LEFT", "Đèn làm việc cần trái"),
    "L-2": ("BOOM WORKING LIGHT RIGHT", "Đèn làm việc cần phải"),
    "L-3": ("ROOM LIGHT", "Đèn phòng"),
    "L-4": ("CAB WORKING LIGHT FRONT", "Đèn làm việc buồng lái trước"),
    "L-5": ("CAB WORKING LIGHT REAR", "Đèn làm việc buồng lái sau"),
    "L-6": ("CAB WORKING LIGHT (OPT.)", "Đèn làm việc buồng lái (tùy chọn)"),
    "L-7": ("ENGINE ROOM LIGHT", "Đèn khoang động cơ"),
    "L-8": ("CAB WORKING LIGHT", "Đèn làm việc buồng lái"),
    "L-9": ("CAB WORKING LIGHT", "Đèn làm việc buồng lái"),
    "L-10": ("ROTARY LIGHT", "Đèn xoay"),
    "L-11": ("STARTER ALARM LAMP", "Đèn báo máy khởi động"),
    # Motor
    "M-1": ("STARTER MOTOR", "Mô-tơ khởi động"),
    "M-2": ("GOVERNOR MOTOR", "Mô-tơ điều tốc"),
    "M-3": ("WIPER MOTOR", "Mô-tơ gạt mưa"),
    "M-4": ("WASHER MOTOR", "Mô-tơ phun nước rửa"),
    "M-8": ("ROOF WIPER MOTOR (OPT.)", "Mô-tơ gạt mưa trần (tùy chọn)"),
    "M-9": ("FUEL PUMP", "Bơm nhiên liệu"),
    # Relay
    "R-1": ("STARTER RELAY", "Rơ-le khởi động"),
    "R-3": ("GLOW RELAY", "Rơ-le bugi sấy"),
    "R-4": ("SAFETY RELAY", "Rơ-le an toàn"),
    "R-5": ("AUTO IDLE STOP RELAY", "Rơ-le dừng nhả ga tự động"),
    "R-6": ("AUTO IDLE STOP RELAY 2", "Rơ-le dừng nhả ga tự động 2"),
    "R-7": ("HORN RELAY", "Rơ-le còi"),
    "R-8": ("WORK LIGHT RELAY", "Rơ-le đèn làm việc"),
    "R-10": ("WASHER RELAY", "Rơ-le phun nước rửa"),
    "R-13": ("CAB WORK LIGHT RELAY 2", "Rơ-le đèn làm việc buồng lái 2"),
    "R-20": ("GLOW PLUG RELAY 2", "Rơ-le bugi sấy 2"),
    "R-22": ("WIPER RELAY", "Rơ-le gạt mưa"),
    "R-23": ("COUNTERCLOCKWISE RELAY", "Rơ-le quay ngược"),
    "R-28": ("AUTO IDLE RELAY", "Rơ-le nhả ga tự động"),
    "R-29": ("AUTO IDLE RELAY 2", "Rơ-le nhả ga tự động 2"),
    "R-30": ("TIMER UNIT", "Bộ đếm thời gian"),
    "R-31": ("TIMER RELAY", "Rơ-le hẹn giờ"),
    # Sensor (cảm biến vị trí cần gạt / biến trở)
    "SE-1": ("BUCKET DIGGING", "Cảm biến đào gầu"),
    "SE-2": ("BUCKET DUMP", "Cảm biến đổ gầu"),
    "SE-3": ("BOOM RAISING", "Cảm biến nâng cần"),
    "SE-4": ("BOOM LOWER", "Cảm biến hạ cần"),
    "SE-5": ("SWING", "Cảm biến quay toa"),
    "SE-7": ("ARM IN", "Cảm biến tay gầu vào"),
    "SE-8": ("ARM OUT", "Cảm biến tay gầu ra"),
    "SE-9": ("TRAVEL RIGHT", "Cảm biến di chuyển phải"),
    "SE-10": ("TRAVEL LEFT", "Cảm biến di chuyển trái"),
    "SE-13": ("ENG.SPEED SENSOR", "Cảm biến tốc độ động cơ"),
    "SE-14": ("COOLANT THERMO SENSOR", "Cảm biến nhiệt nước làm mát"),
    "SE-15": ("FUEL SENSOR", "Cảm biến mức nhiên liệu"),
    "SE-16": ("ACCEL POTENTIO", "Biến trở gia tốc"),
    # Solenoid valve
    "SV-1": ("SWING PARKING SOL.", "Van điện từ phanh quay toa"),
    "SV-2": ("DOUBLE", "Van điện từ kép"),
    "SV-4": ("WIPER SOL.", "Van điện từ gạt mưa"),
    "SV-6": ("CLOCKWISE SOL.", "Van điện từ quay thuận"),
    "SV-7": ("COUNTERCLOCKWISE SOL.", "Van điện từ quay ngược"),
    "SV-8": ("PQ SHIFT SOL.", "Van điện từ chuyển số PQ"),
}

EQUIP_CODE = re.compile(
    r"^(C|D|E|L|M|R|SE|SV|PSV|SW|CN)-\d+(?:\.\d+)?[A-Z]?$",
    re.I,
)

# Chuẩn hóa tên EN để so khớp mô tả OCR (dính chữ, viết tắt)
def _norm_equip_name(text: str) -> str:
    t = re.sub(r"[\x00-\x1f]", " ", text.upper())
    t = re.sub(r"\.", " ", t)
    t = re.sub(r"\bSOL\.?\b", "SOLENOID", t)
    t = re.sub(r"\bPOTENTIO\b", "POTENTIOMETER", t)
    t = re.sub(r"\bCONTERCLOCKWISE\b", "COUNTERCLOCKWISE", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def lookup_equipment_code(code: str) -> Optional[Tuple[str, str]]:
    """Trả về (name_en, name_vi) nếu có trong danh mục."""
    key = code.strip().upper()
    # Chuẩn hóa L-1.1 → L-1
    key = re.sub(r"(\d+)\.\d+", r"\1", key)
    if key in EQUIPMENT_NAMES:
        return EQUIPMENT_NAMES[key]
    return None


def translate_labeled_component(code: str, desc_en: str = "") -> Optional[str]:
    """
    Dịch semantic nhãn dạng SE-1:BUCKET DIGGING.
    Ưu tiên tra cứu danh mục thiết bị; fallback nhóm (SE → cảm biến).
    """
    code = code.strip().upper()
    desc_en = re.sub(r"[\x00-\x1f]", " ", desc_en).strip().rstrip(".")
    hit = lookup_equipment_code(code)
    if hit:
        name_en, name_vi = hit
        if not desc_en:
            return name_vi
        nd = _norm_equip_name(desc_en)
        ne = _norm_equip_name(name_en)
        if nd == ne or nd in ne or ne in nd:
            return name_vi
        # Cùng mã — ưu tiên tên danh mục (semantic) hơn OCR lệch
        return name_vi
    return None


def vi_meaning_only(text_vi: str) -> str:
    """Bỏ prefix mã (SE-14: / C-1 —) — chỉ giữ nghĩa tiếng Việt."""
    t = text_vi.strip()
    m = re.match(
        r"^(?:C|D|E|L|M|R|SE|SV|PSV|SW|CN)-\d+(?:\.\d+)?[A-Z]?\s*[:—-]\s*(.+)$",
        t,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m2 = re.match(r"^([A-Z]{1,4}-\d+[A-Z]?)\s*\((.+)\)$", t, re.I)
    if m2:
        return m2.group(2).strip()
    return t


def group_for_code(code: str) -> Optional[Tuple[str, str]]:
    key = code.strip().upper()
    for prefix in sorted(GROUP_PREFIX, key=len, reverse=True):
        if key.startswith(prefix + "-"):
            return GROUP_PREFIX[prefix]
    return None


def format_semantic_label(code: str, name_en: str, name_vi: str) -> str:
    """Hiển thị: C-1 — bộ điều khiển Mechatro"""
    return f"{code.upper()} — {name_vi}"


def enrich_pure_code(code: str) -> Optional[str]:
    """
    Tra cứu semantic cho mã thuần (C-1, E-14).
    Fallback: C-1 → C-1 (bộ điều khiển) nếu biết nhóm.
    """
    code = code.strip().upper()
    hit = lookup_equipment_code(code)
    if hit:
        _, name_vi = hit
        return format_semantic_label(code, hit[0], name_vi)
    grp = group_for_code(code)
    if grp:
        _, grp_vi = grp
        return f"{code} ({grp_vi})"
    return None


def is_equipment_index_code(text: str) -> bool:
    return bool(EQUIP_CODE.match(text.strip()))


EQUIPMENT_LIST_INTRO_VI = (
    "Bảng dưới liệt kê mã thiết bị trong Danh mục thiết bị điện (mục 23.1.2). "
    "Mỗi mã = Nhóm + số thứ tự (vd. C-1 = thiết bị số 1 nhóm Controller). "
    "Tra cứu đầy đủ tên tiếng Anh và số phụ tùng trên hình gốc phía trên."
)
