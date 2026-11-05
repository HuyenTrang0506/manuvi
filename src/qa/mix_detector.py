"""Phát hiện dịch lẫn Anh–Việt (prose + nhãn) — siết hơn detector cũ."""

from __future__ import annotations

import re
from typing import List, Tuple

VI_CHARS = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)

# Mã / viết tắt kỹ thuật được phép giữ
ALLOW_TOKEN = re.compile(
    r"^(?:"
    r"[A-Z]{1,4}-\d+[A-Z]?|"  # SW-7, SE-3, C-1
    r"E\d{2}|P[123]|CN-\d+[A-Z]?|"
    r"ON|OFF|TEST|RUN|ATT|CPU|EEPROM|AIS|SOL|LS|OPT|DC|AC|AM|FM|"
    r"Mechatro|Kobelco|SK\d+|YT\d+|"
    r"[WGRBY]{1,3}|Br|Lg|Gr|Sb|"  # màu dây
    r"mA|MPa|psi|rpm|V|A"
    r")$",
    re.I,
)

# Từ EN kỹ thuật / chức năng — nếu còn trong câu có dấu VI ⇒ lẫn
EN_RESIDUAL = {
    "pressure", "sensor", "switch", "relay", "harness", "controller", "solenoid",
    "travel", "boom", "bucket", "swing", "lever", "lock", "mounting", "washer",
    "tank", "replace", "inspect", "fault", "displayed", "command", "feedback",
    "hardware", "check", "speed", "select", "boost", "curl", "code", "name",
    "part", "qty", "corrective", "action", "trouble", "diagnosis", "operation",
    "motion", "neutral", "only", "except", "machine", "moves", "freely", "drifts",
    "impossible", "poor", "slow", "between", "output", "valve", "pedal", "option",
    "arm", "digging", "dump", "left", "right", "working", "light", "room",
    "wiper", "engine", "fuel", "coolant", "safety", "glow", "motor", "assy",
    "from", "position", "timer", "parking", "brake", "idle", "auto", "stop",
    "connector", "ground", "battery", "starter", "alternator", "fuse", "cable",
    "wire", "panel", "instrument", "cab", "upper", "electric", "electrical",
    "circuit", "diagram", "equipment", "list", "system", "components", "group",
    "trouble", "pin", "proportional", "straight", "independent", "combined",
    "downhill", "slope", "actuated", "unusual", "sound", "heard", "during",
    "raise", "lower", "raising", "lowering", "out", "into", "onto", "with",
    "when", "where", "the", "and", "for", "of", "or", "not", "can", "does",
    "will", "must", "should", "error", "item", "normal", "value", "contents",
    "display", "service", "internal", "adjustment", "remove", "grommet",
    "appear", "numeric", "arrow", "center", "insert", "precision", "driver",
    "inter", "two", "single", "double", "clockwise", "counterclockwise",
    "rotary", "roof", "horn", "work", "front", "detail", "compressor", "wiring",
    "instal", "install", "left", "right",
}

EN_RESIDUAL_UPPER = {w.upper() for w in EN_RESIDUAL if len(w) >= 3}

VI_ASCII_OK = {
    "xanh", "trai", "phai", "trung", "binh", "thuong", "den", "cong", "tat",
    "cam", "bien", "ro", "le", "bo", "day", "dien", "cap", "van", "bom", "quay",
    "toa", "phanh", "sau", "truoc", "tren", "duoi", "phong", "buong", "lai",
    "cung", "ga", "can", "gau", "mo", "to", "ac", "quy",
}

WORD = re.compile(r"[A-Za-z]{2,}")


def _strip_allowed(text: str) -> str:
    """Bỏ mã thiết bị / đơn vị trước khi soi từ EN."""
    t = text
    t = re.sub(
        r"\b(?:[A-Z]{1,4}-\d+[A-Z]?|E\d{2}|P[123]|CN-\d+[A-Z]?|YT\d{2}[A-Z]\d{5}[A-Z]\d)\b",
        " ",
        t,
        flags=re.I,
    )
    t = re.sub(r"\b(?:ON|OFF|TEST|RUN|ATT|CPU|EEPROM|AIS|OPT|DC|AC)\b", " ", t, flags=re.I)
    t = re.sub(r"\b(?:mA|MPa|psi|rpm)\b", " ", t, flags=re.I)
    return t


def residual_english_tokens(text: str) -> List[str]:
    body = _strip_allowed(text)
    found: List[str] = []
    for w in WORD.findall(body):
        if ALLOW_TOKEN.match(w):
            continue
        wl = w.lower()
        if wl in VI_ASCII_OK:
            continue
        if wl in {"mechatro", "pilot", "bypass", "shunt", "kobelco"}:
            continue
        if wl in EN_RESIDUAL or w.upper() in EN_RESIDUAL_UPPER:
            found.append(w)
        elif w.isupper() and len(w) >= 4 and w in EN_RESIDUAL_UPPER:
            found.append(w)
    return found


def is_mixed_vi_en(text: str) -> bool:
    """Có dấu/tiếng Việt + còn từ EN kỹ thuật rõ ràng."""
    if not text or not text.strip():
        return False
    if not VI_CHARS.search(text):
        return False
    return bool(residual_english_tokens(text))


def scan_text_lines(text: str) -> List[Tuple[str, List[str]]]:
    """Trả [(dòng, [token EN sót]), ...]"""
    out: List[Tuple[str, List[str]]] = []
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s:
            continue
        toks = residual_english_tokens(s)
        if toks and VI_CHARS.search(s):
            out.append((s, toks))
    return out
