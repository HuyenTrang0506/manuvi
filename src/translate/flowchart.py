"""Dịch node flowchart / bảng chẩn đoán Section 43 — nguyên cụm, không word-by-word."""

from __future__ import annotations

import re
from typing import Dict

from src.ingest.text_structure import fix_glued_tokens, normalize_pdf_text
from src.models import Chunk, Glossary
from src.translate.engine import _fix_section_spacing
from src.translate.section43_map import lookup_section43

# ── Khối nguyên văn (ưu tiên dài trước) ──────────────────────────────────
BLOCK_PHRASES: Dict[str, str] = {
    "No motions operate\nMachine moves freely\nwhen lever is at neutral": (
        "Không có thao tác nào hoạt động\n"
        "Máy di chuyển tự do\n"
        "khi cần gạt ở vị trí trung gian"
    ),
    "Only bucket speed is\nslow.\nOnly boom raise\nspeed is slow.": (
        "Chỉ tốc độ gầu chậm.\n"
        "Chỉ tốc độ nâng cần\n"
        "chậm."
    ),
    "Speed is slow\nPower is poor\nFine operation\nimpossible": (
        "Tốc độ chậm\n"
        "Lực yếu\n"
        "Không thể thao tác\n"
        "tinh chỉnh"
    ),
    "Motions are slow\nexcept boom raise\nand arm digging.": (
        "Các thao tác chậm\n"
        "trừ nâng cần\n"
        "và đào tay gầu."
    ),
    "Travel 1-2 speed can\nnot be changed\nTravel independent\nspeed is low.": (
        "Không đổi được tốc độ\n"
        "di chuyển 1-2\n"
        "Tốc độ di chuyển độc lập\n"
        "thấp."
    ),
    "ATT travel combined and\ntravel straight are\nimpossible.": (
        "Không thể kết hợp ATT\n"
        "di chuyển và đi thẳng."
    ),
    "Machine drifts on a\ndownhill and does not\nstop still on a slope": (
        "Máy trôi trên dốc xuống\n"
        "và không đứng yên\n"
        "trên dốc"
    ),
    "Swing can not be held\non a slope\nAn unusual sound is\nheard during swing\nAuto Idle stop is not\nactuated": (
        "Không thể giữ quay\n"
        "trên dốc\n"
        "Nghe thấy âm thanh bất thường\n"
        "khi quay\n"
        "Dừng không tải tự động\n"
        "không hoạt động"
    ),
    "Travel 1-2speed select SW\nFuse blown, shortcircuit,\ndisconnection": (
        "Công tắc chọn tốc độ di chuyển 1-2\nCầu chì bị đứt, ngắn mạch,\nđứt dây"
    ),
    "Error\ncode\nTrouble diagnosis item\nCorrective action\nError\ncode\nTrouble diagnosis item\nCorrective action": (
        "Mã\nlỗi\nHạng mục chẩn đoán\nBiện pháp khắc phục\nMã\nlỗi\nHạng mục chẩn đoán\nBiện pháp khắc phục"
    ),
    "Replace engine hydraulic\nswitch or inspect harness\nbetween engine oil switch\nand controller.": (
        "Thay công tắc thủy lực động cơ\n"
        "hoặc kiểm tra bộ dây giữa\n"
        "công tắc dầu động cơ\n"
        "và bộ điều khiển."
    ),
    "Replace selector valve or\ninspect harness between\nselector valve and\ncontroller.": (
        "Thay van chọn\n"
        "hoặc kiểm tra bộ dây giữa\n"
        "van chọn và\n"
        "bộ điều khiển."
    ),
    "Replace battery relay or\ninspect harness between\nbattery relay and controller.": (
        "Thay rơ-le ắc quy hoặc\n"
        "kiểm tra bộ dây giữa\n"
        "rơ-le ắc quy và bộ điều khiển."
    ),
    "Replace proportional valve\nor inspect harness between\nproportional valve and controller.": (
        "Thay van tỷ lệ\n"
        "hoặc kiểm tra bộ dây giữa\n"
        "van tỷ lệ và bộ điều khiển."
    ),
    "Replace governor motor or\ninspect harness between\ngovernor motor and controller.": (
        "Thay mô-tơ điều tốc hoặc\n"
        "kiểm tra bộ dây giữa\n"
        "mô-tơ điều tốc và bộ điều khiển."
    ),
    "Replace sensor or inspect\nharness between sensor\nand controller.": (
        "Thay cảm biến hoặc kiểm tra\n"
        "bộ dây giữa cảm biến\n"
        "và bộ điều khiển."
    ),
    "Readjust engine speed or\nreplace controller": (
        "Hiệu chỉnh lại tốc độ động cơ hoặc\nthay bộ điều khiển"
    ),
    "Remove grommet,\nand the right\nswitches are\nappear.": (
        "Tháo vòng đệm cao su,\n"
        "các công tắc bên phải\n"
        "sẽ hiện ra."
    ),
    "Hardware check SW (The left shows the\n\"0.0\" setting condition.)\nTurn the arrow at the center of switch\nand set it to numeric value.\nInsert precision driver (-) in to convex\nindicated by arrow and turn the arrow to\nset to numeric values.": (
        "Công tắc kiểm tra phần cứng (Bên trái là\n"
        "trạng thái cài đặt \"0.0\".)\n"
        "Xoay mũi tên ở giữa công tắc\n"
        "để đặt giá trị số.\n"
        "Đưa tua vít chính xác (-) vào chỗ lõm\n"
        "chỉ bởi mũi tên và xoay để\n"
        "đặt giá trị số."
    ),
    "Machine moves freely when lever is at neutral.": (
        "Máy di chuyển tự do khi cần ở vị trí trung gian."
    ),
    "Lever lock switch (SW-11)\nis mounted improperly.": (
        "Công tắc khóa cần gạt (SW-11)\nlắp đặt không đúng."
    ),
    "Swing motion can not be held on a slope.": "Không thể giữ quay trên dốc.",
    "Only boom raise and arm in motions are slow.": (
        "Chỉ tốc độ nâng cần và tay gầu vào là chậm."
    ),
    "YES (Hard check SW30) ; controller service diagnosis 30": (
        "CÓ (Kiểm tra phần cứng SW30) ; chẩn đoán dịch vụ bộ điều khiển 30"
    ),
    "(Hard check SW00) ; error code E17 is indicated at this time.": (
        "(Kiểm tra phần cứng SW00) ; mã lỗi E17 được hiển thị lúc này."
    ),
    "(Hard check SW00) ; Error code E17 is displayed at this time.": (
        "(Kiểm tra phần cứng SW00) ; mã lỗi E17 được hiển thị lúc này."
    ),
    "(Hard check SW00) ; error code E18 is displayed at this time.": (
        "(Kiểm tra phần cứng SW00) ; mã lỗi E18 được hiển thị lúc này."
    ),
    "(Hard check SW00) ; Error code E18 comes out on that occasion.": (
        "(Kiểm tra phần cứng SW00) ; mã lỗi E18 xuất hiện trong trường hợp đó."
    ),
    "Trouble disappears if arm in sensor (SE-7)\nis replaced.": (
        "Sự cố được khắc phục nếu cảm biến tay gầu vào (SE-7) được thay thế."
    ),
    "Trouble disappears if arm\nin sensor (SE-7) is replaced.": (
        "Sự cố được khắc phục nếu cảm biến\ntay gầu vào (SE-7) được thay thế."
    ),
    "Trouble disappears when arm in\nsensor (SE-7) is replaced.": (
        "Sự cố được khắc phục khi cảm biến tay gầu vào\n(SE-7) được thay thế."
    ),
    "Trouble disappears if travel\nleft sensor (SE-10) is\nreplaced.": (
        "Sự cố được khắc phục nếu cảm biến\ndi chuyển trái (SE-10) được thay thế."
    ),
    "Trouble disappears when\ntravel right sensor (SE-9)\nis replaced.": (
        "Sự cố được khắc phục khi\ncảm biến di chuyển phải (SE-9)\nđược thay thế."
    ),
    "YES\nYES\nTrouble disappears when swing sensor\n(SE-5) is replaced.": (
        "CÓ\nCÓ\nSự cố được khắc phục khi cảm biến quay toa\n(SE-5) được thay thế."
    ),
    "Boom raise sensor (SE-3)\nis faulty.\nYES": "Cảm biến nâng cần (SE-3)\nbị lỗi.\nCÓ",
    "Fine operation of swing motion can not be performed.": (
        "Không thể thực hiện thao tác tinh chỉnh chuyển động quay."
    ),
    "Fine operation of boom raise and lower motions can not be performed.": (
        "Không thể thực hiện thao tác tinh chỉnh nâng/hạ cần."
    ),
    "Fine operation of arm in and out motions is not possible.": (
        "Không thể thực hiện thao tác tinh chỉnh tay gầu vào/ra."
    ),
    "Fine operation of bucket digging and dump motions can not be performed.": (
        "Không thể thực hiện thao tác tinh chỉnh đào/đổ gầu."
    ),
    "24V is impressed on timer unit No.1\npin terminal.": (
        "Có 24V tại chân số 1 của bộ đếm thời gian."
    ),
    "Swing parking release\nswitch (SW-4) is at\nNORMAL.": (
        "Công tắc nhả phanh quay toa\n(SW-4) ở vị trí\nBÌNH THƯỜNG."
    ),
}

# ── Từng dòng / cụm ngắn (exact match, không substring) ──────────────────
LINE_PHRASES: Dict[str, str] = {
    # Symptom index
    "No motions operate": "Không có thao tác nào hoạt động",
    "Machine moves freely": "Máy di chuyển tự do",
    "when lever is at neutral": "khi cần gạt ở vị trí trung gian",
    "Only bucket speed is": "Chỉ tốc độ gầu",
    "slow.": "chậm.",
    "Only boom raise": "Chỉ nâng cần",
    "speed is slow.": "tốc độ chậm.",
    "Speed is slow": "Tốc độ chậm",
    "Power is poor": "Lực yếu",
    "Fine operation": "Thao tác tinh chỉnh",
    "impossible": "không thể",
    "Motions are slow": "Các thao tác chậm",
    "except boom raise": "trừ nâng cần",
    "and arm digging.": "và đào tay gầu.",
    "Travel 1-2 speed can": "Không đổi được tốc độ",
    "not be changed": "di chuyển 1-2",
    "Travel independent": "Tốc độ di chuyển độc lập",
    "speed is low.": "thấp.",
    "ATT travel combined and": "Không thể kết hợp ATT",
    "travel straight are": "di chuyển và đi thẳng",
    "impossible.": "không thể.",
    "Machine drifts on a": "Máy trôi trên",
    "downhill and does not": "dốc xuống và không",
    "stop still on a slope": "đứng yên trên dốc",
    "Swing motion does not": "Chuyển động quay không",
    "work": "hoạt động",
    "Swing speed is slow": "Tốc độ quay chậm",
    "Swing power is poor": "Lực quay yếu",
    "Swing drifts too much": "Quay bị trôi quá nhiều",
    "Travel power is poor": "Lực di chuyển yếu",
    "Only arm motion is slow.": "Chỉ thao tác tay gầu chậm.",
    "Only arm out motion": "Chỉ thao tác tay gầu ra",
    "is slow.": "chậm.",
    "All operation": "Mọi thao tác",
    "Bucket digging": "Đào gầu",
    "Boom raising": "Nâng cần",
    "Boom raising, lowering": "Nâng/hạ cần",
    "Arm in": "Tay gầu vào",
    "Arm in, out": "Tay gầu vào/ra",
    "Swing left, right": "Quay trái/phải",
    "Electric systems": "Hệ thống điện",
    "Attachment operation": "Thao tác thiết bị công tác",
    "Travel operation": "Thao tác di chuyển",
    "Swing operation": "Thao tác quay toa",
    "Others": "Khác",
    "System": "Hệ thống",
    "Group No.": "Số nhóm",
    "Trouble": "Sự cố",
    "Pin": "Chân",
    "Components": "Linh kiện",
    "Proportional valve": "Van tỷ lệ",
    "Sensor": "Cảm biến",
    "C1 mechatro controller": "Bộ điều khiển Mechatro C-1",
    # Error table headers
    "Error": "Mã",
    "code": "lỗi",
    "Trouble diagnosis item": "Hạng mục chẩn đoán",
    "Corrective action": "Biện pháp khắc phục",
    "1. Trouble diagnosis": "1. Chẩn đoán sự cố",
    "SERVICE DIAGNOSIS DISPLAY": "HIỂN THỊ CHẨN ĐOÁN DỊCH VỤ",
    "HARDWARE CHECK SW": "CÔNG TẮC KIỂM TRA PHẦN CỨNG",
    "INTERNAL ADJUSTMENT JP SW": "CÔNG TẮC JP ĐIỀU CHỈNH NỘI BỘ",
    "Hardware check SW": "Công tắc kiểm tra phần cứng",
    "Trouble diagnosis": "Chẩn đoán sự cố",
    # Error items
    "EEPROM data faulty": "Lỗi dữ liệu EEPROM",
    "Mechatronic adjustment faulty": "Lỗi điều chỉnh Mechatro",
    "Fault on boom raise pressure sensor": "Lỗi cảm biến áp nâng cần",
    "Fault on boom lower pressure sensor": "Lỗi cảm biến áp hạ cần",
    "Fault on arm in pressure sensor": "Lỗi cảm biến áp tay gầu vào",
    "Fault on arm out pressure sensor": "Lỗi cảm biến áp tay gầu ra",
    "Fault on bucket digging pressure sensor": "Lỗi cảm biến áp đào gầu",
    "Fault on bucket dump pressure sensor": "Lỗi cảm biến áp đổ gầu",
    "Fault on swing pressure sensor": "Lỗi cảm biến áp quay toa",
    "Fault on P3 pump pressure sensor": "Lỗi cảm biến áp bơm P3",
    "Fault on travel right pressure sensor": "Lỗi cảm biến áp di chuyển phải",
    "Fault on travel left pressure sensor": "Lỗi cảm biến áp di chuyển trái",
    "Accel dial faulty pressure sensor": "Lỗi cảm biến / núm ga (accel dial)",
    "Fault on travel boost-up solenoid valve": "Lỗi van điện từ tăng lực di chuyển",
    "Fault on swing parking selector valve": "Lỗi van chọn phanh quay toa",
    "Battery relay faulty": "Lỗi rơ-le ắc quy",
    "Fault on P1 by-pass cut proportional valve": "Lỗi van tỷ lệ cắt bypass P1",
    "Fault on P2 by-pass cut proportional valve": "Lỗi van tỷ lệ cắt bypass P2",
    "Fault on travel straight proportional valve": "Lỗi van tỷ lệ đi thẳng",
    "Fault on governor motor current": "Lỗi dòng điện mô-tơ điều tốc",
    "Fault on governor motor": "Lỗi mô-tơ điều tốc",
    "starting point": "điểm khởi đầu",
    "Reverse rotation of engine": "Động cơ quay ngược",
    # Components on diagnosis table
    "Swing P/B selector valve": "Van chọn phanh quay toa",
    "P2 by-pass cut proportional valve": "Van tỷ lệ cắt bypass P2",
    "Travel straight proportional valve": "Van tỷ lệ đi thẳng",
    "P1 by-pass cut proportional valve": "Van tỷ lệ cắt bypass P1",
    "Travel boost-up SOL": "Van điện từ tăng lực di chuyển",
    "Boom raising pressure sensor": "Cảm biến áp nâng cần",
    "Boom lowering pressure sensor": "Cảm biến áp hạ cần",
    "Arm in pressure sensor": "Cảm biến áp tay gầu vào",
    "Arm out pressure sensor": "Cảm biến áp tay gầu ra",
    "Bucket digging pressure sensor": "Cảm biến áp đào gầu",
    # Misc lines
    "Travel 1-2speed select SW": "Công tắc chọn tốc độ di chuyển 1-2",
    "Fuse blown, shortcircuit,": "Cầu chì bị đứt, ngắn mạch,",
    "disconnection": "đứt dây",
    "Device Item": "Mục thiết bị",
    "Normal (no service diagnosis)": "Bình thường (không chẩn đoán dịch vụ)",
    "A set value of engine rpm": "Giá trị cài đặt tốc độ động cơ",
    "An actual engine rpm": "Tốc độ động cơ thực tế",
    "Key switch input / option switch input": "Đầu vào công tắc khóa / đầu vào công tắc tùy chọn",
    "43.1": "43.1",
    "TROUBLE DIAGNOSIS TABLE : ELECTRIC": "BẢNG CHẨN ĐOÁN SỰ CỐ : ĐIỆN",
    "43.2": "43.2",
    "CONTROLLER ERROR CODE DISPLAY": "HIỂN THỊ MÃ LỖI BỘ ĐIỀU KHIỂN",
    "43. TROUBLESHOOTING (ELECTRIC)": "43. CHẨN ĐOÁN SỰ CỐ (ĐIỆN)",
}

BLOCK_SORTED = sorted(BLOCK_PHRASES.items(), key=lambda x: -len(x[0]))
LINE_SORTED = sorted(LINE_PHRASES.items(), key=lambda x: -len(x[0]))

NUMERIC_LINE = re.compile(r"^[\d.~\-]+$")
PIN_LINE = re.compile(r"^(\d{1,2}\s*[-–]\s*\d{1,2}|G\(A\d\)5V|5V\(A\d\)G)$", re.I)
ERROR_CODE = re.compile(r"^E\d{2}$", re.I)


def _norm_key(text: str) -> str:
    t = normalize_pdf_text(text).strip()
    t = re.sub(r"[ \t]+", " ", t)
    return t


def _translate_line(line: str, glossary: Glossary) -> str:
    raw = line.rstrip()
    line = raw.strip()
    if not line:
        return ""
    if NUMERIC_LINE.match(line) or PIN_LINE.match(line):
        return line
    if ERROR_CODE.match(line):
        return line.upper()
    # Chỉ YES/NO đứng một mình mới dịch — không thay trong câu
    if line in ("YES", "NO", "A", "B", "C", "D", "E", "F", "H", "S"):
        return {"YES": "CÓ", "NO": "KHÔNG"}.get(line, line)

    for en, vi in LINE_SORTED:
        if line.lower() == en.lower():
            return vi

    # Không apply HEADING_PHRASES / glossary word-by-word (gây KHÔNG/not, gầu/bucket…)
    return raw if raw.endswith(" ") or raw != line else line


def translate_flowchart_text(text_en: str, glossary: Glossary) -> str:
    text_en = normalize_pdf_text(fix_glued_tokens(text_en))
    if not text_en:
        return ""

    mapped = lookup_section43(text_en)
    if mapped is not None:
        return mapped

    key = _norm_key(text_en)
    for en, vi in BLOCK_SORTED:
        if key == _norm_key(en):
            return vi

    lines = text_en.split("\n")
    vi_lines = [_translate_line(ln, glossary) for ln in lines]
    # Nếu vẫn còn cả khối EN nguyên (không khớp dòng), thử map từng dòng qua lookup
    if vi_lines == lines:
        remapped = []
        for ln in lines:
            m = lookup_section43(ln)
            remapped.append(m if m is not None else _translate_line(ln, glossary))
        return "\n".join(remapped)
    return "\n".join(vi_lines)


def translate_flowchart_chunk(chunk: Chunk, glossary: Glossary) -> str:
    return _fix_section_spacing(translate_flowchart_text(chunk.text_en, glossary))
