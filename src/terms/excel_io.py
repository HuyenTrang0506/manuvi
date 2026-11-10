"""BL-US-06: proposed terms <-> Excel for the internal QA reviewer."""
from __future__ import annotations

from pathlib import Path
from typing import Iterable, Optional

from openpyxl import Workbook, load_workbook
from pydantic import BaseModel

from .store import STATUSES, TermRecord, TermStore, key_of, validate_record

COLUMNS = ["term_en", "term_vi", "status", "citation", "confidence", "approved_by", "protected", "version", "must_use_in_prompt"]


class RowIssue(BaseModel):
    row: int  # 1-based Excel row
    term_en: str
    code: str  # missing_vi | missing_citation | missing_approver | bad_status | conflict | bad_value | missing_term
    message: str


class ImportResult(BaseModel):
    ok: bool
    version: int
    applied: int = 0
    unchanged: int = 0
    issues: list[RowIssue] = []


_DANGEROUS = ("=", "+", "-", "@", "\t", "\r")


def _safe(v):
    """Neutralize formula injection: dangerous strings get a leading ' and are stored as strings."""
    if isinstance(v, str) and v.startswith(_DANGEROUS):
        return "'" + v
    return v


def _unsafe(s: str) -> str:
    return s[1:] if s.startswith("'") and s[1:].startswith(_DANGEROUS) else s


def write_terms_xlsx(records: Iterable[TermRecord], path: Path) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "terms"
    ws.append(COLUMNS)
    for r in records:
        ws.append([_safe(v) for v in (r.term_en, r.term_vi, r.status, r.citation, r.confidence,
                                      r.approved_by, "yes" if r.protected else "", r.version, "yes" if r.must_use_in_prompt else "")])
        for c in ws[ws.max_row]:
            if isinstance(c.value, str):
                c.data_type = "s"
    for col, w in zip("ABCDEFGHI", (32, 32, 12, 48, 11, 16, 10, 9, 14)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def export_proposed(store: TermStore, path: Path) -> Path:
    recs = [r for r in store.current().values() if r.status == "proposed"]
    return write_terms_xlsx(sorted(recs, key=lambda r: r.key), path)


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return _unsafe(str(v).strip())


_CODE = {"approved term has no Vietnamese": "missing_vi", "approved term has no citation": "missing_citation",
         "approved term has no approved_by": "missing_approver", "missing term_en": "missing_term"}


def import_terms_xlsx(store: TermStore, path: Path, reviewer: str = "") -> ImportResult:
    """Import edited workbook as ONE new version. All-or-nothing: any issue -> nothing is written."""
    wb = load_workbook(path, read_only=True, data_only=False)
    try:
        cells = list(wb.active.iter_rows())
    finally:
        wb.close()
    formula_rows = {n for n, row in enumerate(cells, start=1) if any(c.data_type == "f" for c in row)}
    rows = [tuple(c.value for c in row) for row in cells]
    if not rows:
        return ImportResult(ok=False, version=store.version,
                            issues=[RowIssue(row=1, term_en="", code="missing_term", message="empty workbook")])
    header = [_s(h).lower() for h in rows[0]]
    missing = [c for c in ("term_en", "term_vi", "status") if c not in header]
    if missing:
        return ImportResult(ok=False, version=store.version, issues=[
            RowIssue(row=1, term_en="", code="bad_value", message=f"missing columns: {missing}")])
    idx = {h: i for i, h in enumerate(header)}

    def cell(row, name):
        i = idx.get(name)
        return row[i] if i is not None and i < len(row) else None

    issues: list[RowIssue] = []
    parsed: list[tuple[int, TermRecord]] = []
    for n, row in enumerate(rows[1:], start=2):
        if all(v is None or _s(v) == "" for v in row):
            continue
        term_en = _s(cell(row, "term_en"))
        if n in formula_rows:
            issues.append(RowIssue(row=n, term_en=term_en, code="formula",
                                   message="formula cells are not allowed"))
            continue
        status = _s(cell(row, "status")).lower()
        if status not in STATUSES:
            issues.append(RowIssue(row=n, term_en=term_en, code="bad_status",
                                   message=f"status {status!r} not in {STATUSES}"))
            continue
        try:
            conf_raw = cell(row, "confidence")
            conf = float(conf_raw) if _s(conf_raw) else 0.0
            if not 0.0 <= conf <= 1.0:
                raise ValueError
        except ValueError:
            issues.append(RowIssue(row=n, term_en=term_en, code="bad_value", message="confidence must be 0..1"))
            continue
        rec = TermRecord(
            term_en=term_en, term_vi=_s(cell(row, "term_vi")), status=status, citation=_s(cell(row, "citation")),
            confidence=conf, approved_by=_s(cell(row, "approved_by")),
            protected=_s(cell(row, "protected")).lower() in ("yes", "true", "1", "y", "x"),
            must_use_in_prompt=_s(cell(row, "must_use_in_prompt")).lower() in ("yes", "true", "1", "y", "x"))
        if rec.status == "approved" and not rec.approved_by:
            rec = rec.model_copy(update={"approved_by": reviewer})
        errs = validate_record(rec)
        for e in errs:
            issues.append(RowIssue(row=n, term_en=term_en, code=_CODE.get(e, "bad_value"), message=e))
        if not errs:
            parsed.append((n, rec))

    # duplicate EN with conflicting VI inside the sheet
    by_key: dict[str, list[tuple[int, TermRecord]]] = {}
    for n, rec in parsed:
        by_key.setdefault(rec.key, []).append((n, rec))
    dup_rows: set[int] = set()
    for k, items in by_key.items():
        vis = {r.term_vi.casefold() for _, r in items if r.term_vi}
        if len(items) > 1 and len(vis) > 1:
            for n, r in items:
                issues.append(RowIssue(row=n, term_en=r.term_en, code="conflict",
                                       message="duplicate term_en with conflicting term_vi"))
                dup_rows.add(n)
    if issues:
        return ImportResult(ok=False, version=store.version, issues=sorted(issues, key=lambda i: i.row))

    cur = store.current()
    to_write, unchanged, seen = [], 0, set()
    for n, rec in parsed:
        if rec.key in seen:
            unchanged += 1  # harmless exact duplicate
            continue
        seen.add(rec.key)
        old = cur.get(rec.key)
        if old and (old.term_vi, old.status, old.citation, old.confidence, old.approved_by, old.protected, old.must_use_in_prompt) == (
                rec.term_vi, rec.status, rec.citation, rec.confidence, rec.approved_by, rec.protected,
                rec.must_use_in_prompt):
            unchanged += 1
            continue
        sv = _s(cell(rows[n - 1], "version"))
        if old and sv.isdigit() and int(sv) < old.version:
            issues.append(RowIssue(row=n, term_en=rec.term_en, code="stale",
                                   message=f"sheet version {sv} is older than stored version {old.version}"))
            continue
        to_write.append(rec)
    if issues:
        return ImportResult(ok=False, version=store.version, issues=issues)
    version = store.commit(to_write) if to_write else store.version
    return ImportResult(ok=True, version=version, applied=len(to_write), unchanged=unchanged)
