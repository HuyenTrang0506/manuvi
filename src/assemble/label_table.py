"""Định dạng bảng nhãn sơ đồ — dùng chung DOCX và PDF."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.ingest.text_structure import clean_for_xml
from src.translate.equipment_list import GROUP_PREFIX, lookup_equipment_code, vi_meaning_only
from src.translate.wire_labels import is_wire_label
from src.translate.harness_callouts import HARNESS_CALLOUTS, is_harness_figure
from src.qa.label_validator import is_untranslated
from src.qa.mix_detector import is_mixed_vi_en

_VI = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)

# Vị trí cột PDF (point, A4 margin=50) — mỗi cột = textbox riêng, thẳng hàng
PDF_LABEL_COLS: List[Tuple[float, float]] = [
    (50, 108),   # Mã
    (110, 295),  # Tên EN
    (298, 545),  # Nghĩa VI
]
PDF_WIRE_COLS: List[Tuple[float, float]] = [
    (50, 130),   # Mã dây
    (135, 545),  # Giải thích VI
]
PDF_LABEL_HEADERS = ("Mã", "Tên thiết bị (EN)", "Nghĩa tiếng Việt")
PDF_WIRE_HEADERS = ("Mã dây", "Giải thích")

LABELED = re.compile(r"^([A-Z]{1,4}-\d+[A-Z]?)\s*:\s*(.+)$", re.I)
LABELED_PIPE = re.compile(r"^([A-Z]{1,4}-\d+[A-Z]?)\s*\|\s*(.+)$", re.I)
PURE_CODE = re.compile(r"^[A-Z]{1,4}-\d+[A-Z]?$|^[WGRBY]{1,3}$|^\d{1,4}$", re.I)
CODE_NUM = re.compile(r"^([A-Z]{1,4})-(\d+)([A-Z]?)$", re.I)
PIN_NUM = re.compile(r"^\d{1,4}$")
NOISE_LABEL = re.compile(
    r"^(FROM\s*\d|TO\s*\d|TERMINAL|OPTION|POSITION|MODE|NOTES?|SIGN|COLOR|"
    r"WIRER?|TABLE|OVER|DOUBLE|SINGLE|SELECT|KEY SW\.?|GB|GRB|BO)$",
    re.I,
)
# Mã màu / chữ cái ngắn không phải thiết bị — đưa sang bảng dây hoặc bỏ
ORPHAN_COLOR = re.compile(
    r"^(?:B|G|L|R|W|Y|P|O|V|Br|Lg|Gr|Sb|[BGRWYLPOV]{2,4}|Br[YLRBWG]|Lg[BRWY]|Gr[LRBWY])$",
    re.I,
)

# Nhóm chức năng (thứ tự hiển thị)
GROUP_ORDER = [
    ("C", "Bộ điều khiển"),
    ("SW", "Công tắc"),
    ("SE", "Cảm biến"),
    ("R", "Rơ-le / bộ hẹn giờ"),
    ("SV", "Van điện từ"),
    ("PSV", "Van điện từ tỷ lệ"),
    ("L", "Đèn"),
    ("M", "Mô-tơ / bơm"),
    ("E", "Phụ kiện điện"),
    ("D", "Điode"),
    ("CN", "Đầu nối"),
]
GROUP_TITLE = {p: t for p, t in GROUP_ORDER}
INDICATOR_TITLE = "Đèn báo / chỉ thị trên cụm đồng hồ"
OTHER_TITLE = "Khác"


@dataclass
class LegendRow:
    code: str
    name_en: str
    meaning_vi: str
    suspicion: bool = False


@dataclass
class LegendSection:
    title: str
    rows: List[LegendRow] = field(default_factory=list)


def split_label(en: str) -> Tuple[str, str, str]:
    """
    Tách nhãn: SW-19:OPT... → ('SW-19', 'OPT.PEDAL...', 'SW-19: OPT.PEDAL...')
    C-1 → ('C-1', 'MECHATRO CONTROLLER', 'C-1')
    """
    en = en.strip()
    m = LABELED.match(en) or LABELED_PIPE.match(en)
    if m:
        code, desc = m.group(1).upper(), m.group(2).strip()
        return code, desc, f"{code}: {desc}"
    if PURE_CODE.match(en) and not PIN_NUM.match(en):
        hit = lookup_equipment_code(en)
        if hit:
            name_en, _ = hit
            return en.upper(), name_en, en
        return en.upper(), "", en
    return "", en, en


def sort_labels(labels: list) -> list:
    def key(lb):
        code, desc, _ = split_label(lb.text_en)
        if code and desc:
            return (1, code)
        if code:
            return (0, code)
        return (2, lb.text_en.lower())

    return sorted(labels, key=key)


def is_pin_or_noise(text: str) -> bool:
    """Số chân / line No. / nhiễu — không đưa vào bảng tra cứu."""
    t = text.strip()
    if not t:
        return True
    if PIN_NUM.match(t):
        return True
    if NOISE_LABEL.match(t):
        return True
    if re.match(r"^\d{1,3}-\d{1,3}$", t):
        return True
    return False


def partition_labels(labels: list) -> Tuple[list, list, list]:
    """Tách: (thiết bị/đèn báo, mã dây, bỏ qua)."""
    components, wires, skipped = [], [], []
    for lb in labels:
        en = (lb.text_en or "").strip()
        if not en:
            continue
        if is_pin_or_noise(en):
            skipped.append(lb)
        elif is_wire_label(en) or ORPHAN_COLOR.match(en):
            # Màu thuần (BRW, GrB…) → bảng dây
            wires.append(lb)
        else:
            components.append(lb)
    return sort_labels(components), sort_labels(wires), skipped


def format_vi_meaning(lb) -> str:
    en = (lb.text_en or "").strip()
    try:
        from src.translate.section43_map import lookup_section43

        mapped = lookup_section43(en)
        if mapped:
            return clean_for_xml(mapped)
    except Exception:
        pass
    raw = lb.text_vi or lb.text_en or ""
    vi = clean_for_xml(vi_meaning_only(raw))
    if getattr(lb, "suspicion", False) and lb.guess_note:
        vi = f"{vi}\n({lb.guess_note})"
    return vi


def format_vi_cell(lb) -> str:
    vi = clean_for_xml(lb.text_vi or lb.text_en)
    if getattr(lb, "suspicion", False) and lb.guess_note:
        vi = f"{vi}\n({lb.guess_note})"
    return vi


def wire_code_display(lb) -> str:
    return clean_for_xml((lb.text_en or "").strip())


def wire_meaning_display(lb) -> str:
    raw = (lb.text_vi or lb.text_en or "").strip()
    if "—" in raw:
        return clean_for_xml(raw.split("—", 1)[1].strip())
    if " - " in raw:
        return clean_for_xml(raw.split(" - ", 1)[1].strip())
    return format_vi_meaning(lb)


def _norm_meaning(vi: str) -> str:
    t = vi.strip().lower()
    t = re.sub(r"\s+", " ", t)
    return t


def _clean_en_name(desc: str) -> str:
    """Bỏ rác OCR dính sau tên (màu dây, số chân)."""
    if not desc:
        return ""
    d = re.sub(r"[\x00-\x1f]", " ", desc)
    d = re.split(
        r"\s+(?:\d{2,3}\s+)?(?:[WGRBYLPOV]{1,3}|Br[YLRB]|Lg[BR]|Gr[LRB]|GREEN|RED|BLACK|BLUE)\b",
        d,
        maxsplit=1,
        flags=re.I,
    )[0]
    d = re.sub(r"\s+\d{2,3}(?:\s+\d{2,3})*$", "", d)
    d = re.sub(r"\s+", " ", d).strip(" .,;:")
    if len(d) > 42:
        d = d[:40].rstrip() + "…"
    return d


_EXTRA_SW_PHRASES = {
    "key sw.": "công tắc chìa khóa",
    "key sw": "công tắc chìa khóa",
    "starter key sw.": "công tắc chìa khởi động",
    "starter key sw": "công tắc chìa khởi động",
    "lever lock sw.": "công tắc khóa cần",
    "lever lock sw": "công tắc khóa cần",
    "eng.water temp.sw.": "công tắc nhiệt nước làm mát",
    "eng. water temp. sw.": "công tắc nhiệt nước làm mát",
    "eng.water temp.sw": "công tắc nhiệt nước làm mát",
    "air filter restriction sw.": "công tắc tắc lọc gió",
    "air filter restriction sw": "công tắc tắc lọc gió",
    "travel two-speed sw.": "công tắc tốc độ di chuyển 2",
    "travel two-speed sw": "công tắc tốc độ di chuyển 2",
    "travel two-speed sol.": "van điện từ tốc độ di chuyển 2",
    "travel two-speed sol": "van điện từ tốc độ di chuyển 2",
    "travel two-speed relay": "rơ-le tốc độ di chuyển 2",
    "travel two-speed": "tốc độ di chuyển 2",
    "auto accel release sw.": "công tắc nhả ga tự động",
    "auto accel release sw": "công tắc nhả ga tự động",
    "auto accel release switch": "công tắc nhả ga tự động",
    "sequence box assy": "hộp tuần tự (lắp ráp)",
    "sequence box": "hộp tuần tự",
    "over load press adjust unit": "bộ chỉnh áp quá tải",
    "overload press adjust": "chỉnh áp quá tải",
    "p1 by-pass cut propotional sol.": "van tỷ lệ cắt bypass P1",
    "p1 by-pass cut proportional sol.": "van tỷ lệ cắt bypass P1",
    "p2 by-pass cut propotional sol.": "van tỷ lệ cắt bypass P2",
    "p2 by-pass cut proportional sol.": "van tỷ lệ cắt bypass P2",
    "travel priority propotional sol.": "van tỷ lệ ưu tiên di chuyển",
    "travel priority proportional sol.": "van tỷ lệ ưu tiên di chuyển",
    "select sol.": "van điện từ chọn",
    "select sol": "van điện từ chọn",
    "horn sw.(left)": "công tắc còi (trái)",
    "horn sw.(right)": "công tắc còi (phải)",
    "horn sw. (left)": "công tắc còi (trái)",
    "horn sw. (right)": "công tắc còi (phải)",
    "horn sw left": "công tắc còi trái",
    "horn sw right": "công tắc còi phải",
    "double, single": "chọn kép / đơn",
    "double single select sw.": "công tắc chọn kép/đơn",
    "double, single select sw.": "công tắc chọn kép/đơn",
    "cab work light": "đèn làm việc buồng lái",
    "cabin": "buồng lái",
    "mounting cab work light": "lắp đèn làm việc buồng lái",
    "cab harness assy": "lắp ráp bộ dây buồng lái",
}


def _lookup_extra_phrase(desc: str) -> Optional[str]:
    low = re.sub(r"\s+", " ", desc.strip().lower().rstrip("."))
    if low in _EXTRA_SW_PHRASES:
        return _EXTRA_SW_PHRASES[low]
    # thử bỏ số chân / màu ở cuối
    cleaned = _clean_en_name(desc).lower().rstrip(".")
    return _EXTRA_SW_PHRASES.get(cleaned) or _EXTRA_SW_PHRASES.get(cleaned + ".")


def _is_generic_group_meaning(vi: str, prefix: str) -> bool:
    """Chỉ 'Công tắc' / 'Rơ-le' — chưa có tên cụ thể → được gom range."""
    n = _norm_meaning(vi)
    grp = GROUP_PREFIX.get(prefix.upper())
    if not grp:
        return False
    _, grp_vi = grp
    return n == grp_vi.lower() or n in {
        "công tắc", "rơ-le", "cảm biến", "đèn", "mô-tơ", "van điện từ",
        "điode", "đầu nối", "bộ điều khiển", "phụ kiện điện",
    }


def _format_code_ranges(prefix: str, nums: List[int], suffix: str = "") -> str:
    """[1,4,6,7,8] → 'SW-1, SW-4, SW-6→8'."""
    if not nums:
        return prefix
    nums = sorted(set(nums))
    ranges: List[Tuple[int, int]] = []
    start = prev = nums[0]
    for n in nums[1:]:
        if n == prev + 1:
            prev = n
            continue
        ranges.append((start, prev))
        start = prev = n
    ranges.append((start, prev))

    parts = []
    for a, b in ranges:
        if a == b:
            parts.append(f"{prefix}-{a}{suffix}")
        else:
            parts.append(f"{prefix}-{a}→{b}{suffix}")
    return ", ".join(parts)


def _prefix_of(code: str) -> Optional[str]:
    m = CODE_NUM.match(code.strip())
    if not m:
        return None
    return m.group(1).upper()


def _section_title_for(prefix: Optional[str], has_specific: bool, is_indicator: bool) -> str:
    if is_indicator:
        return INDICATOR_TITLE
    if prefix and prefix in GROUP_TITLE:
        return GROUP_TITLE[prefix]
    return OTHER_TITLE


def _is_indicator_text(en: str, code: str) -> bool:
    if code:
        return False
    return bool(
        re.search(
            r"ILLUMINATION|CHARGE|OIL PRESS|WATER TEMP|AIR CLEANER|FUEL LEVEL|"
            r"FUEL METER|GLOW|TRAVEL TWO|SWING PARKING|CPU ALLERGY|OVERLOAD|"
            r"COOLANT THERMO|CONTROLLER EARTH",
            en,
            re.I,
        )
    )


def code_sort_key(code: str) -> tuple:
    """Sort: letter prefix → số → chuỗi. SW-1, SW-2, … SW-10; rồi đèn báo không mã."""
    c = (code or "").strip()
    m = re.match(r"^([A-Z]{1,4})-(\d+)", c, re.I)
    if m:
        return (0, m.group(1).upper(), int(m.group(2)), c.upper())
    # Range đã gom: SW-1, SW-3→4 → lấy mã đầu
    m2 = re.match(r"^([A-Z]{1,4})-(\d+)", c.replace("→", "-"), re.I)
    if m2:
        return (0, m2.group(1).upper(), int(m2.group(2)), c.upper())
    return (1, "ZZZ", 0, c.lower())


def build_structured_legend(labels: list) -> List[LegendSection]:
    """
    Bảng tra cứu phẳng — sort theo mã (chữ cái → số):
    C-1, C-2, D-1→7, E-1, … SW-1, SW-2, …
    Bỏ số chân; gom mã cùng nghĩa chung (D-1→7: Điode).
    """
    # OCR dump đoạn văn (vd. trang 43-6 thành "labels") — không làm legend
    if labels and len(labels) >= 25:
        n_code = sum(
            1
            for lb in labels
            if re.match(r"^[A-Z]{1,4}-\d+", (lb.text_en or "").strip())
        )
        if n_code < max(3, len(labels) * 0.08):
            return []

    components, _wires, _skip = partition_labels(labels)

    specific: List[LegendRow] = []
    generic: Dict[Tuple[str, str], List[int]] = {}
    generic_suffix: Dict[Tuple[str, str], str] = {}
    named_codes: set[str] = set()
    seen_row: set[Tuple[str, str]] = set()

    for lb in components:
        en = (lb.text_en or "").strip()
        if is_pin_or_noise(en):
            continue
        code, desc, _ = split_label(en)
        meaning = format_vi_meaning(lb).split("\n")[0].strip()
        if not meaning or PIN_NUM.match(meaning):
            continue
        if meaning == en and PIN_NUM.match(en):
            continue

        prefix = _prefix_of(code) if code else None

        if code:
            hit = lookup_equipment_code(code)
            if hit:
                name_en, name_vi = hit
                if not desc or len(desc) < 3:
                    desc = name_en
                    meaning = name_vi
                elif _is_generic_group_meaning(meaning, prefix or ""):
                    desc = name_en
                    meaning = name_vi

        if desc:
            desc = _clean_en_name(desc)
            extra = _lookup_extra_phrase(desc)
            if extra:
                meaning = extra

        # Bỏ nhiễu "Khác" / heading
        if not code and (
            "ELECTRICAL" in en.upper()
            or re.match(r"^23\.", en)
            or re.match(r"^YT\d", en)
            or "mm²" in meaning
        ):
            continue

        generic_en = {
            "DIODE", "RELAY", "SWITCH", "SENSOR", "LIGHT", "MOTOR",
            "SOLENOID", "CONTROLLER",
        }
        desc_is_generic = (not desc) or desc.upper().rstrip(".") in generic_en

        if code and desc and not desc_is_generic and not _is_generic_group_meaning(meaning, prefix or ""):
            named_codes.add(code.upper())

        if (
            code
            and prefix
            and desc_is_generic
            and _is_generic_group_meaning(meaning, prefix)
        ):
            m = CODE_NUM.match(code)
            if m:
                key = (prefix, _norm_meaning(meaning))
                generic.setdefault(key, []).append(int(m.group(2)))
                generic_suffix[key] = m.group(3) or ""
                continue

        row_key = (code.upper() if code else en.upper(), _norm_meaning(meaning))
        if row_key in seen_row:
            continue
        seen_row.add(row_key)

        name_en = _clean_en_name(desc) if desc else ("" if code else en)
        specific.append(
            LegendRow(
                code=code or "—",
                name_en=name_en,
                meaning_vi=meaning,
                suspicion=bool(getattr(lb, "suspicion", False)),
            )
        )

    for (prefix, meaning_key), nums in generic.items():
        filtered = [n for n in nums if f"{prefix}-{n}".upper() not in named_codes]
        if not filtered:
            continue
        grp = GROUP_PREFIX.get(prefix)
        meaning_vi = grp[1] if grp else meaning_key
        code_disp = _format_code_ranges(
            prefix, filtered, generic_suffix.get((prefix, meaning_key), "")
        )
        specific.append(
            LegendRow(code=code_disp, name_en="(cùng loại)", meaning_vi=meaning_vi)
        )

    # Dedup theo mã đơn (giữ bản sạch nhất)
    def _row_quality(r: LegendRow) -> tuple:
        vi_score = len(re.findall(r"[àáảãạăâêôơưđ]", r.meaning_vi, re.I))
        en_clean = 0 if re.search(r"\d{2}-\d|GB |GrB|  ", r.name_en or "") else 1
        mixed_pen = -5 if re.search(r"\b[A-Z]{4,}\b", r.meaning_vi) and re.search(
            r"[àáảãạăâêôơưđ]", r.meaning_vi, re.I
        ) else 0
        return (en_clean, vi_score + mixed_pen, -len(r.name_en or ""))

    best: Dict[str, LegendRow] = {}
    extras: List[LegendRow] = []
    for r in specific:
        if r.code == "—" or "→" in r.code or "," in r.code:
            extras.append(r)
            continue
        key = r.code.upper()
        if key not in best or _row_quality(r) > _row_quality(best[key]):
            best[key] = r

    # Dedup đèn báo (không mã) theo nghĩa VI
    ind_best: Dict[str, LegendRow] = {}
    for r in extras:
        if r.code == "—":
            k = _norm_meaning(r.meaning_vi)
            if k not in ind_best:
                ind_best[k] = r
        else:
            ind_best[f"range:{r.code}"] = r

    rows = list(best.values()) + list(ind_best.values())
    rows = [r for r in rows if _legend_row_keep(r)]
    rows.sort(key=lambda r: code_sort_key(r.code if r.code != "—" else r.name_en or r.meaning_vi))

    if not rows:
        return []
    return [LegendSection(title="Chú thích tra cứu (theo mã)", rows=rows)]


def _legend_row_keep(r: LegendRow) -> bool:
    """Chỉ giữ dòng đã dịch được — bỏ EN nguyên / mã linh tinh / BOM header vô nghĩa."""
    en = (r.name_en or "").strip()
    vi = (r.meaning_vi or "").strip()
    code = (r.code or "").strip()

    # Header BOM không cần trong chú thích thiết bị
    if en.upper() in {"NAME", "NO", "NO.", "PART NO", "PART NO.", "Q'TY", "QTY"}:
        return False
    if vi.lower() in {"tên", "stt", "stt.", "mã phụ tùng", "sl"}:
        return False

    # Part number thuần / kích thước (LA + ≥4 số = PN; LA105 kiểu đầu nối thì giữ)
    if re.match(r"^YT\d|^LE\d|^YAZAKI|^LA\d{4,}|\(\d+\.\d+\s*\)", en, re.I):
        return False
    if re.match(r"^\d+(\.\d+)?\s*\(", en):
        return False

    # Mã thuần 1 ký tự / không có nghĩa
    if code in {"—", "-"} and (not en or len(en) <= 2) and not _VI.search(vi):
        return False

    if not vi:
        return False

    # VI vẫn là EN → bỏ
    if is_untranslated(en or code, vi):
        return False
    if is_mixed_vi_en(vi):
        return False
    if vi.upper() == en.upper() and re.search(r"[A-Za-z]{3,}", vi):
        # Cho phép mã màu ngắn đã có nghĩa kiểu "B — đen" qua format khác
        if "—" not in vi and not _VI.search(vi):
            return False

    # Phải có dấu tiếng Việt hoặc là mã+nghĩa đã chuẩn hóa có "—"
    if not _VI.search(vi) and "—" not in vi:
        # vẫn cho phép vài viết tắt kỹ thuật đã dịch không dấu? bỏ an toàn
        if re.search(r"\b(NAME|PART|HARNESS|ASSY|SWITCH|RELAY|SENSOR|MOTOR|LIGHT)\b", vi, re.I):
            return False
        if re.search(r"[A-Za-z]{4,}", vi):
            return False

    return True


def build_harness_callout_section(
    *,
    caption_en: str = "",
    figure_id: str = "",
    force: bool = False,
    workspace: Optional[Path] = None,
) -> List[LegendSection]:
    """Bảng phụ: ưu tiên kết quả Vision GPT; fallback danh sách callout cố định.

    Co-page sibling legends (e.g. fig-23-12 on the same PDF page as fig-23-11)
    are merged and deduped by EN key so one vision table covers the full page.
    """
    if workspace is not None and figure_id:
        try:
            from src.translate.vision_legend import load_merged_vision_legend

            data = load_merged_vision_legend(workspace, figure_id)
            if data and data.get("labels"):
                rows = [
                    LegendRow(
                        code="—",
                        name_en=str(lb.get("en") or ""),
                        meaning_vi=str(lb.get("vi") or ""),
                    )
                    for lb in data["labels"]
                    if lb.get("en") and lb.get("vi")
                ]
                rows = [r for r in rows if _legend_row_keep(r)]
                if rows:
                    n_src = len(data.get("merged_from") or [])
                    title = "Nhãn đọc từ ảnh (Vision) — EN → VI"
                    if n_src > 1:
                        figs = ", ".join(
                            fid.replace("fig-", "", 1)
                            for fid in data["merged_from"]
                        )
                        title = f"Nhãn đọc từ ảnh (Vision) — Fig. {figs} — EN → VI"
                    return [LegendSection(title=title, rows=rows)]
        except Exception:
            pass

    if not force and not is_harness_figure(caption_en, figure_id):
        return []
    rows = [
        LegendRow(code="—", name_en=en, meaning_vi=vi)
        for en, vi in HARNESS_CALLOUTS
    ]
    return [LegendSection(title="Nhãn chữ trên sơ đồ (ảnh gốc → nghĩa VI)", rows=rows)]

