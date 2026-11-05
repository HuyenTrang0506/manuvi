"""
Vẽ lại sơ đồ lưu đồ (flowchart) Chương 43 bằng tiếng Việt.

Pipeline:
  enumerate_flowchart_pages() → for each page → build_page_model() → draw_flowchart_png()

Output:
  workspace/sk70sr-1es/assets/flowcharts_vi/43-{ref}.png   ← ảnh lưu đồ VI
  workspace/sk70sr-1es/assets/flowcharts_vi/43-{ref}.json  ← sidecar JSON
"""

from __future__ import annotations

import json
import os
import re
import textwrap
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

# Matplotlib - set backend trước khi import pyplot
os.environ.setdefault("MPLCONFIGDIR", "/tmp/mpl_flowchart")
import matplotlib
matplotlib.use("Agg")
import matplotlib.patches as mpatches
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch

from src.translate.flowchart import translate_flowchart_text
from src.translate.section43_map import lookup_section43
from src.models import Glossary

_EMPTY_GLOSSARY = Glossary(terms={})


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class FlowNode:
    idx: int
    node_type: str      # check | action | terminal | goto | header
    text_en: str
    text_vi: str
    branch_label: str = ""   # "CÓ" | "KHÔNG" | "" — nhãn trên mũi tên vào node này

    def as_dict(self) -> dict:
        return {
            "idx": self.idx,
            "type": self.node_type,
            "en": self.text_en,
            "vi": self.text_vi,
            "branch_label": self.branch_label,
        }


@dataclass
class FlowchartPage:
    manual_ref: str
    pdf_page: int
    title_en: str
    title_vi: str
    nodes: List[FlowNode] = field(default_factory=list)
    png_path: Optional[str] = None   # đường dẫn ảnh đã vẽ

    def as_dict(self) -> dict:
        return {
            "manual_ref": self.manual_ref,
            "pdf_page": self.pdf_page,
            "title_en": self.title_en,
            "title_vi": self.title_vi,
            "nodes": [n.as_dict() for n in self.nodes],
            "png_path": self.png_path,
        }


# ── Translation helpers ────────────────────────────────────────────────────────

_CODE_RE = re.compile(
    r"\b(SW\d+|SE-\d+|SV-\d+|PSV-[A-Z]|C-1|E\d{2}|P\d+|CN-\d+\w*|"
    r"[\d.]+\s*mA|[\d.]+\s*MPa|[\d.]+\s*psi|[\d.]+\s*V)\b",
    re.I,
)

# Mẫu dịch động — thay thế pattern + giữ mã thiết bị
_PATTERN_PHRASES = [
    # Inspect harness between X and Y
    (re.compile(r"Inspect harness between\s+(.+?)\s+and\s+(.+?)\.", re.I | re.S),
     lambda m: f"Kiểm tra bộ dây giữa {_keep_codes(m.group(1))} và {_keep_codes(m.group(2))}."),
    # Check harness between X and Y
    (re.compile(r"(?:Check|Inspect) harness between\s+(.+?)\s+and\s+C-1 controller\.", re.I | re.S),
     lambda m: f"Kiểm tra bộ dây giữa {_keep_codes(m.group(1))} và bộ điều khiển C-1."),
    # Trouble disappears if X is replaced
    (re.compile(r"Trouble (?:disappears|gets normal|is corrected) (?:if|when)\s+(.+?)\s+is replaced\.", re.I | re.S),
     lambda m: f"Sự cố được khắc phục khi thay {_keep_codes(m.group(1))}."),
    # Trouble disappears when X
    (re.compile(r"Trouble (?:disappears|gets normal)\s+when\s+(.+)\.", re.I | re.S),
     lambda m: f"Sự cố được khắc phục khi {_keep_codes(m.group(1))}."),
    # X shows approx. N V when lever is at neutral
    (re.compile(r"(.+?)\s+(?:shows?|reads?|indicates?)\s+(approx\.?\s*[\d.]+\s*V(?:\s+or\s+over)?)\s+when lever is at neutral\.", re.I | re.S),
     lambda m: f"{_keep_codes(m.group(1))} đo {m.group(2).replace('approx.','khoảng').replace('or over','trở lên')} khi cần ở vị trí trung gian."),
    # X reads more than N V when lever is at neutral
    (re.compile(r"(.+?)\s+(?:shows?|reads?)\s+more than\s+([\d.]+\s*V)\s+when lever is at neutral\.", re.I | re.S),
     lambda m: f"{_keep_codes(m.group(1))} đo > {m.group(2)} khi cần ở vị trí trung gian."),
    # X is more than N MPa (Ypsi) when lever is at neutral
    (re.compile(r"(.+?)\s+is\s+more than\s+([\d.]+\s*MPa)\s*\([\d.]+\s*psi\)\s+when lever is at neutral\.", re.I | re.S),
     lambda m: f"{_keep_codes(m.group(1))} > {m.group(2)} khi cần ở vị trí trung gian."),
    # Command current for X is approx. N mA
    (re.compile(r"Command current for\s+(.+?)\s+is\s+(approx\.?\s*[\d.]+\s*m\s*A(?:\s+or\s+over)?)\.", re.I | re.S),
     lambda m: f"Dòng lệnh của {_keep_codes(m.group(1))} là {m.group(2).replace('approx.','khoảng').replace('or over','trở lên')}."),
    # Command current for X is > N mA only when Y
    (re.compile(r"Command current for\s+(.+?)\s+is\s+[>≥]\s*(approx\.?\s*[\d.]+\s*m\s*A)\s+only when\s+(.+?)\.", re.I | re.S),
     lambda m: f"Dòng lệnh của {_keep_codes(m.group(1))} > {m.group(2).replace('approx.','khoảng')} chỉ khi {_keep_codes(m.group(3))}."),
    # Replace X or inspect harness between X and controller
    (re.compile(r"Replace\s+(.+?)\s+or inspect harness between\s+(.+?)\s+and\s+controller\.", re.I | re.S),
     lambda m: f"Thay {_keep_codes(m.group(1))} hoặc kiểm tra bộ dây giữa {_keep_codes(m.group(2))} và bộ điều khiển."),
    # Is the motion normal after connector X disconnected
    (re.compile(r"Is the motion normal after\s+connector\s*\((.+?)\)\s+(?:of\s+)?(.+?)\s+is disconnected\?", re.I | re.S),
     lambda m: f"Thao tác có bình thường sau khi ngắt đầu nối ({m.group(1)}) của {_keep_codes(m.group(2))}?"),
]


def _keep_codes(text: str) -> str:
    """Dịch cụm từ thông dụng nhưng giữ nguyên mã thiết bị.
    QUAN TRỌNG: đặt pattern dài/cụ thể TRƯỚC pattern ngắn/chung để tránh thay thế
    sớm làm hỏng pattern dài (ví dụ: "proportional valve" phải đứng sau
    "P2 bypass cut proportional valve").
    """
    t = text.strip().replace("\n", " ")
    # ── Bộ điều khiển ────────────────────────────────────────────────────────
    t = re.sub(r"\bC-1 controller\b", "bộ điều khiển C-1", t, flags=re.I)
    t = re.sub(r"\bcontroller\b", "bộ điều khiển", t, flags=re.I)
    # ── Van tỷ lệ — cụ thể trước, chung sau ─────────────────────────────────
    t = re.sub(r"\btravel straight proportional valve\b", "van tỷ lệ đi thẳng", t, flags=re.I)
    t = re.sub(r"\btravel straight proportional\b", "van tỷ lệ đi thẳng", t, flags=re.I)
    t = re.sub(r"\bP1 by-?pass cut proportional valve\b", "van tỷ lệ cắt bypass P1", t, flags=re.I)
    t = re.sub(r"\bP2 by-?pass cut proportional valve\b", "van tỷ lệ cắt bypass P2", t, flags=re.I)
    t = re.sub(r"\bbypass cut proportional valve\b", "van tỷ lệ cắt bypass", t, flags=re.I)
    t = re.sub(r"\bproportional valve\b", "van tỷ lệ", t, flags=re.I)
    # ── Van pilot — "ATT pilot valve" trước "pilot valve" ───────────────────
    t = re.sub(r"\bATT pilot valve\b", "van pilot ATT", t, flags=re.I)
    t = re.sub(r"\bpilot valve\b", "van pilot", t, flags=re.I)
    # ── Van điện từ — cụ thể trước chung ────────────────────────────────────
    t = re.sub(r"\bswing parking (?:release )?solenoid\b", "van điện từ nhả phanh quay toa", t, flags=re.I)
    t = re.sub(r"\blever lock solenoid\b", "van điện từ khóa cần", t, flags=re.I)
    t = re.sub(r"\bsolenoid valve\b", "van điện từ", t, flags=re.I)
    # ── Van chọn — cụ thể trước chung ───────────────────────────────────────
    t = re.sub(r"\bswing parking selector valve\b", "van chọn phanh quay toa", t, flags=re.I)
    t = re.sub(r"\bselector valve\b", "van chọn", t, flags=re.I)
    # ── Van khác ─────────────────────────────────────────────────────────────
    t = re.sub(r"\brelief valve\b", "van an toàn", t, flags=re.I)
    t = re.sub(r"\bcheck valve\b", "van một chiều", t, flags=re.I)
    # ── Cảm biến ─────────────────────────────────────────────────────────────
    t = re.sub(r"\bswing sensor\b", "cảm biến quay toa", t, flags=re.I)
    t = re.sub(r"\bboom raise sensor\b", "cảm biến nâng cần", t, flags=re.I)
    t = re.sub(r"\bboom lower sensor\b", "cảm biến hạ cần", t, flags=re.I)
    t = re.sub(r"\barm in sensor\b", "cảm biến tay gầu vào", t, flags=re.I)
    t = re.sub(r"\barm out sensor\b", "cảm biến tay gầu ra", t, flags=re.I)
    t = re.sub(r"\btravel right sensor\b", "cảm biến di chuyển phải", t, flags=re.I)
    t = re.sub(r"\btravel left sensor\b", "cảm biến di chuyển trái", t, flags=re.I)
    # ── Áp pilot ─────────────────────────────────────────────────────────────
    t = re.sub(r"\btravel right pilot pressure\b", "áp pilot di chuyển phải", t, flags=re.I)
    t = re.sub(r"\btravel left pilot pressure\b", "áp pilot di chuyển trái", t, flags=re.I)
    t = re.sub(r"\bpilot pressure\b", "áp pilot", t, flags=re.I)
    # ── Thao tác / chuyển động ───────────────────────────────────────────────
    t = re.sub(r"\btravel right\b", "di chuyển phải", t, flags=re.I)
    t = re.sub(r"\btravel left\b", "di chuyển trái", t, flags=re.I)
    t = re.sub(r"\bboom raise\b", "nâng cần", t, flags=re.I)
    t = re.sub(r"\bboom lower\b", "hạ cần", t, flags=re.I)
    t = re.sub(r"\barm out\b", "đưa tay gầu ra", t, flags=re.I)
    t = re.sub(r"\barm in\b", "tay gầu vào", t, flags=re.I)
    # ── Cảm biến cụ thể — TRƯỚC "bucket dump"→"đổ gầu" để pattern dài match trước ──
    t = re.sub(r"\bbucket dump sensor\b", "cảm biến đổ gầu", t, flags=re.I)
    t = re.sub(r"\bbucket dig(?:ging)? sensor\b", "cảm biến đào gầu", t, flags=re.I)
    t = re.sub(r"\bboom raise sensor\b", "cảm biến nâng cần", t, flags=re.I)
    t = re.sub(r"\bboom lower sensor\b", "cảm biến hạ cần", t, flags=re.I)
    t = re.sub(r"\barm in sensor\b", "cảm biến tay gầu vào", t, flags=re.I)
    t = re.sub(r"\barm out sensor\b", "cảm biến tay gầu ra", t, flags=re.I)
    # ── General motion (sau sensor patterns) ─────────────────────────────────
    t = re.sub(r"\bbucket dump\b", "đổ gầu", t, flags=re.I)
    t = re.sub(r"\bbucket dig(?:ging)?\b", "đào gầu", t, flags=re.I)
    t = re.sub(r"\bswing motion\b", "thao tác quay toa", t, flags=re.I)
    t = re.sub(r"\btravel motion\b", "thao tác di chuyển", t, flags=re.I)
    t = re.sub(r"\btravel boost-?up\b", "tăng lực di chuyển", t, flags=re.I)
    t = re.sub(r"\bswing\b", "quay toa", t, flags=re.I)   # general — sau các pattern cụ thể của swing
    # ── Dòng điện ─────────────────────────────────────────────────────────────
    t = re.sub(r"\bFeedback current\b", "Dòng phản hồi", t, flags=re.I)
    t = re.sub(r"\bCommand current for\b", "Dòng lệnh của", t, flags=re.I)
    t = re.sub(r"\bCommand current\b", "Dòng lệnh", t, flags=re.I)
    # ── Khác ─────────────────────────────────────────────────────────────────
    t = re.sub(r"\bparking brake\b", "phanh đỗ", t, flags=re.I)
    t = re.sub(r"\bis faulty\b", "bị lỗi", t, flags=re.I)
    t = re.sub(r"\bis shorted\b", "bị ngắn mạch", t, flags=re.I)
    t = re.sub(r"\bat neutral\b", "ở vị trí trung gian", t, flags=re.I)
    t = re.sub(r"\bgovernor motor\b", "mô-tơ điều tốc", t, flags=re.I)
    t = re.sub(r"\blever lock switch\b", "công tắc khóa cần", t, flags=re.I)
    t = re.sub(r"\bharness\b", "bộ dây", t, flags=re.I)
    t = re.sub(r"\bbattery relay\b", "rơ-le ắc quy", t, flags=re.I)
    t = re.sub(r"\bshorted to frame\b", "bị chập với khung", t, flags=re.I)
    t = re.sub(r"\bshorted point\b", "điểm chập", t, flags=re.I)
    t = re.sub(r"\bshort circuit\b", "ngắn mạch", t, flags=re.I)
    t = re.sub(r"\bopen circuit\b", "đứt mạch", t, flags=re.I)
    t = re.sub(r"\bdisconnection\b", "đứt kết nối", t, flags=re.I)
    t = re.sub(r"\bfuse\b", "cầu chì", t, flags=re.I)
    t = re.sub(r"\bblown\b", "bị đứt", t, flags=re.I)
    t = re.sub(r"\bHard check\b", "Kiểm tra phần cứng", t, flags=re.I)
    return t


def _full_cleanup(text: str) -> str:
    """
    Áp dụng toàn bộ thay thế từ khoá EN→VI cho một đoạn đã qua _keep_codes hoặc
    bản dịch tự động bị lẫn EN.  Gọi ở bước 3, 5 và 6 của _best_vi.
    """
    t = text
    # ── Sửa lỗi thứ tự khi bản dịch tự động đã dịch một phần ────────────────
    # "bypass cut proportional van" / "bypass cut van tỷ lệ" → "van tỷ lệ cắt bypass"
    t = re.sub(r'\bP1\s+bypass cut proportional\s+van\b', 'van tỷ lệ cắt bypass P1', t, flags=re.I)
    t = re.sub(r'\bP2\s+bypass cut proportional\s+van\b', 'van tỷ lệ cắt bypass P2', t, flags=re.I)
    t = re.sub(r'\bP1\s+bypass cut\s+van tỷ lệ\b', 'van tỷ lệ cắt bypass P1', t, flags=re.I)
    t = re.sub(r'\bP2\s+bypass cut\s+van tỷ lệ\b', 'van tỷ lệ cắt bypass P2', t, flags=re.I)
    t = re.sub(r'\bbypass cut proportional\s+van\b', 'van tỷ lệ cắt bypass', t, flags=re.I)
    t = re.sub(r'\bbypass cut\s+van tỷ lệ\b', 'van tỷ lệ cắt bypass', t, flags=re.I)
    # "travel straight van tỷ lệ" → "van tỷ lệ đi thẳng"
    t = re.sub(r'\btravel straight\s+van tỷ lệ\b', 'van tỷ lệ đi thẳng', t, flags=re.I)
    # Partially-translated motion words — "right"/"left" leftover after "travel" was already translated
    # Order: fix "di chuyển right/left cảm biến" (wrong order) first, then remaining "right/left"
    t = re.sub(r'\bdi chuyển phải cảm biến\b', 'cảm biến di chuyển phải', t, flags=re.I)
    t = re.sub(r'\bdi chuyển trái cảm biến\b', 'cảm biến di chuyển trái', t, flags=re.I)
    t = re.sub(r'\bdi chuyển right cảm biến\b', 'cảm biến di chuyển phải', t, flags=re.I)
    t = re.sub(r'\bdi chuyển left cảm biến\b', 'cảm biến di chuyển trái', t, flags=re.I)
    t = re.sub(r'\bdi chuyển right\b', 'di chuyển phải', t, flags=re.I)
    t = re.sub(r'\bdi chuyển left\b', 'di chuyển trái', t, flags=re.I)
    t = re.sub(r'\bcảm biến right\b', 'cảm biến di chuyển phải', t, flags=re.I)
    t = re.sub(r'\bcảm biến left\b', 'cảm biến di chuyển trái', t, flags=re.I)
    # "is > …" / "is >" → remove redundant "is"
    t = re.sub(r'\bis\s*(>|>=|<|<=)\s*', r'\1 ', t)
    # "lever is" when followed by Vietnamese context
    t = re.sub(r'\blever is\b', 'cần gạt', t, flags=re.I)
    # Redundant "is" before Vietnamese words
    t = re.sub(r'\bis\s+(?=[a-záàảãạăắặẳẵặâấầẩẫậđéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵ])', '', t, flags=re.I)
    # "shifted to full" (without "the") → "hết hành trình"
    t = re.sub(r'\bshifted to (?:the )?full\b', 'hết hành trình', t, flags=re.I)
    # "350m A" / "350mA" spacing fix → "350 mA"
    t = re.sub(r'(\d+)\s*m\s*A\b', r'\1 mA', t)
    # "khoảng." (leftover period from "approx.") → "khoảng"
    t = re.sub(r'\bkhoảng\.\s*(?=\d)', 'khoảng ', t)
    # ── Partially-translated component names (wrong order from auto-translate) ─
    # "cần raise/lower cảm biến" → sensor names
    t = re.sub(r'\bcần raise cảm biến\b', 'cảm biến nâng cần', t, flags=re.I)
    t = re.sub(r'\bcần lower cảm biến\b', 'cảm biến hạ cần', t, flags=re.I)
    t = re.sub(r'\bgầu digging cảm biến\b', 'cảm biến đào gầu', t, flags=re.I)
    t = re.sub(r'\bgầu dump cảm biến\b', 'cảm biến đổ gầu', t, flags=re.I)
    # "đổ gầu sensor" / "đào gầu sensor" (Vietnamese "đổ/đào gầu" + English "sensor")
    t = re.sub(r'\bđổ gầu sensor\b', 'cảm biến đổ gầu', t, flags=re.I)
    t = re.sub(r'\bđào gầu sensor\b', 'cảm biến đào gầu', t, flags=re.I)
    # "Swing áp pilot" / "quay toa áp pilot" → "áp pilot quay toa" (word order)
    # Note: use simple string match (not \b) because Vietnamese chars break word boundaries
    t = re.sub(r'(?i)Swing\s+áp pilot', 'áp pilot quay toa', t)
    t = re.sub(r'quay toa áp pilot', 'áp pilot quay toa', t)
    # "quay toa right/left" → "quay toa phải/trái"
    t = re.sub(r'\bquay toa right\b', 'quay toa phải', t, flags=re.I)
    t = re.sub(r'\bquay toa left\b', 'quay toa trái', t, flags=re.I)
    # "xoay parking van điện từ" → parking solenoid (partial translation fix)
    t = re.sub(r'\bxoay parking\b', 'nhả phanh quay toa', t, flags=re.I)
    t = re.sub(r'\bparking van điện từ\b', 'van điện từ nhả phanh quay toa', t, flags=re.I)
    # Remaining motion words after partial translation
    t = re.sub(r'\bcần raise\b', 'nâng cần', t, flags=re.I)
    t = re.sub(r'\bcần lower\b', 'hạ cần', t, flags=re.I)
    t = re.sub(r'\bgầu digging\b', 'đào gầu', t, flags=re.I)
    # ── Trạng thái / verb cơ bản ─────────────────────────────────────────────
    t = re.sub(r'\bYES\b', 'CÓ', t)
    # "No." as abbreviation for "Number" → "số" (must come BEFORE \bNO\b → KHÔNG)
    t = re.sub(r'\bNo\.(\d)', r'số \1', t)
    t = re.sub(r'\bNO\b', 'KHÔNG', t)
    t = re.sub(r'\bHard check\b', 'Kiểm tra phần cứng', t, flags=re.I)
    t = re.sub(r'\berror code\b', 'mã lỗi', t, flags=re.I)
    t = re.sub(r'\bInspect the\b', 'Kiểm tra', t, flags=re.I)
    t = re.sub(r'\bReplace the\b', 'Thay', t, flags=re.I)
    t = re.sub(r'\bInspect\b', 'Kiểm tra', t, flags=re.I)
    t = re.sub(r'\bReplace\b', 'Thay', t, flags=re.I)
    t = re.sub(r'\bTrouble disappears\b', 'Sự cố được khắc phục', t, flags=re.I)
    t = re.sub(r'\bTrouble gets normal\b', 'Sự cố trở về bình thường', t, flags=re.I)
    t = re.sub(r'\bTrouble is corrected\b', 'Sự cố được khắc phục', t, flags=re.I)
    t = re.sub(r'\bis replaced\b', 'được thay thế', t, flags=re.I)
    t = re.sub(r'\bis shown on\b', 'hiển thị trên', t, flags=re.I)
    t = re.sub(r'\bshown on\b', 'hiển thị trên', t, flags=re.I)
    t = re.sub(r'\bis shown\b', 'được hiển thị', t, flags=re.I)
    t = re.sub(r'\bis displayed\b', 'được hiển thị', t, flags=re.I)
    t = re.sub(r'\bis indicated\b', 'được hiển thị', t, flags=re.I)
    t = re.sub(r'\bis disconnected\b', 'bị ngắt kết nối', t, flags=re.I)
    t = re.sub(r'\bdisconnected\b', 'bị ngắt kết nối', t, flags=re.I)
    t = re.sub(r'\bis normal\b', 'bình thường', t, flags=re.I)
    t = re.sub(r'\bshorted to frame\b', 'bị chập với khung', t, flags=re.I)
    t = re.sub(r'\bshorted point\b', 'điểm chập', t, flags=re.I)
    t = re.sub(r'\bshorted\b', 'bị ngắn mạch', t, flags=re.I)
    t = re.sub(r'\bfaulty\b', 'bị lỗi', t, flags=re.I)
    t = re.sub(r'\bdisplayed\b', 'được hiển thị', t, flags=re.I)
    t = re.sub(r'\bindicated\b', 'xuất hiện', t, flags=re.I)
    t = re.sub(r'\bneutral\b', 'vị trí trung gian', t, flags=re.I)
    t = re.sub(r'\breplaced\b', 'được thay thế', t, flags=re.I)
    t = re.sub(r'\bworks\b', 'hoạt động', t, flags=re.I)
    t = re.sub(r'\boperate\b', 'vận hành', t, flags=re.I)
    t = re.sub(r'\boperated\b', 'gạt', t, flags=re.I)
    t = re.sub(r'\balive across\b', 'có điện qua', t, flags=re.I)
    t = re.sub(r'\bcontinuity\b', 'thông mạch', t, flags=re.I)
    t = re.sub(r'\bground\b', 'mass', t, flags=re.I)
    t = re.sub(r'\bwires?\b', 'dây', t, flags=re.I)
    t = re.sub(r'\bsolenoid\b', 'van điện từ', t, flags=re.I)
    t = re.sub(r'\bCheck\b', 'Kiểm tra', t)
    t = re.sub(r'\bThere\s+(?=thông mạch|đứt|là|có\s)', '', t)
    t = re.sub(r'\bduring\b', 'khi', t, flags=re.I)
    t = re.sub(r'\boverheating\b', 'quá nhiệt', t, flags=re.I)
    t = re.sub(r'\bdetected\b', 'được phát hiện', t, flags=re.I)
    t = re.sub(r'\bactuated\b', 'kích hoạt', t, flags=re.I)
    t = re.sub(r'\bimpressed\b', 'cấp điện', t, flags=re.I)
    t = re.sub(r'\bterminal\b', 'chân', t, flags=re.I)
    t = re.sub(r'\btimer unit\b', 'bộ hẹn giờ', t, flags=re.I)
    t = re.sub(r'\bholding circuit\b', 'mạch giữ', t, flags=re.I)
    t = re.sub(r'\bspeed switch\b', 'công tắc tốc độ', t, flags=re.I)
    t = re.sub(r'\btravel 1-2\b', 'di chuyển 1-2', t, flags=re.I)
    t = re.sub(r'\bengine running\b', 'động cơ đang chạy', t, flags=re.I)
    t = re.sub(r'\blocklever limit switch\b', 'công tắc giới hạn cần khóa', t, flags=re.I)
    t = re.sub(r'\blimit switch\b', 'công tắc hành trình', t, flags=re.I)
    t = re.sub(r'\btravel straight proportional\b', 'van tỷ lệ đi thẳng', t, flags=re.I)
    t = re.sub(r'\btraveling\b', 'di chuyển', t, flags=re.I)
    t = re.sub(r'\bdiagnosis\b', 'chẩn đoán', t, flags=re.I)
    t = re.sub(r'\bselecting\b', 'chọn', t, flags=re.I)
    t = re.sub(r'\bselect(?:or)?\b', 'bộ chọn', t, flags=re.I)
    t = re.sub(r'\bpossible\b', 'có thể', t, flags=re.I)
    t = re.sub(r'\bupward\b', 'lên trên', t, flags=re.I)
    t = re.sub(r'\bmoved\b', 'di chuyển', t, flags=re.I)
    t = re.sub(r'\blocke?d\b', 'khóa', t, flags=re.I)
    t = re.sub(r'\bcondition\b', 'điều kiện', t, flags=re.I)
    t = re.sub(r'\bshows?\b', 'đo', t, flags=re.I)
    t = re.sub(r'\breads?\b', 'đo', t, flags=re.I)
    t = re.sub(r'\bindicates?\b', 'hiển thị', t, flags=re.I)
    t = re.sub(r'\bconnector\b', 'đầu nối', t, flags=re.I)
    t = re.sub(r'\bwiring\b', 'bộ dây', t, flags=re.I)
    t = re.sub(r'\bfuse box\b', 'hộp cầu chì', t, flags=re.I)
    t = re.sub(r'\bmotions?\b', 'thao tác', t, flags=re.I)
    t = re.sub(r'\bswing\b', 'quay toa', t, flags=re.I)
    # ── Liên từ / giới từ ────────────────────────────────────────────────────
    t = re.sub(r'\bonly when\b', 'chỉ khi', t, flags=re.I)
    t = re.sub(r'\bonly\b', 'chỉ', t, flags=re.I)
    t = re.sub(r'\bif\b', 'nếu', t, flags=re.I)
    t = re.sub(r'\bwhen\b', 'khi', t, flags=re.I)
    t = re.sub(r'\bbetween\b', 'giữa', t, flags=re.I)
    t = re.sub(r'\band\b', 'và', t, flags=re.I)
    t = re.sub(r'\bor\b', 'hoặc', t, flags=re.I)
    t = re.sub(r'\bof\b', 'của', t, flags=re.I)
    t = re.sub(r'\bthe\b', '', t, flags=re.I)
    # "for" as preposition → "cho" (e.g. "đầu nối for van điện từ" → "đầu nối cho van điện từ")
    t = re.sub(r'\bfor\b', 'cho', t, flags=re.I)
    # "on it" / "it" → remove artifact pronouns
    t = re.sub(r'\bon it\b', '', t, flags=re.I)
    t = re.sub(r'\bit\b', '', t, flags=re.I)
    # Switch ON/OFF state (must come before generic "on"/"off")
    t = re.sub(r'\bSW on\b', 'SW bật', t, flags=re.I)
    t = re.sub(r'\bSW off\b', 'SW tắt', t, flags=re.I)
    t = re.sub(r'\bturned on\b', 'bật', t, flags=re.I)
    t = re.sub(r'\bturned off\b', 'tắt', t, flags=re.I)
    t = re.sub(r'\bis on\b', 'đang bật', t, flags=re.I)
    t = re.sub(r'\bis off\b', 'đang tắt', t, flags=re.I)
    # "on" as preposition (after switch-state patterns) → "trên"
    t = re.sub(r'\bon\b', 'trên', t, flags=re.I)
    # "off" standalone → "tắt"
    t = re.sub(r'\boff\b', 'tắt', t, flags=re.I)
    # "not" as negation → "không"
    t = re.sub(r'\bnot\b', 'không', t, flags=re.I)
    # "in" as preposition → "trong"
    t = re.sub(r'\bin\b', 'trong', t, flags=re.I)
    # "line" (wiring) → "đường dây"
    t = re.sub(r'\bline\b', 'đường dây', t, flags=re.I)
    # ── Số / đơn vị ──────────────────────────────────────────────────────────
    # "approx." → "khoảng" (note: \bapprox\b\.? consumes the trailing period correctly)
    t = re.sub(r'\bapprox\b\.?', 'khoảng', t, flags=re.I)
    t = re.sub(r'\bor over\b', 'trở lên', t, flags=re.I)
    t = re.sub(r'\bhoặc over\b', 'trở lên', t, flags=re.I)
    t = re.sub(r'\bor more\b', 'trở lên', t, flags=re.I)
    t = re.sub(r'\bis\s+more than\b', '>', t, flags=re.I)
    t = re.sub(r'\bmore than\b', '>', t, flags=re.I)
    # ── Lever / position ─────────────────────────────────────────────────────
    t = re.sub(r'\blever is at neutral\b', 'cần ở vị trí trung gian', t, flags=re.I)
    t = re.sub(r'\blever is shifted to the full\b', 'cần gạt hết hành trình', t, flags=re.I)
    t = re.sub(r'\b(at|lever is at) neutral\b', 'ở vị trí trung gian', t, flags=re.I)
    t = re.sub(r'\bshifted to the full\b', 'hết hành trình', t, flags=re.I)
    # "is ở" → "ở" (redundant "is" before Vietnamese position word)
    t = re.sub(r'\bis\s+ở\b', 'ở', t, flags=re.I)
    # "trở lên at" / "or over at" → "trở lên khi" ("at" used as "when" before Vietnamese action)
    t = re.sub(r'\btrở lên at\b', 'trở lên khi', t, flags=re.I)
    t = re.sub(r'\bover at\b', 'trở lên khi', t, flags=re.I)
    # Doubled "cần" or "gạt" from partial translations
    t = re.sub(r'\bnâng cần cần gạt\b', 'nâng cần gạt', t, flags=re.I)
    t = re.sub(r'\bhạ cần cần gạt\b', 'hạ cần gạt', t, flags=re.I)
    t = re.sub(r'\bcần gạt gạt\b', 'cần gạt', t, flags=re.I)
    # "error" standalone (when flagged as EN)
    t = re.sub(r'\berror\b', 'lỗi', t, flags=re.I)
    # "does not work" → "không hoạt động"
    t = re.sub(r'\bdoes not work\b', 'không hoạt động', t, flags=re.I)
    # "does không work" (partial translation artifact) → "không hoạt động"
    t = re.sub(r'\bdoes không work\b', 'không hoạt động', t, flags=re.I)
    t = re.sub(r'\bdoes\b', '', t, flags=re.I)
    # "to protect" → "để bảo vệ"
    t = re.sub(r'\bto protect\b', 'để bảo vệ', t, flags=re.I)
    # "to lock/to khóa position" → "để khóa vị trí"
    t = re.sub(r'\bto khóa\b', 'để khóa', t, flags=re.I)
    t = re.sub(r'\bto lock\b', 'để khóa', t, flags=re.I)
    # "speed" → "tốc độ" (for "di chuyển 1-2 speed" contexts)
    t = re.sub(r'\bspeed\b', 'tốc độ', t, flags=re.I)
    # "switch" standalone (after "selector switch" and "speed switch" patterns have fired) → "công tắc"
    t = re.sub(r'\bswitch\b', 'công tắc', t, flags=re.I)
    # "lock" as standalone verb → "khóa"
    t = re.sub(r'\block\b(?!\s+cần)', 'khóa', t, flags=re.I)
    # "position" → "vị trí"
    t = re.sub(r'\bposition\b', 'vị trí', t, flags=re.I)
    # "at this time" / "at this point" → "vào lúc này"
    t = re.sub(r'\bat this (?:time|point)\b', 'vào lúc này', t, flags=re.I)
    # "even if" / "even when" → "ngay cả khi"
    t = re.sub(r'\beven (?:if|when)\b', 'ngay cả khi', t, flags=re.I)
    t = re.sub(r'\beven\b', 'ngay cả', t, flags=re.I)
    # "that" as conjunction (e.g. "Kiểm tra that bộ dây") → remove or "rằng"
    t = re.sub(r'\bthat\b(?=\s+bộ|\s+mạch|\s+cảm|\s+van|\s+rơ|\s+cần|\s+dây|\s+công)', '', t, flags=re.I)
    # Wire colors (long combos first) → Vietnamese
    t = re.sub(r'\blight\s+green\b', 'xanh lá nhạt', t, flags=re.I)
    t = re.sub(r'\bgreen\b', 'xanh lá', t, flags=re.I)
    t = re.sub(r'\bgray\b', 'xám', t, flags=re.I)
    t = re.sub(r'\bpink\b', 'hồng', t, flags=re.I)
    t = re.sub(r'\bblue\b', 'xanh dương', t, flags=re.I)
    t = re.sub(r'\bblack\b', 'đen', t, flags=re.I)
    t = re.sub(r'\bwhite\b', 'trắng', t, flags=re.I)
    t = re.sub(r'\bred\b', 'đỏ', t, flags=re.I)
    t = re.sub(r'\byellow\b', 'vàng', t, flags=re.I)
    t = re.sub(r'\borange\b', 'cam', t, flags=re.I)
    t = re.sub(r'\bpurple\b', 'tím', t, flags=re.I)
    # "gầu dump" (partial) → "gầu đổ đất" (bucket dump partially translated)
    t = re.sub(r'\bgầu dump\b', 'gầu đổ đất', t, flags=re.I)
    t = re.sub(r'\bdump\b', 'đổ', t, flags=re.I)
    # "normal opera-?\s*tion" → "hoạt động bình thường" (OCR hyphenated word)
    t = re.sub(r'\bnormal\s+opera-?\s*tion\b', 'hoạt động bình thường', t, flags=re.I)
    t = re.sub(r'\bnormal\b', 'bình thường', t, flags=re.I)
    t = re.sub(r'\bopera-?\s*tion\b', 'hoạt động', t, flags=re.I)
    # Stray OCR fragment "rmal" from "nor-mal" broken across lines
    t = re.sub(r'\brmal\b', '', t, flags=re.I)
    # "pressed" → "được nhấn"
    t = re.sub(r'\bpressed\b', 'được nhấn', t, flags=re.I)
    # ── Dọn khoảng trắng thừa ────────────────────────────────────────────────
    t = re.sub(r' {2,}', ' ', t).strip()
    return t


def _best_vi(text_en: str, chunk_text_vi: str = "") -> str:
    """
    Lấy bản dịch VI tốt nhất cho một đoạn EN.
    Thứ tự ưu tiên:
      1. lookup_section43 (map chính xác)
      2. lookup với text normalize (xóa newline)
      3. translate_flowchart_text
      4. Pattern matching động
      5. chunk.text_vi (nếu ít lẫn EN/VI) — áp dụng _full_cleanup
      6. Fallback: EN gốc với _keep_codes + _full_cleanup
    """
    text_en = text_en.strip()
    if not text_en:
        return ""

    # 1. Lookup trực tiếp
    vi = lookup_section43(text_en)
    if vi is not None:
        return vi

    # 2. Lookup với normalize (newline → space)
    normalized = re.sub(r"\s+", " ", text_en).strip()
    if normalized != text_en:
        vi = lookup_section43(normalized)
        if vi is not None:
            return vi

    # 3. translate_flowchart_text + full cleanup
    vi2 = translate_flowchart_text(text_en, _EMPTY_GLOSSARY)
    if vi2 and vi2.strip() != text_en.strip():
        vi2 = _full_cleanup(_keep_codes(re.sub(r"\s+", " ", vi2).strip()))
        # Kiểm tra tỉ lệ VI (nếu dịch được đủ khối lượng)
        en_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', text_en))
        vi2_en_words = set(re.findall(r'\b[a-zA-Z]{4,}\b', vi2))
        if len(en_words) == 0 or len(vi2_en_words) / len(en_words) < 0.7:
            return vi2.strip()

    # 4. Pattern matching động
    for pattern, translator in _PATTERN_PHRASES:
        m = pattern.search(normalized)
        if m:
            try:
                result = translator(m)
                if result and result != text_en:
                    return _full_cleanup(result)
            except Exception:
                pass

    # 5. Dùng chunk.text_vi nếu ít lẫn EN — áp dụng full cleanup rồi kiểm tra lại
    if chunk_text_vi:
        cv = chunk_text_vi.strip()
        en_words_in_vi = set(re.findall(r'\b[A-Za-z]{5,}\b', cv))
        en_words_in_vi = {w for w in en_words_in_vi
                          if not re.match(
                              r'^(SW|SE|PSV|MPa|mA|SV|CN|KHÔNG|approx|PSV)$',
                              w, re.I)}
        if len(en_words_in_vi) <= 3:
            # Áp dụng cleanup
            cv_clean = _full_cleanup(_keep_codes(re.sub(r"\s+", " ", cv).strip()))
            # Kiểm tra lại sau cleanup — nếu vẫn còn nhiều EN, bỏ qua và dùng bước 6
            remaining_en = set(re.findall(r'\b[a-zA-Z]{5,}\b', cv_clean))
            remaining_en = {w for w in remaining_en
                            if not re.match(
                                r'^(SW|SE|PSV|MPa|mA|SV|CN|bypass)$',
                                w, re.I)}
            # Số EN còn sót không được vượt quá 1 (tức là chỉ chấp nhận "bypass" và tên thiết bị)
            if len(remaining_en) <= 1:
                return cv_clean

    # 6. Fallback: _keep_codes + full cleanup trên EN gốc
    result = _full_cleanup(_keep_codes(normalized))
    return result


# ── Node type classification ───────────────────────────────────────────────────

_CHECK_RE = re.compile(
    r"Hard check SW"
    r"|shows?\s+[\d.]+\s*[Vv]"
    r"|reads?\s+[\d.]+\s*[Vv]"
    r"|indicates?\s+[\d.]+\s*[Vv]"
    r"|is\s+[\d.]+\s*[Vv]"
    r"|\d+\s*mA\b"
    r"|[\d.]+\s*MPa"
    r"|\bIs the\b"
    r"|\bDoes\b"
    r"|correct after"
    r"|normal after",
    re.I,
)
_TERMINAL_RE = re.compile(
    r"Trouble\s+(disappears|gets normal|is corrected)"
    r"|Readjust engine speed"
    r"|Normal\s*\(",
    re.I,
)
_GOTO_RE = re.compile(
    r"Continued on\s+P"
    r"|To hydraulic"
    r"|Go to"
    r"|Refer to"
    r"|See page"
    r"|^43-\d+\s*$",
    re.I,
)
_HEADER_RE = re.compile(
    r"^(HARDWARE CHECK|SERVICE DIAGNOSIS|INTERNAL ADJUSTMENT|43\.\d)",
    re.I,
)


def _classify(text_en: str) -> str:
    t = text_en.strip()
    if _HEADER_RE.search(t):
        return "header"
    if _TERMINAL_RE.search(t):
        return "terminal"
    if _GOTO_RE.search(t):
        return "goto"
    if _CHECK_RE.search(t):
        return "check"
    return "action"


def _extract_branch_label(text_en: str) -> Tuple[str, str]:
    """
    Nếu text bắt đầu bằng YES/NO, tách ra branch_label + phần còn lại.
    Ví dụ: 'YES\nInspect harness...' → ('YES','Inspect harness...')
    """
    m = re.match(r"^(YES|NO)\s*[\n:]?\s*(.*)", text_en.strip(), re.S | re.I)
    if m:
        label = "CÓ" if m.group(1).upper() == "YES" else "KHÔNG"
        rest = m.group(2).strip()
        return label, rest
    # Nhiều YES/NO liên tiếp
    m2 = re.match(r"^(?:YES\s*\n)+(.+)", text_en.strip(), re.S | re.I)
    if m2:
        return "CÓ", m2.group(1).strip()
    m3 = re.match(r"^(?:NO\s*\n)+(.+)", text_en.strip(), re.S | re.I)
    if m3:
        return "KHÔNG", m3.group(1).strip()
    return "", text_en


def _infer_page_title(chunks: List[dict]) -> Tuple[str, str]:
    """Suy ra tiêu đề trang (EN, VI) từ danh sách chunk."""
    # Tìm chunk đầu tiên có tên triệu chứng (không phải SW/code/YES/NO)
    symptom_map = {
        # LINE_PHRASES từ flowchart.py
        "Swing speed is slow": "Tốc độ quay chậm",
        "Swing power is poor": "Lực quay kém",
        "Swing drifts too much": "Quay bị trôi quá nhiều",
        "Machine moves freely when lever is at neutral.": "Máy di chuyển tự do khi cần ở trung gian.",
        "No motions operate": "Không có thao tác nào hoạt động",
        "Fine operation of swing motion can not be performed.": "Không thể thao tác tinh chỉnh chuyển động quay.",
        "Fine operation of bucket digging and dump motions can not be performed.": "Không thể thao tác tinh chỉnh đào/đổ gầu.",
        "Fine operation of arm in and out motions is not possible.": "Không thể thao tác tinh chỉnh tay gầu vào/ra.",
        "Fine operation of boom raise and lower motions can not be performed.": "Không thể thao tác tinh chỉnh nâng/hạ cần.",
        "Only arm motion is slow.": "Chỉ thao tác tay gầu chậm.",
        "Travel power is poor": "Lực di chuyển kém",
    }
    for c in chunks:
        en = c.get("text_en", "").strip()
        if en in symptom_map:
            return en, symptom_map[en]
        vi = _best_vi(en)
        if vi and vi != en and len(en) > 15 and not en.startswith(("YES", "NO", "Hard check")):
            return en, vi

    # Fallback: page ref
    ref = chunks[0].get("manual_ref", "") if chunks else ""
    return f"Trang {ref}", f"Trang {ref}"


# ── Page model builder ─────────────────────────────────────────────────────────

def build_page_model(page_ref: str, pdf_page: int, chunks: List[dict]) -> FlowchartPage:
    """Chuyển danh sách chunk thành FlowchartPage có danh sách nodes."""
    title_en, title_vi = _infer_page_title(chunks)
    page = FlowchartPage(
        manual_ref=page_ref,
        pdf_page=pdf_page,
        title_en=title_en,
        title_vi=title_vi,
    )

    idx = 0
    for chunk in chunks:
        en_raw = chunk.get("text_en", "").strip()
        if not en_raw:
            continue

        # Tách nhãn YES/NO nếu có
        branch_label, en_body = _extract_branch_label(en_raw)

        if not en_body:
            continue

        # Bỏ qua chunk chỉ là YES/NO đơn
        if re.fullmatch(r"(YES|NO)\s*", en_body.strip(), re.I):
            continue

        ntype = _classify(en_body)
        # Dùng text_vi từ chunk nếu có (làm gợi ý bổ sung)
        chunk_vi = chunk.get("text_vi", "") or ""
        vi_body = _best_vi(en_body, chunk_vi)

        node = FlowNode(
            idx=idx,
            node_type=ntype,
            text_en=en_body,
            text_vi=vi_body,
            branch_label=branch_label,
        )
        page.nodes.append(node)
        idx += 1

    return page


# ── Drawing helpers ────────────────────────────────────────────────────────────

# Màu sắc theo loại node
_COLORS: Dict[str, Tuple] = {
    "check":    ("#FFF3CD", "#856404"),   # vàng nhạt — kiểm tra điều kiện
    "action":   ("#D1ECF1", "#0C5460"),   # xanh nhạt — hành động sửa chữa
    "terminal": ("#D4EDDA", "#155724"),   # xanh lá — giải quyết xong
    "goto":     ("#E2E3E5", "#383D41"),   # xám — chuyển trang
    "header":   ("#F8D7DA", "#721C24"),   # đỏ nhạt — tiêu đề bảng
}
_DEFAULT_COLORS = ("#F0F0F0", "#333333")

_WRAP_WIDTH = 42   # ký tự mỗi dòng trong node
_NODE_MIN_H = 0.9  # inch
_NODE_W = 7.0      # inch
_ARROW_H = 0.35    # inch mỗi mũi tên


def _wrap(text: str, width: int = _WRAP_WIDTH) -> str:
    """Wrap text giữ xuống dòng gốc."""
    parts = []
    for line in text.split("\n"):
        if len(line) > width:
            parts.append(textwrap.fill(line, width))
        else:
            parts.append(line)
    return "\n".join(parts)


def _node_height(text: str, fs: float = 10) -> float:
    """Ước tính chiều cao node (inch) theo số dòng văn bản."""
    lines = text.count("\n") + 1
    line_h = fs * 1.4 / 72.0   # inch per line
    return max(_NODE_MIN_H, lines * line_h + 0.25)


def _draw_diamond(ax, x_center, y_center, w, h, fc, ec):
    """Vẽ hình thoi (diamond) cho node kiểm tra."""
    xs = [x_center, x_center + w/2, x_center, x_center - w/2]
    ys = [y_center + h/2, y_center, y_center - h/2, y_center]
    diamond = plt.Polygon(list(zip(xs, ys)), closed=True,
                          facecolor=fc, edgecolor=ec, linewidth=1.2)
    ax.add_patch(diamond)


def _draw_rounded_rect(ax, x0, y0, w, h, fc, ec, style="round,pad=0.1"):
    """Vẽ hình chữ nhật bo góc cho node action/terminal/goto."""
    fancy = FancyBboxPatch(
        (x0, y0), w, h,
        boxstyle=style,
        facecolor=fc, edgecolor=ec, linewidth=1.2,
    )
    ax.add_patch(fancy)


def _draw_arrow(ax, x, y_from, y_to, label: str = ""):
    """Vẽ mũi tên từ trên xuống, tuỳ chọn nhãn."""
    ax.annotate(
        "",
        xy=(x, y_to),
        xytext=(x, y_from),
        arrowprops=dict(arrowstyle="->", color="#333333", lw=1.2),
    )
    if label:
        mid_y = (y_from + y_to) / 2
        ax.text(x + 0.08, mid_y, label, fontsize=8, color="#555555",
                ha="left", va="center", style="italic")


# ── Main draw function ─────────────────────────────────────────────────────────

def draw_flowchart_png(page: FlowchartPage, out_path: Path) -> None:
    """Vẽ lưu đồ VI cho một trang thành file PNG."""

    nodes = [n for n in page.nodes if n.node_type != "header"]
    if not nodes:
        return

    # Tính kích thước canvas
    total_h = 1.8  # title area
    node_heights = []
    for node in nodes:
        nh = _node_height(_wrap(node.text_vi))
        if node.node_type == "check":
            nh = max(nh, 1.1)
        node_heights.append(nh)
        total_h += nh + _ARROW_H

    fig_w = _NODE_W + 2.5
    fig_h = max(total_h + 1.0, 6.0)

    fig, ax = plt.subplots(figsize=(fig_w, fig_h))
    ax.set_xlim(0, fig_w)
    ax.set_ylim(-fig_h + 0.5, 0.5)
    ax.axis("off")
    fig.patch.set_facecolor("white")

    # ── Tiêu đề trang ────────────────────────────────────────────────────────
    title_y = 0.1
    ref = page.manual_ref
    ax.text(
        fig_w / 2, title_y,
        f"Trang {ref} — {page.title_vi}",
        ha="center", va="top", fontsize=13, fontweight="bold", color="#1a1a2e",
    )
    ax.text(
        fig_w / 2, title_y - 0.38,
        f"({page.title_en})",
        ha="center", va="top", fontsize=9, color="#666666",
    )

    # Đường kẻ ngang dưới tiêu đề
    ax.axhline(y=title_y - 0.65, xmin=0.05, xmax=0.95,
               color="#cccccc", linewidth=1.0)

    cur_y = title_y - 0.9   # y hiện tại (đầu node kế tiếp)
    cx = fig_w / 2           # tâm ngang

    for i, (node, nh) in enumerate(zip(nodes, node_heights)):
        fc, ec = _COLORS.get(node.node_type, _DEFAULT_COLORS)
        text = _wrap(node.text_vi)
        w = _NODE_W

        # Nhãn branch (CÓ/KHÔNG) trên mũi tên vào
        if i > 0 and node.branch_label:
            ax.text(
                cx + w / 2 * 0.55, cur_y + _ARROW_H * 0.5,
                node.branch_label,
                ha="left", va="center", fontsize=9,
                color="#007700" if node.branch_label == "CÓ" else "#cc0000",
                fontweight="bold",
            )

        node_top = cur_y
        node_bot = cur_y - nh

        if node.node_type == "check":
            # Hình thoi
            _draw_diamond(ax, cx, (node_top + node_bot) / 2, w, nh, fc, ec)
        else:
            # Hình chữ nhật bo góc
            style = "round,pad=0.12"
            if node.node_type == "terminal":
                style = "round,pad=0.14"
            elif node.node_type == "goto":
                style = "round,pad=0.08"
            _draw_rounded_rect(ax, cx - w / 2, node_bot, w, nh, fc, ec, style)

        # Chỉ số node (nhỏ, góc trái)
        ax.text(
            cx - w / 2 + 0.1, node_top - 0.08,
            f"#{i+1}",
            ha="left", va="top", fontsize=7, color="#999999",
        )

        # Văn bản node
        ax.text(
            cx, (node_top + node_bot) / 2,
            text,
            ha="center", va="center", fontsize=10,
            color=ec, multialignment="center",
            linespacing=1.3,
        )

        # Mũi tên xuống (không vẽ dưới node cuối)
        if i < len(nodes) - 1:
            _draw_arrow(ax, cx, node_bot, node_bot - _ARROW_H)

        cur_y = node_bot - _ARROW_H

    # Watermark nhỏ
    ax.text(
        fig_w - 0.1, -fig_h + 0.6,
        "SK70SR-1ES · Phần 43 · Bản dịch VI",
        ha="right", va="bottom", fontsize=7, color="#bbbbbb",
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    import io as _io
    from PIL import Image as _Image
    buf = _io.BytesIO()
    fig.savefig(buf, format="png", dpi=150, bbox_inches="tight",
                facecolor="white", edgecolor="none")
    plt.close(fig)
    buf.seek(0)
    img = _Image.open(buf)
    # Flatten RGBA → RGB (white background) so LibreOffice PDF export works
    if img.mode in ("RGBA", "LA", "P"):
        bg = _Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1] if img.mode == "RGBA" else None)
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.save(str(out_path), format="PNG", optimize=False)


# ── Enumeration & batch runner ─────────────────────────────────────────────────

def _flowchart_dir(workspace: Path) -> Path:
    return workspace / "assets" / "flowcharts_vi"


def enumerate_flowchart_pages(workspace: Path) -> List[dict]:
    """
    Liệt kê tất cả trang flowchart Section 43 (43-3 đến 43-22).
    Trả về list dict với manual_ref, pdf_page, chunk_count.
    """
    import glob

    page_map_path = workspace / "reference" / "page_map.json"
    page_map: Dict[str, dict] = {}
    if page_map_path.exists():
        page_map = json.loads(page_map_path.read_text(encoding="utf-8"))

    # Tải tất cả chunk vào dict ref → list
    chunks_by_ref: Dict[str, List[dict]] = {}
    for fp in sorted(Path(workspace / "chunks").glob("chunk_*.json")):
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        ref = d.get("manual_ref", "")
        if ref.startswith("43-"):
            chunks_by_ref.setdefault(ref, []).append(d)

    pages = []
    for ref, info in sorted(page_map.items()):
        if not ref.startswith("43-"):
            continue
        pdf_page = info.get("pdf_page", 0)
        chunks = chunks_by_ref.get(ref, [])
        pages.append({
            "manual_ref": ref,
            "pdf_page": pdf_page,
            "chunk_count": len(chunks),
            "chunks": chunks,
        })
    return pages


def run_redraw_batch(
    workspace: Path,
    force: bool = False,
    only: Optional[List[str]] = None,
    progress_cb: Optional[Callable] = None,
    dry_run: bool = False,
) -> List[dict]:
    """
    Vòng lặp vẽ lại lưu đồ VI cho tất cả trang S43 flowchart.

    Args:
        workspace:   đường dẫn workspace (ví dụ: workspace/sk70sr-1es)
        force:       True → vẽ lại dù đã có file PNG
        only:        danh sách manual_ref cụ thể (VD: ['43-13','43-14'])
        progress_cb: callback(i, total, unit) gọi trước mỗi unit
        dry_run:     chỉ liệt kê, không vẽ

    Returns:
        list dict kết quả mỗi unit: {manual_ref, status, png_path, error}
    """
    pages = enumerate_flowchart_pages(workspace)
    # Lọc chỉ trang flowchart (bỏ 43-1, 43-2 prose_spec)
    pages = [p for p in pages if p["chunk_count"] >= 3]

    if only:
        only_set = set(only)
        pages = [p for p in pages if p["manual_ref"] in only_set]

    fc_dir = _flowchart_dir(workspace)
    fc_dir.mkdir(parents=True, exist_ok=True)

    results = []
    total = len(pages)

    for i, page_info in enumerate(pages, 1):
        ref = page_info["manual_ref"]
        out_png = fc_dir / f"{ref}.png"
        out_json = fc_dir / f"{ref}.json"

        unit = {"manual_ref": ref, "png_path": str(out_png), "error": None}

        if progress_cb:
            progress_cb(i, total, page_info)

        # Skip nếu đã có và không force
        if out_png.exists() and not force:
            unit["status"] = "cached"
            results.append(unit)
            continue

        if dry_run:
            unit["status"] = "dry_run"
            results.append(unit)
            continue

        try:
            model = build_page_model(
                ref,
                page_info["pdf_page"],
                page_info["chunks"],
            )
            draw_flowchart_png(model, out_png)

            # Ghi sidecar JSON
            out_json.write_text(
                json.dumps(model.as_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            model.png_path = str(out_png)
            unit["status"] = "drawn"
            unit["node_count"] = len(model.nodes)
        except Exception as exc:
            unit["status"] = "error"
            unit["error"] = str(exc)

        results.append(unit)

    return results


def list_available_vi_flowcharts(workspace: Path) -> Dict[str, Path]:
    """Trả về dict manual_ref → png_path cho tất cả ảnh VI đã vẽ."""
    fc_dir = _flowchart_dir(workspace)
    result = {}
    if fc_dir.exists():
        for p in sorted(fc_dir.glob("43-*.png")):
            ref = p.stem  # "43-13"
            result[ref] = p
    return result
