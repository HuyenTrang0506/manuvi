"""Unit test — dịch semantic nhãn SE/SV/R và format cột."""

from __future__ import annotations

import unittest
from pathlib import Path

from src.assemble.label_table import format_vi_meaning, split_label
from src.glossary.manager import load_glossary
from src.qa.label_validator import is_mixed_translation, is_untranslated
from src.translate.equipment_list import enrich_pure_code, translate_labeled_component, vi_meaning_only
from src.translate.label_translator import translate_label_line
from src.models import LabelNote


GLOSSARY = load_glossary(Path("workspace/sk70sr-1es"))


class LabelSemanticTests(unittest.TestCase):
    """Nhãn sơ đồ phải dịch semantic — không copy EN."""

    CASES = [
        ("SE-1:BUCKET DIGGING", "SE-1: Cảm biến đào gầu"),
        ("SE-8:ARM OUT", "SE-8: Cảm biến tay gầu ra"),
        ("SE-16:ACCEL POTENTIO", "SE-16: Biến trở gia tốc"),
        ("SE-3:BOOM RAISING", "SE-3: Cảm biến nâng cần"),
        ("SV-8:PQ SHIFT SOL.", "SV-8: Van điện từ chuyển số PQ"),
        ("R-4:SAFETY RELAY", "R-4: Rơ-le an toàn"),
        ("R-30:TIMER UNIT.", "R-30: Bộ đếm thời gian"),
        ("SE-14:COOLANT THERMO SENSOR", "SE-14: Cảm biến nhiệt nước làm mát"),
        ("SE-10:TRAVEL LEFT", "SE-10: Cảm biến di chuyển trái"),
    ]

    def test_translate_labeled_lines(self):
        for en, expected in self.CASES:
            with self.subTest(en=en):
                got = translate_label_line(en, GLOSSARY)
                self.assertEqual(got, expected, f"{en} → {got}")

    def test_pure_codes_enriched(self):
        for code, fragment in [
            ("SE-4", "hạ cần"),
            ("SE-7", "tay gầu vào"),
            ("C-1", "Mechatro"),
        ]:
            with self.subTest(code=code):
                got = enrich_pure_code(code)
                self.assertIsNotNone(got)
                self.assertIn(fragment.lower(), got.lower())

    def test_no_english_residual_in_vi(self):
        bad_words = {"BUCKET", "DIGGING", "RAISING", "POTENTIO", "ARM OUT", "PQ SHIFT"}
        for en, _ in self.CASES:
            vi = translate_label_line(en, GLOSSARY)
            upper = vi.upper()
            for w in bad_words:
                self.assertNotIn(w, upper, f"{en} còn sót EN: {vi}")

    def test_not_mixed_translation(self):
        for en, _ in self.CASES:
            vi = translate_label_line(en, GLOSSARY)
            self.assertFalse(is_mixed_translation(vi), vi)

    def test_vi_meaning_column_strips_code(self):
        lb = LabelNote(text_en="SE-14:COOLANT THERMO SENSOR", text_vi="SE-14: Cảm biến nhiệt nước làm mát")
        self.assertEqual(format_vi_meaning(lb), "Cảm biến nhiệt nước làm mát")
        self.assertNotIn("SE-14", format_vi_meaning(lb))

    def test_split_label_three_columns(self):
        code, desc, _ = split_label("SE-1:BUCKET DIGGING")
        self.assertEqual(code, "SE-1")
        self.assertEqual(desc, "BUCKET DIGGING")

    def test_translate_labeled_component(self):
        self.assertEqual(translate_labeled_component("SE-1", "BUCKET DIGGING"), "Cảm biến đào gầu")
        self.assertEqual(translate_labeled_component("SE-4", ""), "Cảm biến hạ cần")

    def test_vi_meaning_only_em_dash(self):
        self.assertEqual(vi_meaning_only("C-1 — Bộ điều khiển Mechatro"), "Bộ điều khiển Mechatro")

    def test_pure_code_not_flagged_untranslated(self):
        vi = translate_label_line("SE-1:BUCKET DIGGING", GLOSSARY)
        self.assertFalse(is_untranslated("SE-1:BUCKET DIGGING", vi))

    def test_gauge_cluster_indicators(self):
        cases = [
            ("ILLUMINATION", "chiếu sáng"),
            ("CHARGE", "nạp"),
            ("CPU ALLERGY", "CPU"),
            ("OVERLOAD", "quá tải"),
            ("FUEL METER", "nhiên liệu"),
            ("TRAVEL TWO-SPEED", "di chuyển"),
        ]
        for en, frag in cases:
            with self.subTest(en=en):
                vi = translate_label_line(en, GLOSSARY)
                self.assertNotEqual(vi.upper(), en.upper())
                self.assertIn(frag.lower(), vi.lower())

    def test_wire_labels_semantic(self):
        from src.translate.wire_labels import format_wire_vi, is_wire_label

        self.assertTrue(is_wire_label("1.25 WV"))
        self.assertTrue(is_wire_label("0.5 BrY"))
        self.assertIn("trắng", format_wire_vi("1.25 WV") or "")
        vi = translate_label_line("1.25 WV", GLOSSARY)
        self.assertIn("mm²", vi)
        self.assertIn("trắng", vi)

    def test_structured_legend_collapses_sw_and_drops_pins(self):
        from src.assemble.label_table import build_structured_legend, code_sort_key
        from src.models import LabelNote

        labels = [
            LabelNote(text_en="SW-1", text_vi="SW-1 (Công tắc)"),
            LabelNote(text_en="SW-4", text_vi="SW-4 (Công tắc)"),
            LabelNote(text_en="SW-6", text_vi="SW-6 (Công tắc)"),
            LabelNote(text_en="SW-7", text_vi="SW-7 (Công tắc)"),
            LabelNote(text_en="SW-8", text_vi="SW-8 (Công tắc)"),
            LabelNote(text_en="SW-10:HORN SW.", text_vi="SW-10: công tắc còi"),
            LabelNote(text_en="C-2:GAUGE CLUSTER", text_vi="C-2: Cụm đồng hồ"),
            LabelNote(text_en="179", text_vi="179"),
            LabelNote(text_en="802", text_vi="802"),
            LabelNote(text_en="99", text_vi="99"),
            LabelNote(text_en="ILLUMINATION", text_vi="đèn chiếu sáng bảng đồng hồ"),
        ]
        sections = build_structured_legend(labels)
        self.assertEqual(len(sections), 1)
        rows = sections[0].rows
        codes = [r.code for r in rows]
        # Sort: C trước SW; SW-10 sau SW-8 (số nguyên)
        self.assertIn("C-2", codes)
        sw_idx = [i for i, c in enumerate(codes) if c.startswith("SW")]
        self.assertTrue(sw_idx)
        # Không có số chân
        all_text = " ".join(f"{r.code} {r.meaning_vi}" for r in rows)
        self.assertNotIn("179", all_text)
        self.assertNotIn("802", all_text)
        # Keys tăng dần
        keys = [code_sort_key(r.code if r.code != "—" else r.name_en or "") for r in rows]
        self.assertEqual(keys, sorted(keys))
        # Có gom range
        self.assertTrue(any("→" in c for c in codes))


class ExtractCoverageTests(unittest.TestCase):
    """Extract từ PDF gốc phải lấy được đèn báo trên C-2."""

    def test_page_23_3_has_gauge_lamps(self):
        import fitz
        from src.ingest.diagram_labels import extract_labels_from_page

        pdf = Path("data/382014584-sk70sr-1es-shop-manual.pdf")
        if not pdf.exists():
            self.skipTest("PDF nguồn không có")
        doc = fitz.open(str(pdf))
        labels = extract_labels_from_page(doc[188], GLOSSARY)  # 23-3
        doc.close()
        ens = {lb.text_en.upper() for lb in labels}
        joined = " | ".join(ens)
        for must in ["ILLUMINATION", "CHARGE", "CPU ALLERGY", "OVERLOAD", "FUEL METER", "AIR CLEANER"]:
            self.assertTrue(
                any(must in e for e in ens),
                f"Thiếu {must} trong extract ({len(labels)} nhãn). Có: {joined[:200]}",
            )
        self.assertGreaterEqual(len(labels), 80, f"Extract quá ít: {len(labels)}")


class BomListPhraseTests(unittest.TestCase):
    """BOM / mounting / washer — không được sót EN."""

    CASES = [
        ("MOUNTING BOOM LIGHT", "đèn cần"),
        ("MOUNTING CAB", "buồng lái"),
        ("MOUNTING LIGHT", "đèn"),
        ("WASHER TANK", "bình nước rửa"),
        ("WINDOW WASHER TANK & PUMP", "bình nước rửa"),
        ("CIGALETTE LIGHTER", "bật lửa"),
        ("KYE SWITCH", "chìa"),
        ("ACCEL POTENTIO DIAL", "gia tốc"),
        ("Name", "Tên"),
        ("Part No.", "Mã phụ tùng"),
        ("Q'ty", "SL"),
        ("TARMINAL", "cực"),
    ]

    def test_list_lines(self):
        from src.translate.list_translator import translate_line_list

        for en, frag in self.CASES:
            with self.subTest(en=en):
                vi = translate_line_list(en, GLOSSARY)
                self.assertNotEqual(vi.upper(), en.upper(), f"chưa dịch: {en}")
                self.assertIn(frag.lower(), vi.lower(), f"{en} → {vi}")
                if "WASHER" in en.upper():
                    self.assertNotIn("TANK", vi.upper())

    def test_heading_phrases(self):
        from src.assemble.toc import translate_heading_text

        self.assertIn("ĐÈN", translate_heading_text("MOUNTING LIGHT").upper())
        self.assertIn("NƯỚC RỬA", translate_heading_text("WINDOW WASHER TANK & PUMP").upper())


class TocCollectTests(unittest.TestCase):
    def test_collect_has_appendix_and_bilingual(self):
        from src.assemble.toc import collect_toc_entries
        from src.ingest.parser import load_document

        ws = Path("workspace/sk70sr-1es")
        if not (ws / "document_model.json").exists():
            self.skipTest("workspace thiếu")
        doc = load_document(ws)
        entries = collect_toc_entries(doc)
        self.assertGreaterEqual(len(entries), 5)
        self.assertTrue(any("Phụ lục" in e.vi or "Appendix" in e.en for e in entries))
        # Có ít nhất một mục song ngữ khác nhau
        bilingual = [e for e in entries if e.vi and e.vi.upper() != e.en.upper()]
        self.assertGreaterEqual(len(bilingual), 2)


if __name__ == "__main__":
    unittest.main()
