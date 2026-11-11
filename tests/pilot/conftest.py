import pymupdf
import pytest

from src.llm_gateway.errors import RequestRejected
from src.pipeline.demo import DemoTranslator

PROSE = ("Check the hydraulic pump pressure before starting the engine. Inspect the hoses for cracks or leaks "
         "and replace the fuel filter every 500 hours. Use part YN20-01234-A when replacing.")


def _label(page, text):
    page.insert_text((280, page.rect.height - 30), text, fontsize=9)


def _text_page(doc, label, extra=()):
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 72, 520, 150), PROSE, fontsize=11)
    y = 170
    for t in extra:
        p.insert_textbox(pymupdf.Rect(72, y, 520, y + 40), t, fontsize=11)
        y += 60
    _label(p, label)


def _table_page(doc, label):
    p = doc.new_page()
    xs, ys = [72, 220, 370, 520], [100, 140, 180, 220]
    for x in xs:
        p.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        p.draw_line((xs[0], y), (xs[-1], y))
    for r, row in enumerate([("Relay name", "Rating", "Circuit"), ("Fuse box", "24 V", "Starter circuit"),
                             ("Main relay", "5 A", "Battery circuit")]):
        for c, t in enumerate(row):
            p.insert_text((xs[c] + 4, ys[r] + 25), t, fontsize=9)
    _label(p, label)


def _diagram_page(doc, label, rotation=0):
    p = doc.new_page(width=842, height=595)
    for i in range(40):
        p.draw_line((50 + i * 15, 100), (80 + i * 14, 400))
    p.insert_text((100, 450), "Fuse box", fontsize=8)
    p.insert_text((300, 450), "Battery relay", fontsize=8)
    p.insert_text((600, 300), "Starter motor", fontsize=8, rotate=90)
    p.insert_text((650, 460), "YT1234 YT2210", fontsize=8)
    _label(p, label)
    if rotation:
        p.set_rotation(rotation)


def _scanned_page(doc, label):
    p = doc.new_page()
    pm = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 200), False)
    pm.set_rect(pm.irect, (230, 230, 230))
    p.insert_image(pymupdf.Rect(50, 50, 500, 700), pixmap=pm)
    _label(p, label)


@pytest.fixture
def manual(tmp_path):
    """Imitates the real manual: other sections, non-consecutive 43-n labels, table, rotated diagram, scan."""
    doc = pymupdf.open()
    _text_page(doc, "23-1")
    _text_page(doc, "43-1")
    _text_page(doc, "43-2", extra=["WARNING: Disconnect the battery before service."])
    _table_page(doc, "43-5")
    _table_page(doc, "43-6")
    _diagram_page(doc, "43-12")
    _diagram_page(doc, "43-13", rotation=90)
    _scanned_page(doc, "43-20")
    _text_page(doc, "43-21")
    _text_page(doc, "44-1")
    path = tmp_path / "manual.pdf"
    doc.save(str(path))
    doc.close()
    return path


class Counting(DemoTranslator):
    def __init__(self, **kw):
        super().__init__(**kw)
        self.texts: list[str] = []

    def complete(self, req, model_id):
        self.texts.append(next(m for m in req.messages if m["role"] == "user")["content"])
        if any("Starter motor" in t for t in self.texts[-1:]):
            raise RequestRejected("400")
        return super().complete(req, model_id)
