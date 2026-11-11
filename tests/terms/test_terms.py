import json

from openpyxl import load_workbook
from typer.testing import CliRunner

from src.terms import (TermRecord, TermStore, TranslationMemory, build_translation_prompt, export_proposed,
                       import_terms_xlsx, select_glossary_subset)
from src.terms.cli import app


def edit(path, fn):
    wb = load_workbook(path)
    ws = wb.active
    fn(ws)
    wb.save(path)


def seeded(tmp_path):
    s = TermStore(tmp_path / "t.jsonl")
    s.propose([{"term": "hydraulic pump"}, {"term": "pump"}, {"term": "YT12-AB3C", "protected": True}])
    return s


def fill(ws, rows):
    for r, (vi, st, cit) in rows.items():
        ws.cell(r, 2, vi); ws.cell(r, 3, st); ws.cell(r, 4, cit)


def test_roundtrip_and_history(tmp_path):
    s = seeded(tmp_path)
    x = tmp_path / "p.xlsx"
    export_proposed(s, x)
    edit(x, lambda ws: fill(ws, {2: ("bơm thủy lực", "approved", "Kobelco glossary p.3"),
                                 3: ("bơm", "approved", "doc X")}))
    res = import_terms_xlsx(s, x, reviewer="qa1")
    assert res.ok and res.applied == 2
    assert s.get("Hydraulic Pump").term_vi == "bơm thủy lực" and s.get("pump").approved_by == "qa1"
    assert s.version == 2 and len(s.history("pump")) == 2  # append-only
    assert import_terms_xlsx(s, x, "qa1").applied == 0  # idempotent


def test_invalid_rows_reported_atomic(tmp_path):
    s = seeded(tmp_path)
    x = tmp_path / "p.xlsx"
    export_proposed(s, x)
    edit(x, lambda ws: fill(ws, {2: ("", "approved", "c"), 3: ("bơm", "maybe", ""), 4: ("ok", "approved", "c")}))
    before = s.version
    res = import_terms_xlsx(s, x, "qa")
    codes = {i.code for i in res.issues}
    assert not res.ok and {"missing_vi", "bad_status"} <= codes
    assert s.version == before and s.get("YT12-AB3C").status == "proposed"  # nothing applied


def test_conflict_detection(tmp_path):
    s = seeded(tmp_path)
    x = tmp_path / "p.xlsx"
    export_proposed(s, x)
    def f(ws):
        ws.append(["Pump", "máy bơm", "approved", "c", 0.9, "qa", "", ""])
        fill(ws, {3: ("bơm", "approved", "c")})
    edit(x, f)
    res = import_terms_xlsx(s, x, "qa")
    assert not res.ok and sum(i.code == "conflict" for i in res.issues) == 2


def approved_store(tmp_path):
    s = TermStore(tmp_path / "a.jsonl")
    mk = lambda en, vi, p=False: TermRecord(term_en=en, term_vi=vi, status="approved", citation="src",
                                            approved_by="qa", protected=p)
    s.commit([mk("pump", "bơm"), mk("hydraulic pump", "bơm thủy lực"), mk("filter", "bộ lọc"),
              mk("YT12-AB3C", "YT12-AB3C"), mk("Secret", "x", True)])
    return s


def test_subset_longest_match_and_protected(tmp_path):
    s = approved_store(tmp_path)
    got = [r.term_en for r in select_glossary_subset("Check the Hydraulic Pump. Wire YT12-AB3C. Secret.", s)]
    assert got == ["hydraulic pump"]  # 'pump' covered by longer match; codes/protected excluded
    got = [r.term_en for r in select_glossary_subset("pump and hydraulic pump and filter", s, max_terms=2)]
    assert got == ["hydraulic pump", "filter"] or got == ["hydraulic pump", "pump"]
    assert len(got) == 2


def test_prompt_contents_and_tm(tmp_path):
    s = approved_store(tmp_path)
    chunk = "Replace the hydraulic pump ⟦P001⟧."
    tm = TranslationMemory()
    tm.add("Replace the hydraulic pump filter.", "Thay bộ lọc bơm thủy lực.")
    tm.add("Unrelated sentence entirely", "x")
    hits = tm.lookup("Replace the hydraulic pump filter")
    assert len(hits) == 1 and 0.85 <= hits[0].score < 1
    assert tm.lookup("REPLACE the hydraulic  pump filter.")[0].score == 1.0
    p = build_translation_prompt(chunk, select_glossary_subset(chunk, s), hits, "v7")
    sysmsg = p["messages"][0]["content"]
    assert p["prompt_version"] == "v7" and "v7" in sysmsg
    assert "hydraulic pump => bơm thủy lực" in sysmsg and "⟦Pnnn⟧" in sysmsg and "Do not invent" in sysmsg
    assert "Thay bộ lọc" in sysmsg and p["messages"][1] == {"role": "user", "content": chunk}


def test_cli_propose(tmp_path):
    tj = tmp_path / "terms.json"
    tj.write_text(json.dumps([{"term": "relay", "count": 2, "contexts": [], "pages": [1]},
                              {"term": "YT12-AB3C", "count": 1, "contexts": [], "pages": [1], "protected": True}]))
    out = tmp_path / "o.xlsx"
    r = CliRunner().invoke(app, ["propose", str(tj), str(out)])
    assert r.exit_code == 0, r.output
    rows = list(load_workbook(out).active.iter_rows(values_only=True))
    assert len(rows) == 2 and rows[1][0] == "relay" and not rows[1][1] and not rows[1][3]


def test_formula_injection_neutralized(tmp_path):
    s = TermStore(tmp_path / "f.jsonl")
    s.propose([{"term": "=1+1"}, {"term": "@SUM(A1)"}])
    x = tmp_path / "f.xlsx"
    export_proposed(s, x)
    ws = load_workbook(x).active
    for r in (2, 3):
        c = ws.cell(r, 1)
        assert c.data_type == "s" and c.value.startswith("'")
    edit(x, lambda ws: fill(ws, {2: ("a", "approved", "c"), 3: ("b", "approved", "c")}))
    assert import_terms_xlsx(s, x, "qa").ok
    assert s.get("=1+1").term_vi in ("a", "b") and s.get("@SUM(A1)") is not None  # prefix stripped


def test_formula_cell_rejected_and_stale(tmp_path):
    s = seeded(tmp_path)
    x = tmp_path / "p.xlsx"
    export_proposed(s, x)
    edit(x, lambda ws: ws.cell(2, 2, "=1+1"))
    res = import_terms_xlsx(s, x, "qa")
    assert not res.ok and res.issues[0].code == "formula"
    # stale: store moves on after export
    export_proposed(s, x)
    s.commit([TermRecord(term_en="hydraulic pump", term_vi="newer", status="approved", citation="c", approved_by="z")])
    edit(x, lambda ws: fill(ws, {2: ("old", "approved", "c")}))
    res = import_terms_xlsx(s, x, "qa")
    assert not res.ok and any(i.code == "stale" for i in res.issues)
    assert s.get("hydraulic pump").term_vi == "newer"


def test_must_use_terms_are_injected_first_uncapped_and_match_verb_forms(tmp_path):
    s = TermStore(tmp_path / "t.jsonl")
    mk = lambda en, vi, mu=False: TermRecord(term_en=en, term_vi=vi, status="approved", citation="c", approved_by="a",
                                            must_use_in_prompt=mu)
    s.commit([mk("pump", "bơm"), mk("filter", "bộ lọc"), mk("tighten", "siết", True), mk("battery", "ắc quy", True)])
    got = [r.term_en for r in select_glossary_subset("Tightened the battery; check pump and filter.", s, max_terms=1)]
    assert got[:2] == ["battery", "tighten"] and len(got) == 3      # both must-use kept, cap applies to the rest
    p = build_translation_prompt("Tighten ⟦P001⟧", select_glossary_subset("Tighten the battery pump", s), [], "v1")
    sysmsg = p["messages"][0]["content"]
    assert sysmsg.index("MUST use") < sysmsg.index("tighten => siết") < sysmsg.index("Glossary (EN => VI)")
    assert "pump => bơm" in sysmsg.split("Glossary (EN => VI)")[1]
    assert select_glossary_subset("overtighten the bolt", s) == []   # no partial-word match


def test_prompt_states_the_keep_english_rule():
    p = build_translation_prompt("Replace the harness.", [], [], "v9")
    sysmsg = p["messages"][0]["content"]
    assert "Keep English ONLY for names" in sysmsg
    for name in ("model and part names", "codes", "abbreviations (ECU, SOL, SW, GND, B+)", "wire colour codes",
                 "brand names"):
        assert name in sysmsg
    assert "harness, connector, switch, relay" in sysmsg
    assert "Never write an English term in parentheses" in sysmsg
    assert "never leave an English word as a substitute" in sysmsg
