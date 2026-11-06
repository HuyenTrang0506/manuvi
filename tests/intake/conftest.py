import pymupdf
import pytest

CODES = ["YT12-AB3C", "YN20-01234-A", "YT120X"]


def label_footer(page, label):
    if label:
        page.insert_text((250, 810), label, fontsize=9)


def make_pdf(path, labels):
    """One plain text page per entry; the entry is printed as the footer page label (None = no label)."""
    doc = pymupdf.open()
    for i, lab in enumerate(labels):
        p = doc.new_page()
        p.insert_text((72, 100), f"Body text of page {i + 1} about the relay box.", fontsize=11)
        label_footer(p, lab)
    doc.save(path)
    return path


@pytest.fixture
def pdf(tmp_path):
    """5 pages labelled 10-1..10-3, 11-1, 11-2: text, table, diagram, scanned (image only), text."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 100), "Check the relay box. Connect harness YT12-AB3C to the relay box.\n", fontsize=11)
    p.insert_text((72, 120), "Part YN20-01234-A is the main relay box assembly.", fontsize=11)
    label_footer(p, "10-1")
    p = doc.new_page()
    p.insert_text((72, 60), "Table of fuses", fontsize=11)
    for r in range(5):
        for c in range(3):
            x, y = 72 + c * 100, 100 + r * 25
            p.draw_rect(pymupdf.Rect(x, y, x + 100, y + 25))
            p.insert_text((x + 4, y + 16), f"cell {r}{c}", fontsize=9)
    label_footer(p, "10-2")
    p = doc.new_page()
    p.insert_text((72, 60), "Wiring diagram overview", fontsize=11)
    for i in range(30):
        p.draw_line((72, 100 + i * 8), (400, 120 + i * 9))
    label_footer(p, "10-3")
    p = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    p.insert_image(pymupdf.Rect(50, 50, 300, 300), pixmap=pix)
    label_footer(p, "11-1")  # short label only: still below the text-layer threshold
    p = doc.new_page()
    p.insert_text((72, 100), "Check the relay box again. Harness YT12-AB3C and YT120X are listed.", fontsize=11)
    label_footer(p, "11-2")
    path = tmp_path / "synth.pdf"
    doc.save(path)
    return path
