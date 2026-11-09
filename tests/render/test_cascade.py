import pymupdf
import pytest

from src.render.fit import fit_cascade, wrap
from src.render.fonts import find_vi_font

FONT = find_vi_font()
pytestmark = pytest.mark.skipif(FONT is None, reason="no Vietnamese font available")
VI = "Bộ điều khiển động cơ được kiểm tra trước khi lắp vào máy"


def font():
    return pymupdf.Font(fontfile=str(FONT))


def test_stage_order():
    f = font()
    assert fit_cascade(VI, f, 400, 20, 10).stage == "ok"
    # soft floor 7 needs 2+ lines; normal lh does not fit but tight lh does
    w = 150
    base = fit_cascade(VI, f, w, 1000, 7, soft_floor=7)
    n = len(base.fit.lines)
    assert n >= 2
    h_norm = 7 * (1.15 + (n - 1) * 1.15)
    h_tight = 7 * (1.15 + (n - 1) * 1.0)
    r = fit_cascade(VI, f, w, (h_norm + h_tight) / 2, 7, soft_floor=7)
    assert r.stage == "tight_lh" and r.fit.lh == 1.0
    r = fit_cascade(VI, f, w, h_tight - 3, 7, soft_floor=7, free_below=40)
    assert r.stage == "extended" and r.extension > 0
    r = fit_cascade(VI, f, w, h_tight - 3, 7, soft_floor=7, free_below=0)
    assert r.stage in ("hard_floor", "overflow") and r.extension == 0 or r.stage == "hard_floor"


def test_overflow_flagged_not_truncated():
    f = font()
    r = fit_cascade(VI, f, 30, 8, 10)
    assert r.overflow and r.stage == "overflow" and r.fit.size == 5
    assert " ".join(r.fit.lines).replace("\xa0", " ").split() == VI.split()


def test_wrap_splits_only_ascii_whitespace():
    f = font()
    lines = wrap("xem Fig. 23-11 ngay " * 3, f, 8, 60)
    assert all("Fig. 23-11" in l or "Fig." not in l for l in lines)
    assert not any(l.endswith("Fig.") for l in lines)
