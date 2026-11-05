from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

from src.models import Glossary, GlossaryEntry

DOMAIN_TERMS = [
    "harness", "connector", "relay", "solenoid", "sensor", "actuator",
    "battery", "starter", "alternator", "ground", "fuse", "wiring",
    "switch", "valve", "pilot", "cylinder", "motor", "pump", "ecu",
    "controller", "wiring diagram", "electric", "electrical", "voltage",
    "circuit", "terminal", "socket", "plug", "cable", "lamp", "light",
]

PART_NUMBER_PATTERN = re.compile(r"YT\d{2}[A-Z]\d{5}[A-Z]\d")


def load_seed_glossary(seeds_path: Path) -> Glossary:
    glossary = Glossary(version="0.1", entries=[])
    if not seeds_path.exists():
        return glossary
    with seeds_path.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for i, row in enumerate(reader, 1):
            term_en = (row.get("term_en") or "").strip()
            if not term_en:
                continue
            status = (row.get("status") or "approved").strip()
            dnt = (row.get("do_not_translate") or "").strip().lower() in ("true", "1", "yes")
            glossary.entries.append(
                GlossaryEntry(
                    id=f"seed-{i:03d}",
                    term_en=term_en,
                    term_vi=(row.get("term_vi") or "").strip() or None,
                    variants_en=[v.strip() for v in (row.get("variants_en") or "").split("|") if v.strip()],
                    do_not_translate=dnt,
                    domain=(row.get("domain") or "electrical").strip(),
                    status=status,
                    source="seed",
                )
            )
    return glossary


def extract_terms_from_text(text: str) -> Counter:
    counts: Counter = Counter()
    lower = text.lower()
    for term in DOMAIN_TERMS:
        n = len(re.findall(rf"\b{re.escape(term)}\b", lower))
        if n:
            counts[term] = n
    for pn in PART_NUMBER_PATTERN.findall(text):
        counts[pn] += 1
    return counts


def extract_from_chunks(chunks_dir: Path, existing: Glossary) -> Glossary:
    existing_lower = {e.term_en.lower() for e in existing.entries}
    combined = Counter()
    for path in chunks_dir.glob("chunk_*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        combined.update(extract_terms_from_text(data.get("text_en", "")))

    next_id = len(existing.entries) + 1
    for term, freq in combined.most_common():
        if term in existing_lower:
            for e in existing.entries:
                if e.term_en.lower() == term.lower():
                    e.frequency = max(e.frequency, freq)
            continue
        is_part = bool(PART_NUMBER_PATTERN.fullmatch(term))
        existing.entries.append(
            GlossaryEntry(
                id=f"ext-{next_id:03d}",
                term_en=term,
                frequency=freq,
                do_not_translate=is_part,
                status="protected" if is_part else "draft",
                source="extract",
            )
        )
        existing_lower.add(term.lower())
        next_id += 1
    return existing


def save_glossary(glossary: Glossary, workspace: Path) -> Path:
    path = workspace / "glossary" / "glossary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(glossary.model_dump_json(indent=2), encoding="utf-8")
    # CSV cho KTV review
    csv_path = workspace / "glossary" / "glossary_review.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["id", "term_en", "term_vi", "frequency", "status", "do_not_translate", "notes"])
        for e in sorted(glossary.entries, key=lambda x: -x.frequency):
            writer.writerow([e.id, e.term_en, e.term_vi or "", e.frequency, e.status, e.do_not_translate, e.notes])
    return path


def load_glossary(workspace: Path) -> Glossary:
    path = workspace / "glossary" / "glossary.json"
    if not path.exists():
        return Glossary()
    return Glossary.model_validate_json(path.read_text(encoding="utf-8"))
