"""Versioned, append-only term store (JSONL). Current state = latest record per term_en."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional

from pydantic import BaseModel, Field

from src.common.filelock import file_lock

Status = Literal["proposed", "approved", "rejected"]
STATUSES = ("proposed", "approved", "rejected")


def key_of(term_en: str) -> str:
    return " ".join(term_en.split()).casefold()


class TermRecord(BaseModel):
    term_en: str
    term_vi: str = ""
    status: Status = "proposed"
    citation: str = ""  # url/doc + note
    confidence: float = 0.0
    approved_by: str = ""
    version: int = 0
    protected: bool = False
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="seconds"))

    @property
    def key(self) -> str:
        return key_of(self.term_en)


def validate_record(r: TermRecord) -> list[str]:
    """Return problems that make a record unacceptable for the store."""
    errs = []
    if not r.term_en.strip():
        errs.append("missing term_en")
    if r.status == "approved":
        if not r.term_vi.strip():
            errs.append("approved term has no Vietnamese")
        if not r.citation.strip():
            errs.append("approved term has no citation")
        if not r.approved_by.strip():
            errs.append("approved term has no approved_by")
    return errs


class TermStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    def _read(self) -> list[TermRecord]:
        if not self.path.exists():
            return []
        out = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(TermRecord.model_validate_json(line))
        return out

    @property
    def version(self) -> int:
        return max((r.version for r in self._read()), default=0)

    def history(self, term_en: Optional[str] = None) -> list[TermRecord]:
        recs = self._read()
        if term_en is None:
            return recs
        return [r for r in recs if r.key == key_of(term_en)]

    def current(self) -> dict[str, TermRecord]:
        cur: dict[str, TermRecord] = {}
        for r in self._read():  # file order == chronological
            cur[r.key] = r
        return cur

    def get(self, term_en: str) -> Optional[TermRecord]:
        return self.current().get(key_of(term_en))

    def approved(self) -> list[TermRecord]:
        return [r for r in self.current().values() if r.status == "approved"]

    def commit(self, records: Iterable[TermRecord]) -> int:
        """Append all records as one new version, atomically. Raises ValueError (nothing written) on invalid input."""
        recs = list(records)
        problems = [f"{r.term_en!r}: {e}" for r in recs for e in validate_record(r)]
        if problems:
            raise ValueError("; ".join(problems))
        if not recs:
            return self.version
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with file_lock(self.path):
            new_version = self.version + 1
            lines = "".join(
                r.model_copy(update={"version": new_version}).model_dump_json() + "\n" for r in recs)
            old = self.path.read_text(encoding="utf-8") if self.path.exists() else ""
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(old + lines, encoding="utf-8")
            tmp.replace(self.path)
        return new_version

    def propose(self, candidates: Iterable[dict]) -> int:
        """Add new unseen candidates as status=proposed (existing terms are left alone)."""
        seen = set(self.current())
        new = []
        for c in candidates:
            k = key_of(c["term"])
            if k in seen:
                continue
            seen.add(k)
            new.append(TermRecord(term_en=c["term"], protected=bool(c.get("protected", False))))
        return self.commit(new) if new else self.version
