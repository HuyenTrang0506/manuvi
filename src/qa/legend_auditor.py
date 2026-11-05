"""Audit bảng chú thích legend — sort mã, thiếu VI, lẫn EN/VI, số chân."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import List

from src.assemble.label_table import build_structured_legend, code_sort_key, is_pin_or_noise
from src.ingest.parser import load_document
from src.qa.label_validator import is_mixed_translation
from src.models import BlockType

VI_CHARS = re.compile(r"[àáảãạăằắẳẵặâầấẩẫậèéẻẽẹêềếểễệìíĩịòóỏõọôồốổỗộơờớởỡợùúủũụưừứửữựỳýỷỹỵđ]", re.I)
CODE_RE = re.compile(r"^[A-Z]{1,4}-\d+", re.I)


@dataclass
class LegendFinding:
    figure_id: str
    code: str
    meaning: str
    issue: str  # mixed | missing_vi | pin | sort


@dataclass
class LegendAudit:
    figures: int
    rows: int
    mixed: int
    missing_vi: int
    pins: int
    sort_errors: int
    findings: List[LegendFinding]

    def to_dict(self):
        return {
            "figures": self.figures,
            "rows": self.rows,
            "mixed": self.mixed,
            "missing_vi": self.missing_vi,
            "pins": self.pins,
            "sort_errors": self.sort_errors,
            "findings": [asdict(f) for f in self.findings],
        }


def _has_vi(text: str) -> bool:
    if not text or not text.strip():
        return False
    if VI_CHARS.search(text):
        return True
    # Thuật ngữ đã chuẩn không dấu
    low = text.lower()
    return any(
        w in low
        for w in ("công tắc", "rơ-le", "cam bien", "cảm biến", "diode", "điode", "van điện")
    )


def audit_legend(workspace: Path) -> LegendAudit:
    document = load_document(workspace)
    findings: List[LegendFinding] = []
    mixed = missing = pins = sort_err = 0
    total_rows = 0
    fig_n = 0

    seen_figs: set[str] = set()
    for page in document.pages:
        for block in page.blocks:
            if block.type != BlockType.FIGURE:
                continue
            fid = block.figure_id or block.block_id
            if fid in seen_figs:
                continue
            labels = block.labels or []
            if not labels and block.figure_id and block.figure_id in document.figures_registry:
                labels = document.figures_registry[block.figure_id].labels
            if not labels:
                continue
            seen_figs.add(fid)
            fig_n += 1
            sections = build_structured_legend(labels)
            rows = [r for s in sections for r in s.rows]
            total_rows += len(rows)

            # Sort check
            keys = [code_sort_key(r.code if r.code != "—" else r.name_en or "") for r in rows]
            if keys != sorted(keys):
                sort_err += 1
                findings.append(
                    LegendFinding(fid, "(order)", "", "sort")
                )

            for r in rows:
                code = r.code
                meaning = r.meaning_vi or ""
                if is_pin_or_noise(code) or (code.isdigit() if code else False):
                    pins += 1
                    findings.append(LegendFinding(fid, code, meaning, "pin"))
                    continue
                if CODE_RE.match(code) and not _has_vi(meaning):
                    missing += 1
                    findings.append(LegendFinding(fid, code, meaning, "missing_vi"))
                elif is_mixed_translation(meaning) or is_mixed_translation(f"{code}: {meaning}"):
                    mixed += 1
                    findings.append(LegendFinding(fid, code, meaning, "mixed"))

    return LegendAudit(
        figures=fig_n,
        rows=total_rows,
        mixed=mixed,
        missing_vi=missing,
        pins=pins,
        sort_errors=sort_err,
        findings=findings[:300],
    )


def write_legend_audit(workspace: Path, audit: LegendAudit) -> tuple[Path, Path]:
    qa = workspace / "qa"
    qa.mkdir(parents=True, exist_ok=True)
    jp = qa / "legend_audit.json"
    mp = qa / "legend_audit.md"
    jp.write_text(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Legend audit",
        f"**Ngày:** {date.today()}",
        f"Figures: **{audit.figures}** | Rows: **{audit.rows}**",
        f"🔴 Lẫn EN/VI: **{audit.mixed}** | 🔵 Thiếu VI: **{audit.missing_vi}** | "
        f"🟠 Số chân: **{audit.pins}** | 🟡 Sort: **{audit.sort_errors}**",
        "",
    ]
    if audit.findings:
        lines += ["| Issue | Fig | Code | Meaning |", "|-------|-----|------|---------|"]
        for f in audit.findings[:150]:
            m = (f.meaning or "").replace("|", "\\|")[:50]
            lines.append(f"| {f.issue} | {f.figure_id} | {f.code} | {m} |")
    mp.write_text("\n".join(lines), encoding="utf-8")
    return jp, mp
