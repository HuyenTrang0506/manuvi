"""Eval-set format: JSONL cases frozen by a content hash, plus the proposed-glossary loader."""
from __future__ import annotations

import csv
import hashlib
import json
import unicodedata
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_SET = DATA_DIR / "electrical_v1.jsonl"
DEFAULT_GLOSSARY = DATA_DIR / "electrical_terms_proposed.csv"


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


class SetModified(Exception):
    """The eval set no longer matches its frozen manifest."""


class EvalCase(BaseModel):
    id: str
    page: int = 1                                   # synthetic page, used for cost per page
    source: str                                     # EN text
    domains: list[str] = Field(min_length=1)        # e.g. ["electrical", "safety"]; never a section number
    priority: Literal["p0", "p1", "p2"] = "p1"      # p0 = founder-critical
    severity: Literal["critical", "major", "minor"] = "major"
    expected_terms: list[str] = []                  # EN glossary keys that must appear in VI
    protected: list[str] = []                       # strings that must survive byte for byte
    reference_vi: Optional[str] = None              # optional human reference
    notes: str = ""

    @field_validator("source", "reference_vi")
    @classmethod
    def _nfc(cls, v):
        return nfc(v) if isinstance(v, str) else v

    @field_validator("protected")
    @classmethod
    def _nfc_list(cls, v):
        return [nfc(x) for x in v]


def canonical(case: EvalCase) -> str:
    return json.dumps(case.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def set_hash(cases: list[EvalCase]) -> str:
    h = hashlib.sha256()
    for c in cases:
        h.update(canonical(c).encode("utf-8") + b"\n")
    return h.hexdigest()


def manifest_path(path: Path) -> Path:
    return path.with_suffix(".manifest.json")


def read_cases(path: Path | str) -> list[EvalCase]:
    cases = [EvalCase.model_validate_json(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
             if line.strip()]
    ids = [c.id for c in cases]
    dup = sorted({i for i in ids if ids.count(i) > 1})
    if dup:
        raise ValueError(f"duplicate case ids: {dup}")
    return cases


def freeze(path: Path | str) -> dict:
    """Write the manifest (name, sha256, n_cases). Changing a frozen set means a new version + new baseline."""
    path = Path(path)
    cases = read_cases(path)
    m = {"name": path.stem, "sha256": set_hash(cases), "n_cases": len(cases)}
    manifest_path(path).write_text(json.dumps(m, indent=2) + "\n", encoding="utf-8")
    return m


def load_cases(path: Path | str = DEFAULT_SET, verify: bool = True) -> list[EvalCase]:
    path = Path(path)
    cases = read_cases(path)
    if verify:
        mp = manifest_path(path)
        if not mp.exists():
            raise SetModified(f"{path.name} has no manifest; run `manuvi eval freeze`")
        m = json.loads(mp.read_text(encoding="utf-8"))
        if m["sha256"] != set_hash(cases) or m["n_cases"] != len(cases):
            raise SetModified(f"{path.name} differs from its frozen manifest; create a new version instead of editing")
    return cases


class GlossaryTerm(BaseModel):
    term_en: str
    term_vi: str
    status: str = "proposed"
    domain: str = "electrical"
    forbidden_vi: list[str] = []
    note: str = ""
    must_use_in_prompt: bool = False   # inject into the prompt whenever the term occurs (verbs, key nouns)


def load_glossary(path: Path | str = DEFAULT_GLOSSARY) -> dict[str, GlossaryTerm]:
    """EN key (casefolded) -> term. Columns follow the review template; `term_vi_proposed` is a proposal."""
    out: dict[str, GlossaryTerm] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            vi = row.get("term_vi_proposed") or row.get("term_vi") or ""
            t = GlossaryTerm(term_en=row["term_en"].strip(), term_vi=nfc(vi.strip()),
                             status=row.get("status", "proposed") or "proposed",
                             domain=row.get("domain", "electrical") or "electrical",
                             forbidden_vi=[nfc(x.strip()) for x in (row.get("forbidden_vi") or "").split("|") if x.strip()],
                             note=row.get("note", ""),
                             must_use_in_prompt=(row.get("must_use_in_prompt") or "").strip().lower() in ("yes", "true", "1", "y", "x"))
            out[" ".join(t.term_en.split()).casefold()] = t
    return out


def glossary_hash(g: dict[str, GlossaryTerm]) -> str:
    blob = json.dumps([g[k].model_dump() for k in sorted(g)], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()
