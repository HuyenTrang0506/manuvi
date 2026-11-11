import pymupdf

from src.intake.vision_detect import analyze_page
from src.intake.vision_read import VisionConfig

from .vision_helpers import PROSE, image_only_page, mixed_page, raster_label

CFG = VisionConfig()


def test_plain_text_page_needs_no_vision():
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(72, 72, 520, 200), PROSE, fontsize=11)
    d, blocks = analyze_page(doc[0], CFG)
    assert d.mode == "none" and blocks and d.signals["chars"] > 50


def test_image_only_page_and_empty_page():
    doc = pymupdf.open()
    image_only_page(doc)
    doc.new_page()
    assert analyze_page(doc[0], CFG)[0].mode == "page"
    d = analyze_page(doc[1], CFG)[0]
    assert d.mode == "page" and d.reasons == ["no_text_layer"]


def test_scan_with_its_own_text_layer_is_not_read_again():
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=raster_label("x", 595, 842))
    p.insert_textbox(pymupdf.Rect(72, 72, 520, 300), PROSE * 3, fontsize=11, render_mode=3)  # invisible OCR layer
    assert analyze_page(p, CFG)[0].mode == "none"


def test_image_page_with_only_a_few_words_goes_to_vision():
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_image(p.rect, stream=raster_label("x", 595, 842))
    p.insert_text((72, 800), "Page 12 of the electrical section", fontsize=9)
    d = analyze_page(p, CFG)[0]
    assert d.mode == "page" and d.reasons == ["image_page_sparse_text"]


def test_raster_label_inside_text_page_becomes_a_region():
    doc = pymupdf.open()
    rect = mixed_page(doc)
    d, blocks = analyze_page(doc[0], CFG)
    assert d.mode == "regions" and d.reasons == ["image_regions_without_text"]
    assert len(d.regions) == 1 and d.regions[0].contains(rect)


def test_label_image_with_text_layer_inside_and_tiny_logos_are_ignored():
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 72, 520, 160), PROSE, fontsize=11)
    r = pymupdf.Rect(300, 400, 530, 454)
    p.insert_image(r, stream=raster_label("FUSE"))
    p.insert_text((310, 430), "FUSE BOX", fontsize=9)           # text layer on top of the picture
    p.insert_image(pymupdf.Rect(10, 10, 20, 20), stream=raster_label("logo", 40, 40))
    assert analyze_page(p, CFG)[0].mode == "none"


def test_near_images_are_merged_and_regions_capped():
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 72, 520, 160), PROSE, fontsize=11)
    for i in range(3):   # three touching strips = one diagram
        p.insert_image(pymupdf.Rect(60, 300 + i * 56, 290, 354 + i * 56), stream=raster_label(f"L{i}"))
    for i in range(6):
        p.insert_image(pymupdf.Rect(330, 200 + i * 80, 560, 254 + i * 80), stream=raster_label(f"R{i}"))
    d = analyze_page(p, CFG)[0]
    assert len(d.regions) == 7
    d2 = analyze_page(p, VisionConfig(max_regions_per_page=3))[0]
    assert len(d2.regions) == 3 and d2.truncated


class _Page:
    """Real page, but text extraction returns a layer whose glyphs have no Unicode mapping."""

    def __init__(self, page, chars):
        self._p, self._chars = page, chars

    def __getattr__(self, name):
        return getattr(self._p, name)

    def get_text(self, mode="text", **kw):
        if mode == "rawdict":
            ch = [{"c": c, "bbox": (0, 0, 1, 1)} for c in self._chars]
            return {"blocks": [{"type": 0, "bbox": (72, 72, 300, 90),
                                "lines": [{"spans": [{"chars": ch}]}]}]}
        return []


def test_glyphless_text_layer_goes_to_vision():
    doc = pymupdf.open()
    p = doc.new_page()
    d, blocks = analyze_page(_Page(p, "\ufffd" * 60 + "ok relay box wired"), CFG)
    assert d.mode == "page" and d.reasons == ["glyphless_text_layer"] and blocks == []
    d, blocks = analyze_page(_Page(p, "\ue001\x00" * 30 + "Check the pump pressure and the relay box"), CFG)
    assert d.mode == "page"
    ok, blocks = analyze_page(_Page(p, "Check the pump pressure and the relay box \ufffd"), CFG)
    assert ok.mode != "page" or "glyphless_text_layer" not in ok.reasons
