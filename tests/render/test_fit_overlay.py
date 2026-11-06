import pymupdf
import pytest

from src.render.fit import fit_text
from src.render.fonts import covers_vietnamese, find_vi_font
from src.render.overlay import mask_rects, place_text, placed_rect, box_extent, register_font, reading_rotation

FONT = find_vi_font()
pytestmark = pytest.mark.skipif(FONT is None, reason="no Vietnamese font available")
VI = "Bộ điều khiển động cơ được kiểm tra trước khi lắp"


def test_font_covers_vi():
    assert covers_vietnamese(FONT)


def test_fit_shrinks_and_respects_floor():
    f = pymupdf.Font(fontfile=str(FONT))
    big = fit_text(VI, f, 300, 20, 10)
    assert big.fits and big.size == 10
    small = fit_text(VI, f, 120, 12, 10)
    assert small.fits and small.size < 10 and small.size >= 5
    none = fit_text(VI, f, 20, 6, 10)
    assert not none.fits and none.size == 5


def test_no_wrap_single_line():
    f = pymupdf.Font(fontfile=str(FONT))
    r = fit_text(VI, f, 100, 40, 10, allow_wrap=False)
    assert len(r.lines) == 1


@pytest.mark.parametrize("rot", [0, 90, 180, 270])
def test_render_oriented_text_is_extractable_and_inside_box(rot):
    doc = pymupdf.open()
    page = doc.new_page(width=300, height=300)
    font = register_font(page, str(FONT))
    rect = pymupdf.Rect(100, 100, 200, 130) if rot in (0, 180) else pymupdf.Rect(100, 100, 130, 200)
    fit = place_text(page, font, rect, "Điều khiển", rot, 9)
    assert fit.fits
    assert "Điều khiển" in page.get_text()
    assert page.rect.contains(placed_rect(rect, rot, fit))
    assert (rect + (-0.01, -0.01, 0.01, 0.01)).contains(placed_rect(rect, rot, fit))
    assert not page.get_images()


def test_mask_removes_original_text():
    doc = pymupdf.open()
    page = doc.new_page(width=200, height=200)
    page.insert_text((50, 50), "SECRET", fontsize=10)
    mask_rects(page, [pymupdf.Rect(40, 38, 120, 56)])
    assert "SECRET" not in page.get_text()


def test_subset_reduces_size():
    doc = pymupdf.open()
    page = doc.new_page()
    font = register_font(page, str(FONT))
    place_text(page, font, pymupdf.Rect(50, 50, 300, 80), VI, 0, 10)
    before = len(doc.tobytes())
    doc.subset_fonts()
    after = len(doc.tobytes(garbage=4, deflate=True))
    assert after < before
    assert "Bộ điều khiển" in doc[0].get_text()


def test_reading_rotation():
    assert reading_rotation((1, 0)) == 0 and reading_rotation((0, -1)) == 90
    assert reading_rotation((-1, 0)) == 180 and reading_rotation((0, 1)) == 270
