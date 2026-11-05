"""Engine dịch EN→VI cho chunk manual điện — glossary + phrase rules, giữ cấu trúc dòng."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.glossary.manager import load_glossary
from src.ingest.text_structure import apply_structure_from_en, normalize_pdf_text
from src.models import Chunk, Glossary

PART_NUM = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")
FIG_REF = re.compile(r"Fig\.?\s*(\d+-\d+)", re.I)
MANUAL_REF = re.compile(r"\b\d{2}-\d{1,3}\b")

# Cụm tiêu đề TOC / heading thường gặp
HEADING_PHRASES: Dict[str, str] = {
    "ELECTRICAL CIRCUIT DIAGRAM": "SƠ ĐỒ MẠCH ĐIỆN",
    "ELECTRICAL CIRCUIT DIAGRAM AND EQUIPMENT LIST": "SƠ ĐỒ MẠCH ĐIỆN VÀ DANH MỤC THIẾT BỊ",
    "ELECTRICAL EQUIPMENT LIST": "DANH MỤC THIẾT BỊ ĐIỆN",
    "HARNESS & CABLE LIST": "DANH MỤC BỘ DÂY VÀ CÁP",
    "HARNESS CONNECTION DRAWING": "SƠ ĐỒ KẾT NỐI BỘ DÂY",
    "INSTRUMENT PANEL ASSY": "LẮP RÁP BẢNG ĐỒNG HỒ",
    "CONTROLLER ASSY": "LẮP RÁP BỘ ĐIỀU KHIỂN",
    "UPPER ELECTRIC ASSY": "LẮP RÁP ĐIỆN PHẦN TRÊN",
    "UPPER HARNESS ASSY": "LẮP RÁP BỘ DÂY PHẦN TRÊN",
    "ENGINE HARNESS ASSY": "LẮP RÁP BỘ DÂY ĐỘNG CƠ",
    "FLOOR PLATE HARNESS ASSY": "LẮP RÁP BỘ DÂY TẤM SÀN",
    "CAB HARNESS ASSY": "LẮP RÁP BỘ DÂY BUỒNG LÁI",
    "BATTERY": "ẮC QUY",
    "CAB ASSY": "LẮP RÁP BUỒNG LÁI",
    "HARNESS": "BỘ DÂY",
    "AIR CONDITIONER": "ĐIỀU HÒA",
    "MOUNTING BOOM LIGHT": "LẮP ĐÈN CẦN",
    "MOUNTING FUEL PUMP": "LẮP BƠM NHIÊN LIỆU",
    "MOUNTING CAB WORK LIGHT": "LẮP ĐÈN LÀM VIỆC BUỒNG LÁI",
    "MOUNTING BOOM WORK LIGHT": "LẮP ĐÈN LÀM VIỆC CẦN",
    "MOUNTING DECK LIGHT": "LẮP ĐÈN SÀN",
    "MOUNTING WORK LIGHT": "LẮP ĐÈN LÀM VIỆC",
    "MOUNTING LIGHT": "LẮP ĐÈN",
    "MOUNTING CAB": "LẮP BUỒNG LÁI",
    "WINDOW WASHER TANK & PUMP": "BÌNH NƯỚC RỬA KÍNH & BƠM",
    "WINDOW WASHER TANK AND PUMP": "BÌNH NƯỚC RỬA KÍNH & BƠM",
    "WASHER TANK & PUMP": "BÌNH NƯỚC RỬA & BƠM",
    "WASHER TANK": "BÌNH NƯỚC RỬA",
    "NAME PLATE INSTAL (ELECTRIC COMPONENT FOR AUTO IDLE STOP)": "LẮP BIỂN TÊN (LINH KIỆN ĐIỆN CHO DỪNG CHẾ ĐỘ NHẢ GA TỰ ĐỘNG)",
    "INSTRUMENT PANEL HARNESS": "BỘ DÂY BẢNG ĐỒNG HỒ",
    "CAB SWITCH INSTAL": "LẮP CÔNG TẮC BUỒNG LÁI",
    "ELECTRICAL SYSTEM": "HỆ THỐNG ĐIỆN",
    "TROUBLESHOOTING (ELECTRIC)": "CHẨN ĐOÁN SỰ CỐ (ĐIỆN)",
    "TROUBLESHOOTING": "CHẨN ĐOÁN SỰ CỐ",
    "CONTROLLER ERROR CODE DISPLAY": "HIỂN THỊ MÃ LỖI BỘ ĐIỀU KHIỂN",
    "FRONT WINDOW ASSY": "LẮP RÁP KÍNH TRƯỚC",
    "WIPER ASSY": "LẮP RÁP GẠT MƯA",
    "WIPER HARNESS": "BỘ DÂY GẠT MƯA",
    "DOOR SWITCH": "CÔNG TẮC CỬA",
    "CURL CODE HARNESS": "BỘ DÂY MÃ CUỐN",
    "CAB HARNESS": "BỘ DÂY CABIN",
    "ANTENNA": "ĂNG-TEN",
    "ROOM LAMP": "ĐÈN TRẦN",
    "ROOM LAMP HARNESS": "BỘ DÂY ĐÈN TRẦN",
    "INTER LOCK SWITCH": "CÔNG TẮC LIÊN ĐỘNG",
    "WORK LIGHT": "ĐÈN LÀM VIỆC",
    "BOOM LIGHT": "ĐÈN CẦN",
    "FUEL PUMP": "BƠM NHIÊN LIỆU",
    "PRESSURE SENSOR": "CẢM BIẾN ÁP SUẤT",
    "output command": "lệnh đầu ra",
    "feedback": "phản hồi",
    "are displayed.": "được hiển thị.",
    "is displayed.": "được hiển thị.",
    "is displayed": "được hiển thị",
    "When the hard check switch of the controller is set to": "Khi công tắc kiểm tra phần cứng của bộ điều khiển được đặt ở",
    "The machine is tuned to": "Máy được hiệu chỉnh ở",
    "at shipment.": "khi xuất xưởng.",
    "faults of devices and harnesses connected to them are listed as below.": "các lỗi của thiết bị và bộ dây điện kết nối được liệt kê như sau.",
    "When an error code is displayed, the CPU lamp on the gauge cluster flickers and the buzzer blows intermittently.": "Khi mã lỗi được hiển thị, đèn CPU trên cụm đồng hồ nhấp nháy và còi kêu gián đoạn.",
    "In some cases, the CPU lamp on the gauge cluster lights continuously and the buzzer sounds continuously.": "Trong một số trường hợp, đèn CPU trên cụm đồng hồ sáng liên tục và còi kêu liên tục.",
    "This is to indicate a fault on the controller itself.": "Điều này cho biết lỗi ở chính bộ điều khiển.",
    "Hardware check SW": "Công tắc kiểm tra phần cứng",
    "The left shows the": "Bên trái cho thấy",
    "setting condition.": "điều kiện cài đặt.",
    "Turn the arrow at the center of switch": "Xoay mũi tên ở giữa công tắc",
    "and set it to numeric value.": "và đặt giá trị số.",
    "Insert precision driver (-) in to convex": "Đưa tua vít chính xác (-) vào chỗ lõm",
    "indicated by arrow and turn the arrow to": "chỉ bởi mũi tên và xoay mũi tên để",
    "set to numeric values.": "đặt giá trị số.",
    "Trouble diagnosis": "Chẩn đoán sự cố",
    "Auto idling stop (AIS) does not": "Dừng chế độ nhả ga tự động (AIS) không",
    "function. E/G does not stop after an": "hoạt động. Động cơ không dừng sau",
    "elapse of 30 seconds after the lock": "30 giây kể từ khi cần",
    "lever was moved upward to locked": "gạt khóa được đẩy lên vị trí",
    "position.)": "khóa.)",
    "When overheating is": "Khi quá nhiệt được",
    "detected, AIS does not work": "phát hiện, AIS không hoạt động",
    "to protect E/G.": "để bảo vệ động cơ.",
    "Check on it in next page.": "Kiểm tra ở trang sau.",
    "There is 24V between light green /": "Có 24V giữa dây xanh lá nhạt /",
    "blue harness of holding circuit unit": "xanh dương của bộ mạch giữ",
    "and ground, when travel 1-2": "và mass, khi công tắc tốc độ di chuyển 1-2",
    "spped switch (SW-3) is pressed.": "(SW-3) được nhấn.",
    "Statuses of": "Trạng thái của",
    "on the 10th digit": "ở chữ số thứ 10",
    "on the 1st digit": "ở chữ số thứ 1",
    "on the 100th digit": "ở chữ số thứ 100",
    "(ON [1] / OFF [0] )": "(BẬT [1] / TẮT [0] )",
    "(ON [1] / OFF [0] ) are displayed.": "(BẬT [1] / TẮT [0] ) được hiển thị.",
    "(TEST [1] / RUN [0] ) is displayed.": "(TEST [1] / RUN [0] ) được hiển thị.",
    "Status of internal adjust switch on the 1st digit": "Trạng thái công tắc điều chỉnh nội bộ ở chữ số thứ 1",
    "Boom raise pressure sensor": "Cảm biến áp nâng cần",
    "Boom down pressure sensor": "Cảm biến áp hạ cần",
    "Arm out pressure sensor": "Cảm biến áp đưa càng",
    "Arm in pressure sensor": "Cảm biến áp kéo càng",
    "Bucket digging pressure sensor": "Cảm biến áp đào gầu",
    "Bucket dump pressure sensor": "Cảm biến áp đổ gầu",
    "Swing pressure sensor": "Cảm biến áp xoay",
    "P3 pump pressure sensor": "Cảm biến áp bơm P3",
    "Travel right pressure sensor": "Cảm biến áp di chuyển phải",
    "Travel left pressure sensor": "Cảm biến áp di chuyển trái",
    "Travel boost-up selector valve": "Van chọn tăng áp di chuyển",
    "wiper motor": "mô-tơ gạt mưa",
    "rise-up LS": "công tắc giới hạn nâng",
    "forward relay command": "lệnh rơ-le tiến",
    "reverse relay command": "lệnh rơ-le lùi",
    "washer motor relay command": "lệnh rơ-le mô-tơ phun nước rửa",
    "work light relay command": "lệnh rơ-le đèn làm việc",
    "CPU trouble lamp command": "lệnh đèn báo lỗi CPU",
    "wiper operating command": "lệnh vận hành gạt mưa",
    "wiper window operating input command": "lệnh đầu vào vận hành cửa sổ gạt mưa",
    "arc preventive relay command": "lệnh rơ-le chống hồ quang",
    "First edition": "Ấn bản đầu",
    "Date of Issue": "Ngày phát hành",
    "Applicable Machines": "Máy áp dụng",
    "Remarks": "Ghi chú",
    "Issue": "Phát hành",
    "Engine harness assy": "Lắp ráp bộ dây động cơ",
    "Engine harness": "Bộ dây động cơ",
    "Upper harness assy": "Lắp ráp bộ dây phần trên",
    "Cab harness assy": "Lắp ráp bộ dây cabin",
    "Cab instrument harness": "Bộ dây đồng hồ cabin",
    "Left instrument harness": "Bộ dây đồng hồ trái",
    "Right instrument harness": "Bộ dây đồng hồ phải",
    "Cab switch instal": "Lắp công tắc cabin",
    "Detail of compressor wiring": "Chi tiết dây máy nén",
    "Curl code harness": "Bộ dây mã cuốn",
    "INTER LOCK SWITCH": "CÔNG TẮC LIÊN ĐỘNG",
    "Inter lock switch": "Công tắc liên động",
    "cabin IN": "cabin vào",
    "YES": "CÓ",
    "NO": "KHÔNG",
    "See Fig.": "Xem Hình",
    "See fig.": "Xem Hình",
    "WARNING": "CẢNH BÁO",
    "CAUTION": "THẬN TRỌNG",
    "DANGER": "NGUY HIỂM",
    "NOTES:": "GHI CHÚ:",
    "Wire used to be": "Dây điện từng dùng",
    "Wires to be": "Dây điện là",
    "unless otherwise specified.": "trừ khi có ghi chú khác.",
    "Grounding for which wire color is not shown to": "Tiếp đất mà không ghi màu dây được",
    "be performed with component case.": "thực hiện bằng vỏ thiết bị.",
    "Number in square shows line No.": "Số trong ô vuông là số đường dây.",
    "for others.": "cho các loại khác.",
    "or over when": "hoặc hơn khi",
    "Swing pilot pressure is": "Áp pilot xoay là",
    "approx.": "khoảng",
    "pilot pressure": "áp pilot",
    "controller": "bộ điều khiển",
    "gauge cluster": "cụm đồng hồ",
    "buzzer": "còi",
    "harness": "bộ dây điện",
    "connector": "đầu nối",
    "relay": "rơ-le",
    "solenoid": "van điện từ",
    "sensor": "cảm biến",
    "switch": "công tắc",
    "ground": "mass",
    "battery": "ắc quy",
    "starter": "máy khởi động",
    "alternator": "máy phát điện",
    "fuse": "cầu chì",
    "circuit": "mạch",
    "terminal": "đầu cực",
    "voltage": "điện áp",
    "engine": "động cơ",
    "E/G": "động cơ",
    "upper": "phần trên",
    "assy": "lắp ráp",
    "assembly": "lắp ráp",
    "mounting": "lắp",
    "installation": "lắp đặt",
    "electric": "điện",
    "electrical": "điện",
    "light": "đèn",
    "lamp": "đèn",
    "motor": "mô-tơ",
    "pump": "bơm",
    "valve": "van",
    "cable": "cáp",
    "wire": "dây",
    "wires": "dây điện",
    "panel": "bảng",
    "instrument": "đồng hồ",
    "cab": "cabin",
    "boom": "cần",
    "arm": "càng",
    "bucket": "gầu",
    "swing": "xoay",
    "travel": "di chuyển",
    "pilot": "pilot",
    "pressure": "áp suất",
    "error code": "mã lỗi",
    "displayed": "hiển thị",
    "function": "chức năng",
    "operation": "vận hành",
    "check": "kiểm tra",
    "adjust": "điều chỉnh",
    "internal": "nội bộ",
    "external": "ngoài",
    "input": "đầu vào",
    "output": "đầu ra",
    "command": "lệnh",
    "feedback": "phản hồi",
    "troubleshooting": "chẩn đoán sự cố",
    "diagnosis": "chẩn đoán",
    "fault": "lỗi",
    "failure": "hỏng",
    "malfunction": "trục trặc",
    "overheating": "quá nhiệt",
    "detected": "phát hiện",
    "protect": "bảo vệ",
    "stop": "dừng",
    "start": "khởi động",
    "locked": "khóa",
    "position": "vị trí",
    "lever": "cần gạt",
    "pressed": "nhấn",
    "released": "nhả",
    "connected": "kết nối",
    "disconnected": "ngắt",
    "between": "giữa",
    "and": "và",
    "when": "khi",
    "if": "nếu",
    "then": "thì",
    "otherwise": "ngược lại",
    "except": "trừ",
    "note": "ghi chú",
    "refer to": "tham khảo",
    "see": "xem",
    "replace": "thay",
    "remove": "tháo",
    "install": "lắp",
    "inspect": "kiểm tra",
    "measure": "đo",
    "continuity": "thông mạch",
    "resistance": "điện trở",
    "continuity check": "kiểm tra thông mạch",
}

WORD_PHRASES = sorted(HEADING_PHRASES.items(), key=lambda x: -len(x[0]))


def _protect_tokens(text: str, tokens: List[str]) -> Tuple[str, Dict[str, str]]:
    placeholders: Dict[str, str] = {}
    out = text
    all_tokens = list(tokens) + PART_NUM.findall(text) + [m.group(0) for m in FIG_REF.finditer(text)]
    for i, tok in enumerate(dict.fromkeys(all_tokens)):
        if not tok or tok not in out:
            continue
        key = f"@@T{i}@@"
        placeholders[key] = tok
        out = out.replace(tok, key)
    return out, placeholders


def _restore_tokens(text: str, placeholders: Dict[str, str]) -> str:
    for key, val in placeholders.items():
        text = text.replace(key, val)
    return text


def _apply_glossary(text: str, glossary: Glossary) -> str:
    entries = sorted(
        [e for e in glossary.entries if e.term_vi and not e.do_not_translate],
        key=lambda e: -len(e.term_en),
    )
    for e in entries:
        pat = re.compile(rf"\b{re.escape(e.term_en)}\b", re.I)
        text = pat.sub(e.term_vi, text)
        for v in e.variants_en:
            patv = re.compile(rf"\b{re.escape(v)}\b", re.I)
            text = patv.sub(e.term_vi, text)
    return text


def _apply_phrases(text: str) -> str:
    for en, vi in WORD_PHRASES:
        if re.match(r"^[\w][\w\s/&.\-(),':]*[\w]$|^[\w]+$", en):
            pat = re.compile(rf"\b{re.escape(en)}\b", re.I)
        else:
            pat = re.compile(re.escape(en), re.I)
        text = pat.sub(vi, text)
    return text


def _translate_line(line: str, glossary: Glossary) -> str:
    line = line.strip()
    if not line:
        return ""
    # Giữ dòng chỉ có số mục, ref, part number, dots
    if re.match(r"^[\d\.\s\-]+$", line):
        return line
    if PART_NUM.search(line) and len(line) < 40:
        return line
    if re.match(r"^[\.\s\d\-]+$", line):
        return line

    protected, ph = _protect_tokens(line, [])
    out = _apply_phrases(protected)
    out = _apply_glossary(out, glossary)
    out = _restore_tokens(out, ph)

    # Dịch & → VÀ trong tiêu đề
    if "&" in out and out.isupper():
        out = out.replace("&", "VÀ")

    # Chuẩn Fig ref
    out = re.sub(r"Xem Hình\s+(\d+-\d+)", r"Xem Hình \1", out, flags=re.I)
    return out


def translate_chunk(chunk: Chunk, glossary: Glossary) -> str:
    text_en = normalize_pdf_text(chunk.text_en)
    if not text_en:
        return ""

    lines = text_en.split("\n")
    vi_lines = [_translate_line(ln, glossary) for ln in lines]
    text_vi = "\n".join(vi_lines)
    text_vi = apply_structure_from_en(text_en, text_vi)

    # Khôi phục protected tokens
    for tok in chunk.protected_tokens:
        if tok and tok not in text_vi and tok in text_en:
            # part numbers / refs must appear
            if PART_NUM.fullmatch(tok) or FIG_REF.search(tok) or MANUAL_REF.fullmatch(tok):
                pass  # already should be there from protect

    return _fix_section_spacing(normalize_pdf_text(text_vi))


def _fix_section_spacing(text: str) -> str:
    """23.1SƠ → 23.1 SƠ"""
    return re.sub(r"(\d\.\d+(?:\.\d+)*)([A-ZÀ-Ỹ])", r"\1 \2", text)


def translate_all_pending(workspace: Path) -> int:
    from src.models import BlockStatus
    from src.translate.chunker import load_chunks, save_chunk

    glossary = load_glossary(workspace)
    if not glossary.entries:
        from src.glossary.manager import load_seed_glossary, save_glossary
        from pathlib import Path as P
        glossary = load_seed_glossary(P("src/glossary/seeds/construction_electrical_en_vi.csv"))
        save_glossary(glossary, workspace)

    count = 0
    for chunk in load_chunks(workspace):
        if chunk.status != BlockStatus.PENDING:
            continue
        vi = translate_chunk(chunk, glossary)
        if vi and vi != chunk.text_en:
            chunk.text_vi = vi
            chunk.status = BlockStatus.DONE
            save_chunk(workspace, chunk)
            count += 1
        elif vi:
            # vẫn lưu nếu có dịch một phần
            chunk.text_vi = vi
            chunk.status = BlockStatus.DONE
            save_chunk(workspace, chunk)
            count += 1
    return count
