import json
import unicodedata

import pytest

from src.eval.cases import (DEFAULT_SET, EvalCase, SetModified, freeze, glossary_hash, load_cases,
                            load_glossary, set_hash)


def test_shipped_set_is_frozen_and_large_enough():
    cases = load_cases(DEFAULT_SET)
    assert len(cases) >= 40
    assert all(c.reference_vi for c in cases)
    assert {c.priority for c in cases} >= {"p0", "p1", "p2"}
    assert all("electrical" in c.domains for c in cases)


def test_expected_terms_exist_in_glossary_and_source():
    g = load_glossary()
    for c in load_cases():
        for t in c.expected_terms:
            assert t.casefold() in g, (c.id, t)


def test_glossary_status_sources_and_keep_english_rule():
    g = load_glossary()
    terms = [t for t in g.values() if not t.keep_english]
    names = [t for t in g.values() if t.keep_english]
    assert 60 <= len(terms) <= 75
    assert {t.status for t in terms} <= {"verified-usage", "proposed"}
    verified = [t for t in terms if t.status == "verified-usage"]
    assert 25 <= len(verified) < len(terms)
    # verified-usage needs a source URL; proposed terms need a note saying why
    assert all(t.source.startswith("https://") for t in verified)
    assert all(t.note for t in terms if t.status == "proposed")
    # founder rule: only true names/abbreviations may keep their English form; every other term is Vietnamese
    assert {t.term_en for t in names} >= {"ECU", "SOL", "SW", "GND", "B+"}
    assert all(t.term_vi == t.term_en and t.status == "keep-english" for t in names)
    for t in terms:
        assert t.term_vi and t.term_vi.casefold() != t.term_en.casefold(), t.term_en
        assert not any(v.casefold() == t.term_en.casefold() for v in t.variants_vi), t.term_en
    assert len(glossary_hash(g)) == 64


def test_variants_do_not_collide_across_canonical_forms_of_different_terms():
    # one canonical VI form must not belong to two different concepts (outside the known solenoid pair)
    g = load_glossary()
    seen: dict[str, str] = {}
    for t in g.values():
        if t.keep_english:
            continue
        key = t.term_vi.casefold()
        if key in seen and {seen[key], t.term_en} != {"solenoid", "solenoid valve"}:
            raise AssertionError((key, seen[key], t.term_en))
        seen[key] = t.term_en


def write_set(tmp_path, n=2):
    p = tmp_path / "s.jsonl"
    p.write_text("".join(json.dumps({"id": f"A{i}", "source": f"text {i}", "domains": ["electrical"]}) + "\n"
                         for i in range(n)), encoding="utf-8")
    return p


def test_freeze_then_modify_is_detected(tmp_path):
    p = write_set(tmp_path)
    freeze(p)
    assert len(load_cases(p)) == 2
    p.write_text(p.read_text().replace("text 1", "text 9"), encoding="utf-8")
    with pytest.raises(SetModified):
        load_cases(p)


def test_missing_manifest_refused_unless_unverified(tmp_path):
    p = write_set(tmp_path)
    with pytest.raises(SetModified):
        load_cases(p)
    assert len(load_cases(p, verify=False)) == 2


def test_duplicate_ids_rejected(tmp_path):
    p = tmp_path / "d.jsonl"
    line = json.dumps({"id": "X", "source": "a", "domains": ["electrical"]})
    p.write_text(line + "\n" + line + "\n")
    with pytest.raises(ValueError):
        freeze(p)


def test_hash_is_nfc_stable():
    nfd = unicodedata.normalize("NFD", "ắc quy")
    a = EvalCase(id="1", source="x", domains=["e"], reference_vi=nfd)
    b = EvalCase(id="1", source="x", domains=["e"], reference_vi=unicodedata.normalize("NFC", nfd))
    assert set_hash([a]) == set_hash([b])


def test_domain_required():
    with pytest.raises(Exception):
        EvalCase(id="1", source="x", domains=[])
