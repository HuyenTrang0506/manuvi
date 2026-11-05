"""Dịch nhãn sơ đồ mạch điện — từng dòng, không khớp substring trên cả cụm."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from src.ingest.text_structure import fix_glued_tokens
from src.models import Glossary, LabelNote
from src.translate.equipment_list import (
    enrich_pure_code,
    is_equipment_index_code,
    lookup_equipment_code,
    translate_labeled_component,
)
from src.translate.ocr_guess import guess_from_context, is_ocr_suspicious
from src.translate.wire_labels import WIRE_GAUGE_ONLY, format_wire_vi, is_wire_label

PART_NUM = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
LABELED_CODE = re.compile(r"^([A-Z]{1,4}-\d+[A-Z]?)\s*:\s*(.+)$", re.I)
CONNECTOR = re.compile(r"^[A-Z]-\d+[A-Z]?$")
RELAY_CODE = re.compile(r"^R-\d+$")
SW_CODE = re.compile(r"^SW-\d+$")
SV_CODE = re.compile(r"^SV-\d+$")
MOTOR_CODE = re.compile(r"^M-\d+$")
LAMP_CODE = re.compile(r"^L-\d+$")
PURE_CODE = re.compile(r"^[A-Z]{1,4}-\d+$")
WIRE_COLOR = re.compile(r"^[WGRBYBL]{1,3}$")
AMP = re.compile(r"^\d+A$")
VOLT = re.compile(r"^\d+V$")
FIG_OR_SEC = re.compile(r"^(Fig\.|23\.|\d{2}-\d+$)", re.I)
SEC_HEADER = re.compile(
    r"^(23\.\d+(?:\.\d+)?(?:-\d+)?)\s+(.+)$",
    re.I,
)
CABLE_LINE = re.compile(
    r"^(.+?)\s*:\s*([A-Z0-9]+)$",
    re.I,
)
NOISE = re.compile(
    r"^(Copyright|Kobelco|NamePart|No\.\d|^\d{6,}$|EEE100|RXD|TXD|1PAMYAZAKI|PAMYAZAKI)",
    re.I,
)

# Dịch cụm — dài trước
PHRASES: Dict[str, str] = {
    # ── Equipment list / panel (nguyên cụm — chống PRESSURE + cảm biến) ──
    "pressure sensor (low) : bucket digging": "cảm biến áp suất (thấp) : đào gầu",
    "pressure sensor (low) : bucket dump": "cảm biến áp suất (thấp) : đổ gầu",
    "pressure sensor (low) : boom down": "cảm biến áp suất (thấp) : hạ cần",
    "pressure sensor (low) : boom up": "cảm biến áp suất (thấp) : nâng cần",
    "pressure sensor (low) : arm in": "cảm biến áp suất (thấp) : tay gầu vào",
    "pressure sensor (low) : arm out": "cảm biến áp suất (thấp) : tay gầu ra",
    "pressure sensor (low) : travel left": "cảm biến áp suất (thấp) : di chuyển trái",
    "pressure sensor (low) : travel right": "cảm biến áp suất (thấp) : di chuyển phải",
    "pressure sensor (low) : swing": "cảm biến áp suất (thấp) : quay toa",
    "pressure sensor (low)": "cảm biến áp suất (thấp)",
    "opt. pedal pressure switch assy (1)": "lắp ráp công tắc áp bàn đạp tùy chọn (1)",
    "opt. pedal pressure switch assy (2)": "lắp ráp công tắc áp bàn đạp tùy chọn (2)",
    "opt. pedal pressure switch assy (3)": "lắp ráp công tắc áp bàn đạp tùy chọn (3)",
    "opt. pedal pressure switch assy": "lắp ráp công tắc áp bàn đạp tùy chọn",
    "opt.pedal press. swich (1)": "công tắc áp bàn đạp tùy chọn (1)",
    "opt.pedal press. swich (2)": "công tắc áp bàn đạp tùy chọn (2)",
    "opt.pedal press.swich (3)": "công tắc áp bàn đạp tùy chọn (3)",
    "opt. pedal press.": "áp bàn đạp tùy chọn",
    "auto idle stop select switch": "công tắc chọn dừng không tải tự động",
    "auto idle stop select sw. b": "công tắc chọn dừng không tải tự động B",
    "auto idle stop select sw": "công tắc chọn dừng không tải tự động",
    "double·single select sol. (opt.)": "van điện từ chọn kép/đơn (tùy chọn)",
    "double·single select switch": "công tắc chọn kép/đơn",
    "double single select sol": "van điện từ chọn kép/đơn",
    "double single select switch": "công tắc chọn kép/đơn",
    "e/g speed sensor": "cảm biến tốc độ động cơ",
    "lever lock sol.": "van điện từ khóa cần",
    "lever lock sol": "van điện từ khóa cần",
    "lever lock switch": "công tắc khóa cần",
    "inter lock switch": "công tắc liên động",
    "travel boost-up sol.": "van điện từ tăng lực di chuyển",
    "travel boost-up sol": "van điện từ tăng lực di chuyển",
    "travel 1·2-speed sol.": "van điện từ tốc độ di chuyển 1-2",
    "travel 1·2-speed sol": "van điện từ tốc độ di chuyển 1-2",
    "two-speed select sol.": "van điện từ chọn 2 tốc độ",
    "two-speed select sol": "van điện từ chọn 2 tốc độ",
    "two-speed select switch": "công tắc chọn 2 tốc độ",
    "travel two-speed relay": "rơ-le 2 tốc độ di chuyển",
    "select sol.": "van điện từ chọn",
    "select sol": "van điện từ chọn",
    "curl code harness": "bộ dây mã cuốn",
    "curl code": "mã cuốn",
    "wiper motor on the roof (opt.)": "mô-tơ gạt mưa trên nóc (tùy chọn)",
    "wiper motor on the roof": "mô-tơ gạt mưa trên nóc",
    "roof wiper motor (opt.)": "mô-tơ gạt mưa trần (tùy chọn)",
    "roof wiper switch (opt.)": "công tắc gạt mưa trần (tùy chọn)",
    "work light switch (opt.)": "công tắc đèn làm việc (tùy chọn)",
    "work light sw.(opt)": "công tắc đèn làm việc (tùy chọn)",
    "cab work light (opt.)": "đèn làm việc cabin (tùy chọn)",
    "cab working light front (opt.)": "đèn làm việc cabin phía trước (tùy chọn)",
    "rotary light (opt.)": "đèn xoay (tùy chọn)",
    "horn sw. (r.h) (opt.)": "công tắc còi (phải) (tùy chọn)",
    "clockwise sol. (opt.)": "van điện từ quay thuận (tùy chọn)",
    "clockwise relay (opt.)": "rơ-le quay thuận (tùy chọn)",
    "clockwise switch (opt.)": "công tắc quay thuận (tùy chọn)",
    "counterclockwise sol. (opt.)": "van điện từ quay ngược (tùy chọn)",
    "counterclockwise relay (opt.)": "rơ-le quay ngược (tùy chọn)",
    "counterclockwise switch (opt.)": "công tắc quay ngược (tùy chọn)",
    "wiper relay assy (opt.)": "lắp ráp rơ-le gạt mưa (tùy chọn)",
    "electrical circuit diagram and equipment list": "sơ đồ mạch điện và danh mục thiết bị",
    "engine harness assy": "lắp ráp bộ dây động cơ",
    "engine harness": "bộ dây động cơ",
    "cab instrument harness": "bộ dây đồng hồ cabin",
    "left instrument harness": "bộ dây đồng hồ trái",
    "right instrument harness": "bộ dây đồng hồ phải",
    "cab harness assy (1/2)": "lắp ráp bộ dây cabin (1/2)",
    "cab harness assy (2/2)": "lắp ráp bộ dây cabin (2/2)",
    "cab harness assy": "lắp ráp bộ dây cabin",
    "upper harness assy  (1/4)": "lắp ráp bộ dây phần trên (1/4)",
    "upper harness assy  (2/4)": "lắp ráp bộ dây phần trên (2/4)",
    "upper harness assy  (3/4)": "lắp ráp bộ dây phần trên (3/4)",
    "upper harness assy  (4/4)": "lắp ráp bộ dây phần trên (4/4)",
    "cab switch instal": "lắp công tắc cabin",
    "detail of compressor wiring": "chi tiết dây máy nén",
    "cabin in (1/2)": "cabin vào (1/2)",
    "cabin in (2/2)": "cabin vào (2/2)",
    "electrical circuit diagram": "sơ đồ mạch điện",
    "electrical equipment list": "danh mục thiết bị điện",
    "electric equipment list": "danh mục thiết bị điện",
    "installed on the spot": "lắp tại chỗ",
    "instal led on the spot": "lắp tại chỗ",
    "(electric component for ais)": "(linh kiện điện cho AIS)",
    "electric component for ais": "linh kiện điện cho AIS",
    "air conditioner sw.": "công tắc điều hòa",
    "right front harness": "bộ dây phía trước phải",
    "cab right front harness": "bộ dây phía trước phải cabin",
    "left instrument panel harness": "bộ dây bảng đồng hồ trái",
    "right instrument panel harness": "bộ dây bảng đồng hồ phải",
    "instrument panel harness": "bộ dây bảng đồng hồ",
    "battery cable": "cáp ắc quy",
    "starter cable": "cáp máy khởi động",
    "harness & cable list": "danh mục bộ dây và cáp",
    "harness and cable list": "danh mục bộ dây và cáp",
    "cab working light (front 3)": "đèn làm việc cabin (phía trước 3)",
    "cab working light (front 4)": "đèn làm việc cabin (phía trước 4)",
    "cab working light front": "đèn làm việc cabin phía trước",
    "horn switch (left)": "công tắc còi (trái)",
    "horn sw itch (left)": "công tắc còi (trái)",
    "name plate instal (electric component for auto idle stop)": (
        "lắp biển tên (linh kiện điện cho dừng không tải tự động)"
    ),
    "name plate instal": "lắp biển tên",
    "fault on swing parking selector valve": "lỗi van chọn phanh quay toa",
    "controller error code display": "hiển thị mã lỗi bộ điều khiển",
    "error code display": "hiển thị mã lỗi",
    "grounding for which wire color is not shown to": (
        "tiếp đất mà không ghi màu dây được"
    ),
    "be performed with component case.": "thực hiện bằng vỏ thiết bị.",
    "front": "phía trước",
    "panel": "bảng",
    "cable": "cáp",
    "list": "danh mục",
    "instal": "lắp",
    "electric": "điện",
    "component": "linh kiện",
    "air conditioner sw": "công tắc điều hòa",
    "air conditioner sw": "công tắc điều hòa",
    "air commpressor clutch": "ly hợp máy nén khí",
    "air compressor clutch": "ly hợp máy nén khí",
    "dc-dc converter": "bộ chuyển đổi DC-DC",
    "converter": "bộ chuyển đổi",
    "tuner am": "dàn thu sóng AM",
    "speaker right": "loa phải",
    "speaker left": "loa trái",
    "clockwise sw.": "công tắc quay thuận",
    "clockwise sw": "công tắc quay thuận",
    "clockwise sol.": "van điện từ quay thuận",
    "clockwise sol": "van điện từ quay thuận",
    "conterclockwise relay": "rơ-le quay ngược",
    "counterclockwise relay": "rơ-le quay ngược",
    "hand control for rotation": "bộ điều khiển tay cho quay",
    "work light relay": "rơ-le đèn làm việc",
    "cab work light relay": "rơ-le đèn làm việc cabin",
    "horn relay": "rơ-le còi",
    "fuel pump": "bơm nhiên liệu",
    "horn sw.": "công tắc còi",
    "horn sw": "công tắc còi",
    "horn": "còi",
    "mechatro contoller": "bộ điều khiển Mechatro",
    "mechatro controller": "bộ điều khiển Mechatro",
    "roof wiper sw.": "công tắc gạt mưa trần",
    "roof wiper sw": "công tắc gạt mưa trần",
    "wiper sw.": "công tắc gạt mưa",
    "wiper sw": "công tắc gạt mưa",
    "gauge cluster": "cụm đồng hồ",
    "eng.oil press.sw": "công tắc áp suất dầu động cơ",
    "eng.oil press.sw.": "công tắc áp suất dầu động cơ",
    "eng.oil press sw": "công tắc áp suất dầu động cơ",
    "rotary light": "đèn xoay",
    "travel right": "di chuyển phải",
    "travel left": "di chuyển trái",
    "work light": "đèn làm việc",
    "auto idle": "nhả ga tự động",
    "under floor harness": "bộ dây sàn dưới",
    "cab instrument harness": "bộ dây đồng hồ buồng lái",
    "auto idle stop relay": "rơ-le dừng nhả ga tự động",
    "auto idle stop relay 1": "rơ-le dừng nhả ga tự động 1",
    "auto idle stop relay 2": "rơ-le dừng nhả ga tự động 2",
    "auto idle stop": "dừng nhả ga tự động",
    "glow plug": "bugi sấy",
    "mounting cab work light": "lắp đèn làm việc buồng lái",
    "instrument harness": "bộ dây đồng hồ",
    "instrument": "đồng hồ",
    "idle": "nhả ga",
    "stop": "dừng",
    "auto": "tự động",
    "plug": "bugi",
    "dc-dc converter": "bộ chuyển đổi DC-DC",
    "mode select switch": "công tắc chọn chế độ",
    "auto accel release switch": "công tắc nhả ga tự động",
    "socket": "ổ cắm",
    "left instrument harness": "bộ dây đồng hồ trái",
    "right instrument harness": "bộ dây đồng hồ phải",
    "ground cable": "cáp tiếp đất",
    "cab harness assy": "lắp ráp bộ dây cabin",
    "electrical system": "hệ thống điện",
    "battery relay cable": "cáp rơ-le ắc quy",
    "frame ground cable": "cáp tiếp đất khung",
    "room light harness": "bộ dây đèn phòng",
    "e/g room light harness": "bộ dây đèn khoang động cơ",
    "upper electric assy": "lắp ráp điện phần trên",
    "upper harness assy": "lắp ráp bộ dây phần trên",
    "double": "kép",
    "battery": "ắc quy",
    "starter": "máy khởi động",
    "alternator": "máy phát điện",
    "relay": "rơ-le",
    "sensor": "cảm biến",
    "harness": "bộ dây điện",
    "connector": "đầu nối",
    "solenoid": "van điện từ",
    "switch": "công tắc",
    "ground": "tiếp đất",
    "fuse": "cầu chì",
    "motor": "mô-tơ",
    "pump": "bơm",
    "light": "đèn",
    "lamp": "đèn",
    "controller": "bộ điều khiển",
    "converter": "bộ chuyển đổi",
    "speaker": "loa",
    "tuner": "dàn thu sóng",
    "service diagnosis display": "hiển thị chẩn đoán dịch vụ",
    "hardware check sw": "công tắc kiểm tra phần cứng",
    "internal adjustment jp sw": "công tắc JP điều chỉnh nội bộ",
    "pedal pressure sw. (1)(opt.)": "công tắc áp bàn đạp (1)(tùy chọn)",
    "TRAVEL ALARM": "CẢNH BÁO DI CHUYỂN",
    "POWER SOCKET": "Ổ CẮM ĐIỆN",
    "TIMER UNIT": "BỘ ĐẾM THỜI GIAN",
    "HOUR METER": "ĐỒNG HỒ GIỜ",
    "CIGAR LIGHTER": "BẬT LỬA",
    "GLOW PLUG": "BUGI SẤY",
    "SERVICE DIAGNOSIS DISPLAY": "HIỂN THỊ CHẨN ĐOÁN DỊCH VỤ",
    "HARDWARE CHECK SW": "CÔNG TẮC KIỂM TRA PHẦN CỨNG",
    "INTERNAL ADJUSTMENT JP SW": "CÔNG TẮC JP ĐIỀU CHỈNH NỘI BỘ",
    "23.2HARNESS": "23.2 BỘ DÂY ĐIỆN",
    "23.2.1HARNESS & CABLE LIST": "23.2.1 DANH MỤC BỘ DÂY VÀ CÁP",
    "23.2.3CONTROLLER ASSY": "23.2.3 LẮP RÁP BỘ ĐIỀU KHIỂN",
    "23.2.4INSTRUMENT PANEL ASSY": "23.2.4 LẮP RÁP BẢNG ĐỒNG HỒ",
    "23.2.5UPPER ELECTRIC ASSY (1/2)": "23.2.5 LẮP RÁP ĐIỆN PHẦN TRÊN (1/2)",
    "23.2.10BATTERY : 2411U25": "23.2.10 ẮC QUY : 2411U25",
    "23.2.15MOUNTING CAB WORK LIGHT": "23.2.15 LẮP ĐÈN LÀM VIỆC TRÊN CABIN",
    "mounting cab work light": "lắp đèn làm việc trên cabin",
    "battery ground cable": "cáp tiếp đất ắc quy",
    "frame ground cable": "cáp tiếp đất khung",
    "under floor harness": "bộ dây sàn dưới",
    "device item": "mục thiết bị",
    "travel alarm": "cảnh báo di chuyển",
    "power socket": "ổ cắm điện",
    "timer unit": "bộ đếm thời gian",
    "hour meter": "đồng hồ giờ",
    "cigar lighter": "bật lửa",
    "glow plug": "bugi sấy",
    "opt. pedal press. swich": "công tắc áp bàn đạp tùy chọn",
    "opt.pedal press. swich": "công tắc áp bàn đạp tùy chọn",
    "opt.pedal press.swich": "công tắc áp bàn đạp tùy chọn",
    "opt pedal press switch": "công tắc áp bàn đạp tùy chọn",
    "pedal press. swich": "công tắc áp bàn đạp",
    "pedal press swich": "công tắc áp bàn đạp",
    "swing parking brake": "phanh đỗ quay toa",
    "swing parking sol.": "van điện từ phanh quay toa",
    "swing parking sol": "van điện từ phanh quay toa",
    "boom working light left": "đèn làm việc cần trái",
    "boom working light right": "đèn làm việc cần phải",
    "cab working light": "đèn làm việc buồng lái",
    "cab work light": "đèn làm việc buồng lái",
    "room light": "đèn phòng",
    "wiper motor": "mô-tơ gạt mưa",
    "washer relay": "rơ-le phun nước rửa",
    "washer motor": "mô-tơ phun nước rửa",
    "fuel sensor": "cảm biến mức nhiên liệu",
    "coolant thermo sensor": "cảm biến nhiệt nước làm mát",
    "glow relay": "rơ-le bugi sấy",
    "safety relay": "rơ-le an toàn",
    "bucket digging": "cảm biến đào gầu",
    "bucket dump": "cảm biến đổ gầu",
    "boom raising": "cảm biến nâng cần",
    "boom lower": "cảm biến hạ cần",
    "arm in": "cảm biến tay gầu vào",
    "arm out": "cảm biến tay gầu ra",
    "accel potentiometer": "biến trở gia tốc",
    "accel potentio": "biến trở gia tốc",
    "pq shift sol.": "van điện từ chuyển số PQ",
    "pq shift sol": "van điện từ chuyển số PQ",
    "eng.speed sensor": "cảm biến tốc độ động cơ",
    "eng speed sensor": "cảm biến tốc độ động cơ",
    "digging": "đào gầu",
    "raising": "nâng",
    "lowering": "hạ",
    "dump": "đổ gầu",
    "potentiometer": "biến trở",
    "potentio": "biến trở gia tốc",
    "shift sol.": "van điện từ chuyển số",
    "shift sol": "van điện từ chuyển số",
    "sol.": "van điện từ",
    "switch assy": "lắp ráp công tắc",
    "engine room light sw.": "công tắc đèn khoang động cơ",
    "engine room light sw": "công tắc đèn khoang động cơ",
    "rotary light sw.": "công tắc đèn xoay",
    "rotary light sw": "công tắc đèn xoay",
    "cab work light relay": "rơ-le đèn làm việc cabin",
    "timer unit.": "bộ đếm thời gian",
    "timer unit": "bộ đếm thời gian",
    "boom": "cần",
    "working": "làm việc",
    "left": "trái",
    "right": "phải",
    "room": "phòng",
    "cab": "buồng lái",
    "wiper": "gạt mưa",
    "washer": "phun nước rửa",
    "glow": "sấy",
    "safety": "an toàn",
    "fuel": "nhiên liệu",
    "coolant": "nước làm mát",
    "thermo": "nhiệt",
    "assy": "lắp ráp",
    "engine": "động cơ",
    "rotary": "xoay",
    "unit": "bộ",
    "timer": "đếm thời gian",
    "opt.": "tùy chọn",
    "opt": "tùy chọn",
    "press.": "áp",
    "brake": "phanh",
    "parking": "đỗ",
    "swich": "công tắc",
    "swing": "quay toa",
    "horn": "còi",
    "ignition": "đánh lửa",
    "compressor": "máy nén",
    "clutch": "ly hợp",
    "rotation": "quay",
    "clockwise": "thuận chiều kim đồng hồ",
    "counterclockwise": "ngược chiều kim đồng hồ",
    # Đèn báo trên cụm đồng hồ C-2 (gauge cluster)
    "illumination": "đèn chiếu sáng bảng đồng hồ",
    "charge": "đèn báo nạp (máy phát)",
    "eng. oil press.": "đèn báo áp suất dầu động cơ",
    "eng.oil press.": "đèn báo áp suất dầu động cơ",
    "eng. oil press": "đèn báo áp suất dầu động cơ",
    "eng. water temp.": "đèn báo nhiệt nước làm mát",
    "eng.water temp.": "đèn báo nhiệt nước làm mát",
    "eng. water temp": "đèn báo nhiệt nước làm mát",
    "coolant thermo indicator": "đồng hồ / chỉ thị nhiệt nước làm mát",
    "air cleaner": "đèn báo lọc gió",
    "fuel level": "đèn báo mức nhiên liệu",
    "fuel meter": "đồng hồ nhiên liệu",
    "travel two-speed": "đèn báo tốc độ di chuyển 2",
    "swing parking": "đèn báo phanh quay toa",
    "cpu allergy": "đèn báo lỗi CPU",
    "overload": "đèn báo quá tải",
    "overload indicate sw.": "công tắc đèn báo quá tải",
    "overload indicate sw": "công tắc đèn báo quá tải",
    "controller earth": "tiếp đất bộ điều khiển",
    "travel two-speed sol.": "van điện từ tốc độ di chuyển 2",
    "travel two-speed sol": "van điện từ tốc độ di chuyển 2",
    "travel two-speed relay": "rơ-le tốc độ di chuyển 2",
    "travel two-speed": "tốc độ di chuyển 2",
    "auto accel release sw.": "công tắc nhả ga tự động",
    "auto accel release sw": "công tắc nhả ga tự động",
    "sequence box assy.": "hộp tuần tự (lắp ráp)",
    "sequence box assy": "hộp tuần tự (lắp ráp)",
    "p1 by-pass cut propotional sol.": "van tỷ lệ cắt bypass P1",
    "p2 by-pass cut propotional sol.": "van tỷ lệ cắt bypass P2",
    "travel priority propotional sol.": "van tỷ lệ ưu tiên di chuyển",
    "alarm lamp": "đèn báo",
    "back light": "đèn nền",
    "air filter restriction sw.": "công tắc tắc lọc gió",
    "air filter restriction sw": "công tắc tắc lọc gió",
    "eng.water temp.sw.": "công tắc nhiệt nước làm mát",
    "eng. water temp. sw.": "công tắc nhiệt nước làm mát",
    "starter key sw.": "công tắc chìa khởi động",
    "starter key sw": "công tắc chìa khởi động",
    "horn sw.(left)": "công tắc còi (trái)",
    "horn sw.(right)": "công tắc còi (phải)",
    "horn sw. (left)": "công tắc còi (trái)",
    "horn sw. (right)": "công tắc còi (phải)",
    "double, single": "chọn kép / đơn",
    "double, single select sw.": "công tắc chọn kép/đơn",
    "cabin": "buồng lái",
    "mounting cab work light": "lắp đèn làm việc buồng lái",
    "mounting boom light": "lắp đèn cần",
    "mounting cab": "lắp buồng lái",
    "mounting light": "lắp đèn",
    "mounting fuel pump": "lắp bơm nhiên liệu",
    "washer tank": "bình nước rửa",
    "washer tank & pump": "bình nước rửa & bơm",
    "window washer tank & pump": "bình nước rửa kính & bơm",
    "window washer tank and pump": "bình nước rửa kính & bơm",
    "accel potentio dial": "núm xoay biến trở gia tốc",
    "accel potentiometer dial": "núm xoay biến trở gia tốc",
    "accel potentio meter": "biến trở gia tốc",
    "kye switch": "công tắc chìa khóa",
    "key switch": "công tắc chìa khóa",
    "cigalette lighter": "bật lửa",
    "cigarette lighter": "bật lửa",
    "overload indicate switch": "công tắc báo quá tải",
    "rotary light switch": "công tắc đèn xoay",
    "auto accel release switch": "công tắc nhả ga tự động",
    "r/h panel assy": "lắp ráp bảng bên phải",
    "instrument panel assy": "lắp ráp bảng đồng hồ",
    "antenna": "ăng-ten",
    "tuner": "đầu thu sóng",
    "hour meter": "đồng hồ giờ",
    "cover assy": "lắp ráp nắp",
    "speaker": "loa",
    "socket": "ổ cắm",
    "gauge cluster": "cụm đồng hồ",
    "mode select switch": "công tắc chọn chế độ",
    "dc-dc converter": "bộ chuyển đổi DC-DC",
    "air conditioner": "điều hòa",
    "receiver tank": "bình chứa môi chất",
    "cab harness assy": "lắp ráp bộ dây buồng lái",
    "cab work light relay": "rơ-le đèn làm việc buồng lái",
    "switch assy (back light)": "lắp ráp công tắc (đèn nền)",
    "sw assy (back light)": "lắp ráp công tắc (đèn nền)",
    "tarminal": "đầu cực",
    "terminal": "đầu cực",
    "tank": "bình",
    "part no.": "mã phụ tùng",
    "part no": "mã phụ tùng",
    "q'ty": "SL",
    "qty": "SL",
    "no.": "STT.",
    "name": "tên",
    "mounting boom work light": "lắp đèn làm việc cần",
    "mounting deck light": "lắp đèn sàn",
    "mounting work light": "lắp đèn làm việc",
}

# Tín hiệu / màu dây — giữ mã + chú thích ngắn
SIGNAL_ABBR: Dict[str, str] = {
    "GND": "GND (tiếp đất)",
    "IGN": "IGN (đánh lửa)",
    "MEM": "MEM (nhớ)",
    "ACC": "ACC (tiếp nguồn)",
    "BP": "BP",
    "OL": "OL",
    "W": "W (trắng)",
    "G": "G (xanh lá)",
    "R": "R (đỏ)",
    "B": "B (đen)",
    "Y": "Y (vàng)",
    "RB": "RB (đỏ-đen)",
    "WGR": "WGR",
}

PHRASES_SORTED = sorted(PHRASES.items(), key=lambda x: -len(x[0]))

# Gộp nhãn sơ đồ bộ dây
try:
    from src.translate.harness_callouts import HARNESS_PHRASES

    PHRASES.update(HARNESS_PHRASES)
    PHRASES_SORTED = sorted(PHRASES.items(), key=lambda x: -len(x[0]))
except Exception:
    pass


def _lookup_word(wl: str, glossary: Glossary) -> Optional[str]:
    if wl in PHRASES:
        return PHRASES[wl]
    gl = _lookup_glossary(wl, glossary)
    if gl:
        return gl
    for en, vi in PHRASES_SORTED:
        if wl == en.lower().rstrip("."):
            return vi
    return None


def is_mixed_translation(text_vi: str) -> bool:
    from src.qa.label_validator import is_mixed_translation as _mix

    return _mix(text_vi)


def _normalize_component_desc(desc: str) -> str:
    """OPT.PEDAL PRESS. SWICH → opt pedal press swich"""
    d = desc.strip()
    d = re.sub(r"\bSWICH\b", "SWITCH", d, flags=re.I)
    d = d.replace("·", " ").replace("—", "-")
    d = re.sub(r"\.", " ", d)
    d = re.sub(r"\s+", " ", d).strip().lower()
    return d


def _lookup_glossary(term: str, glossary: Glossary) -> Optional[str]:
    low = term.lower().strip().rstrip(".")
    for e in glossary.entries:
        if e.term_en.lower().rstrip(".") == low and e.term_vi:
            return e.term_vi
        for v in e.variants_en:
            if v.lower().rstrip(".") == low and e.term_vi:
                return e.term_vi
    return None


def _greedy_phrase_replace(text: str) -> str:
    """Thay cụm dài nhất trước — chỉ khớp biên từ (tránh electric⊂electrical)."""
    out = _normalize_component_desc(text)
    for en, vi in PHRASES_SORTED:
        en_norm = _normalize_component_desc(en)
        if len(en_norm) < 5:
            continue
        pat = re.compile(rf"(?<![\w]){re.escape(en_norm)}(?![\w])", re.I)
        if pat.search(out):
            out = pat.sub(f" {vi} ", out)
    out = re.sub(r"\s+", " ", out).strip()
    return out


def _translate_description(desc: str, glossary: Glossary) -> str:
    desc = desc.strip().rstrip(".")
    if not desc:
        return ""
    norm = _normalize_component_desc(desc)
    low = desc.lower().rstrip(".")

    if low in SIGNAL_ABBR:
        return SIGNAL_ABBR[low.upper()] if desc.upper() in SIGNAL_ABBR else desc

    gl = _lookup_glossary(desc, glossary)
    if gl:
        return gl

    # Khớp nguyên cụm (dài trước)
    for en, vi in PHRASES_SORTED:
        en_norm = _normalize_component_desc(en)
        if low == en.lower().rstrip(".") or norm == en_norm:
            return vi

    # Greedy cụm con — ưu tiên hơn word-by-word
    greedy = _greedy_phrase_replace(desc)
    if greedy != norm and not is_mixed_translation(greedy):
        return greedy
    if greedy != norm and VI_CHARS_LOCAL.search(greedy):
        # còn sót EN → tiếp tục word replace trên phần còn lại
        tokens = re.split(r"([\s./\-·]+)", greedy)
        out: List[str] = []
        for tok in tokens:
            if not tok or re.match(r"^[\s./\-·]+$", tok):
                out.append(tok)
                continue
            if tok.isdigit() or re.search(r"[àáảãạăâêôơưđ]", tok, re.I):
                out.append(tok)
                continue
            wl = tok.lower().rstrip(".")
            vi = _lookup_word(wl, glossary)
            out.append(vi if vi else tok)
        result = re.sub(r"\s+", " ", "".join(out)).strip()
        if result and not is_mixed_translation(result):
            return result
        if result != desc:
            return result

    # Dịch từng từ — fallback
    tokens = re.split(r"([\s./\-·]+)", re.sub(r"\.", " ", desc))
    out2: List[str] = []
    for tok in tokens:
        if not tok or re.match(r"^[\s./\-·]+$", tok):
            out2.append(tok)
            continue
        if tok.isdigit():
            out2.append(tok)
            continue
        wl = tok.lower().rstrip(".")
        vi = _lookup_word(wl, glossary)
        out2.append(vi if vi else tok)

    result = re.sub(r"\s+", " ", "".join(out2)).strip()

    if is_mixed_translation(result):
        joined = _greedy_phrase_replace(desc)
        if joined and not is_mixed_translation(joined):
            return joined

    if result != desc:
        return result
    return desc


VI_CHARS_LOCAL = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)


PURE_CODE_LINE = re.compile(
    r"^(SW|SV|R|M|L|E|C|CN)-\d+[A-Z]?$|^[WGRBY]{1,3}$|^\d{1,4}$",
    re.I,
)


def _finalize_label(en: str, vi: str, glossary: Glossary) -> LabelNote:
    """Áp dụng OCR suspicion + đoán ngữ cảnh nếu cần."""
    en = en.strip()
    if PURE_CODE_LINE.match(en):
        return LabelNote(text_en=en, text_vi=vi or en, suspicion=False, guess_note=None)

    if is_ocr_suspicious(en):
        guessed, note = guess_from_context(en)
        return LabelNote(text_en=en, text_vi=guessed, suspicion=True, guess_note=note)

    if vi and is_mixed_translation(vi):
        return LabelNote(
            text_en=en,
            text_vi=vi,
            suspicion=True,
            guess_note="Dịch lẫn Anh-Việt — bổ sung cụm trong glossary hoặc Excel",
        )

    if vi == en or (vi and vi.lower() == en.lower()):
        if is_mixed_translation(vi):
            return LabelNote(
                text_en=en,
                text_vi=vi,
                suspicion=True,
                guess_note="Dịch lẫn Anh-Việt — cần sửa glossary hoặc Excel",
            )
        if re.search(r"\b(relay|harness|sensor|solenoid|switch)\b", en, re.I) and not PURE_CODE_LINE.match(en):
            return LabelNote(
                text_en=en,
                text_vi=vi,
                suspicion=True,
                guess_note="Chưa dịch hết — cần bổ sung glossary hoặc sửa trong Excel",
            )
        if re.match(r"^\d{5,}$", en):
            return LabelNote(
                text_en=en, text_vi=vi, suspicion=True, guess_note="Chuỗi số OCR — cần đối chiếu hình",
            )

    return LabelNote(text_en=en, text_vi=vi, suspicion=False, guess_note=None)


def translate_label_line(line: str, glossary: Glossary) -> str:
    line = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", line).strip()
    line = fix_glued_tokens(line)
    if not line:
        return ""

    if NOISE.search(line):
        if PART_NUM.search(line):
            return _translate_description(line, glossary) if ":" in line else line
        return ""

    # OCR dính chữ — bỏ qua nếu quá dài không có khoảng trắng hợp lý
    if len(line) > 40 and line.count(" ") < 2:
        return line

    if line.upper() in SIGNAL_ABBR:
        return SIGNAL_ABBR[line.upper()]

    # Mã dây: 1.25 WV / 0.5 BrY / WB
    if is_wire_label(line):
        wire_vi = format_wire_vi(line)
        if wire_vi:
            return f"{line} — {wire_vi}"

    # Chỉ số tiết diện (màu ở dòng kế — đã gộp lúc extract; nếu sót thì giữ nguyên)
    if WIRE_GAUGE_ONLY.match(line):
        return f"{line} — dây {line} mm²"

    # Mã danh mục thiết bị (C-1, E-14…) — tra cứu semantic, không copy y nguyên
    if is_equipment_index_code(line):
        enriched = enrich_pure_code(line)
        if enriched:
            return enriched

    if PURE_CODE.match(line) or CONNECTOR.match(line) or RELAY_CODE.match(line):
        enriched = enrich_pure_code(line)
        return enriched if enriched else line

    if WIRE_COLOR.match(line) or AMP.match(line) or VOLT.match(line):
        if is_wire_label(line):
            wire_vi = format_wire_vi(line)
            if wire_vi:
                return f"{line} — {wire_vi}"
        if line.upper() in SIGNAL_ABBR:
            return SIGNAL_ABBR[line.upper()]
        return line

    if re.match(r"^\d+$", line) and len(line) <= 3:
        return line

    m = LABELED_CODE.match(line)
    if m:
        code, desc = m.group(1).upper(), m.group(2).strip()
        desc = re.sub(r"[\x00-\x1f]", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip().rstrip(".")
        semantic = translate_labeled_component(code, desc)
        if semantic:
            return f"{code}: {semantic}"
        desc = re.sub(r"(\d+A)$", r" \1", desc, flags=re.I)
        desc_vi = _translate_description(desc, glossary)
        if desc_vi.lower() != _normalize_component_desc(desc) and desc_vi.lower() != desc.lower():
            return f"{code}: {desc_vi}"
        return f"{code}: {desc}"

    # Mã không có dấu hai chấm: "SW-13 AIR CONDITIONER"
    m2 = re.match(r"^([A-Z]{1,4}-\d+)\s+(.+)$", line, re.I)
    if m2:
        code, desc = m2.group(1).upper(), m2.group(2).strip()
        desc_vi = _translate_description(desc, glossary)
        return f"{code}: {desc_vi}"

    if FIG_OR_SEC.match(line) or line.startswith("23."):
        if "electrical system" in line.lower():
            return re.sub(r"ELECTRICAL SYSTEM", "HỆ THỐNG ĐIỆN", line, flags=re.I)
        m = SEC_HEADER.match(line)
        if m:
            sec, title = m.group(1), m.group(2).strip()
            title_vi = _translate_description(title, glossary)
            return f"{sec} {title_vi}"
        m2 = CABLE_LINE.match(line)
        if m2:
            desc, code = m2.group(1).strip(), m2.group(2)
            desc_vi = _translate_description(desc, glossary)
            return f"{desc_vi} : {code}"
        vi = _translate_description(line, glossary)
        if vi.lower() != line.lower():
            return vi
        return line

    vi = _translate_description(line, glossary)
    return vi


def translate_label_text(text_en: str, glossary: Glossary) -> str:
    lines = text_en.replace("\r", "").split("\n")
    vi_lines = [translate_label_line(ln, glossary) for ln in lines]
    return "\n".join(vi_lines)


def split_and_translate_label(note: LabelNote, glossary: Glossary) -> List[LabelNote]:
    """Tách nhãn nhiều dòng thành từng dòng riêng — tránh lặp nghĩa sai."""
    en = note.text_en.strip()
    if not en:
        return []

    lines = [ln.strip() for ln in en.split("\n") if ln.strip()]
    if len(lines) <= 1:
        vi = translate_label_text(en, glossary)
        return [_finalize_label(en, vi, glossary)]

    result: List[LabelNote] = []
    seen: set[str] = set()
    for ln in lines:
        key = ln.lower()
        if key in seen:
            continue
        seen.add(key)
        if is_ocr_suspicious(ln):
            result.append(_finalize_label(ln, "", glossary))
            continue
        vi = translate_label_line(ln, glossary)
        if vi == ln and len(ln) <= 3 and ln.isupper():
            continue
        result.append(_finalize_label(ln, vi, glossary))
    return result


def retranslate_all_labels(document, glossary: Glossary) -> int:
    """Cập nhật labels trong figures_registry và page blocks."""
    count = 0
    for entry in document.figures_registry.values():
        new_labels: List[LabelNote] = []
        seen: set[str] = set()
        for lb in entry.labels:
            for split_lb in split_and_translate_label(lb, glossary):
                key = split_lb.text_en.lower()
                if key in seen:
                    continue
                seen.add(key)
                new_labels.append(split_lb)
                count += 1
        entry.labels = new_labels

    for page in document.pages:
        for block in page.blocks:
            if not block.labels:
                continue
            new_labels: List[LabelNote] = []
            seen: set[str] = set()
            for lb in block.labels:
                for split_lb in split_and_translate_label(lb, glossary):
                    key = split_lb.text_en.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    new_labels.append(split_lb)
                    count += 1
            block.labels = new_labels

    return count
