"""Quét thuật ngữ EN chưa dịch (nhãn + dòng list/BOM ngắn)."""

from __future__ import annotations

import csv
import json
import re
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from src.ingest.parser import load_document
from src.models import BlockType
from src.qa.label_validator import is_mixed_translation
from src.translate.chunker import load_chunks

VI = re.compile(
    r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]",
    re.I,
)
PART_NUM = re.compile(r"^(?:Y[A-Z]|LE|LS|LC|EN|CM|CB)\d", re.I)
CODE_ONLY = re.compile(r"^[A-Z]{1,4}-\d+[A-Z]?$", re.I)
SKIP = re.compile(
    r"^(Fig\.|YES|NO|FULL|SLOW|ERROR|OPTION|NORMAL|OFF|POSITION|SENSOR|PIN|"
    r"TROUBLE|PANEL|OTHERS|NAME|PART NO\.?|Q'TY|NO\.|CODE|BLACK|GREEN|RED)$",
    re.I,
)
FLOW = re.compile(
    r"\b(is|are|when|if|the|between|replaced|inspect|trouble|lever|neutral|"
    r"pressure|approx|shifted|disappears)\b",
    re.I,
)


@dataclass
class UntranslatedTerm:
    term_en: str
    count: int
    sample_vi: str
    sample_ref: str
    kind: str  # label | list | mixed


def _norm_key(en: str) -> str:
    t = re.sub(r"\s+", " ", en.strip())
    t = re.sub(r"\.{2,}.+$", "", t)
    return t.upper().strip(" .")


def _needs_translation(en: str, vi: Optional[str]) -> Tuple[bool, str]:
    en = (en or "").strip()
    vi = (vi or "").strip()
    if not en or len(en) < 3 or len(en) > 70:
        return False, ""
    if PART_NUM.match(en) or CODE_ONLY.match(en) or SKIP.match(en):
        return False, ""
    if re.match(r"^\d+$", en) or re.match(r"^\d{2}-\d+", en):
        return False, ""
    # vendor OCR noise with digits — keep as-is unless it's a named part
    if re.search(r"\d{4,}", en) and not re.search(r"[A-Za-z]{4,}", en.replace("TARMINAL", "TERMINAL")):
        return False, ""
    if FLOW.search(en) and len(en.split()) > 5:
        return False, ""  # flowchart prose

    body = vi
    if ":" in en and re.match(r"^[A-Z]{1,4}-\d+", en, re.I):
        en = en.split(":", 1)[1].strip()
        body = vi.split(":", 1)[-1].strip() if vi else ""

    if not re.search(r"[A-Za-z]{3,}", en):
        return False, ""

    if not body or body.upper() == en.upper():
        return True, "untranslated"
    if is_mixed_translation(vi) or is_mixed_translation(body):
        return True, "mixed"
    # VI không dấu nhưng còn EN hoa
    if not VI.search(body) and re.search(r"\b[A-Z]{4,}\b", body):
        return True, "mixed"
    return False, ""


def collect_untranslated(workspace: Path) -> List[UntranslatedTerm]:
    document = load_document(workspace)
    counter: Counter = Counter()
    samples: Dict[str, Tuple[str, str, str]] = {}

    # Map dòng EN → VI từ chunks (đáng tin hơn block labels trống)
    line_vi: Dict[str, str] = {}
    for c in load_chunks(workspace):
        if not c.text_en:
            continue
        en_lines = c.text_en.split("\n")
        vi_lines = (c.text_vi or "").split("\n")
        for i, ln in enumerate(en_lines):
            key = _norm_key(ln)
            if key and i < len(vi_lines) and vi_lines[i].strip():
                line_vi[key] = vi_lines[i].strip()

    def add(en: str, vi: Optional[str], ref: str, kind: str):
        en_s = (en or "").strip()
        if not vi:
            vi = line_vi.get(_norm_key(en_s))
        ok, issue = _needs_translation(en_s, vi)
        if not ok:
            return
        key = _norm_key(en_s.split(":", 1)[-1] if ":" in en_s else en_s)
        if len(key) < 3:
            return
        counter[key] += 1
        if key not in samples:
            samples[key] = (en_s, vi or "", f"{ref}|{issue}")

    for page in document.pages:
        ref = page.manual_ref or f"p{page.pdf_page}"
        for block in page.blocks:
            for lb in block.labels or []:
                add(lb.text_en, lb.text_vi, ref, "label")
            text = block.text_en or ""
            if block.type in (BlockType.LIST, BlockType.PARAGRAPH, BlockType.TABLE) and text.count("\n") >= 1:
                if len(text) < 800:
                    for ln in text.split("\n"):
                        add(ln.strip(), None, ref, "list")

    for c in load_chunks(workspace):
        if not c.text_en or c.text_en.count("\n") < 1 or len(c.text_en) > 900:
            continue
        en_lines = c.text_en.split("\n")
        vi_lines = (c.text_vi or "").split("\n")
        ref = c.manual_ref or c.chunk_id
        for i, ln in enumerate(en_lines):
            add(ln, vi_lines[i] if i < len(vi_lines) else "", ref, "list")

    out: List[UntranslatedTerm] = []
    for key, n in counter.most_common():
        en, vi, meta = samples[key]
        ref, _, kind = meta.partition("|")
        out.append(
            UntranslatedTerm(
                term_en=key,
                count=n,
                sample_vi=vi[:80],
                sample_ref=ref,
                kind=kind or "untranslated",
            )
        )
    return out


def write_untranslated_report(workspace: Path, terms: List[UntranslatedTerm]) -> Tuple[Path, Path, Path]:
    qa = workspace / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    jp = qa / "untranslated_terms.json"
    mp = qa / "untranslated_terms.md"
    cp = qa / "untranslated_terms.csv"
    jp.write_text(
        json.dumps([asdict(t) for t in terms], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    lines = [
        f"# Untranslated EN terms",
        f"**Ngày:** {date.today()} | **Số cụm:** {len(terms)}",
        "",
        "| Count | Kind | Term EN | Sample VI | Ref |",
        "|------:|------|---------|-----------|-----|",
    ]
    for t in terms[:400]:
        lines.append(
            f"| {t.count} | {t.kind} | {t.term_en[:50]} | "
            f"{(t.sample_vi or '—').replace('|', '/')[:40]} | {t.sample_ref} |"
        )
    mp.write_text("\n".join(lines), encoding="utf-8")
    with cp.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["term_en", "term_vi", "variants_en", "do_not_translate", "domain", "status"])
        for t in terms:
            # stub VI trống — KTV / agent điền
            if re.search(r"\d{4,}", t.term_en) and "TARMINAL" not in t.term_en:
                continue  # part/vendor numbers
            w.writerow([t.term_en.title(), "", "", "false", "electrical", "draft"])
    return jp, mp, cp
