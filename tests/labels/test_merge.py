import pymupdf

from src.labels.merge import count_suspicious, extract_spans, is_suspicious, merge_adjacent


def _page(items, rotate_items=()):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=400)
    for pt, txt, rot in items:
        page.insert_text(pt, txt, fontsize=8, rotate=rot)
    return page


def test_suspicious_rules():
    assert is_suspicious("x") and not is_suspicious("ON")
    assert is_suspicious("x") and is_suspicious("ing") and is_suspicious("Pr") and is_suspicious("ab-")
    assert not is_suspicious("Relay") and not is_suspicious("12") and not is_suspicious("ON")


def test_merge_split_word_same_line():
    page = _page([((50, 50), "Pres", 0), ((50 + 17.2, 50), "sure", 0), ((50, 120), "Valve", 0)])
    spans = extract_spans(page)
    merged = merge_adjacent(spans)
    texts = sorted(m.text for m in merged)
    assert "Valve" in texts and any(t.replace(" ", "") == "Pressure" for t in texts)
    assert count_suspicious(merged) <= count_suspicious(spans)


def test_far_labels_not_merged():
    page = _page([((20, 50), "Relay", 0), ((300, 50), "Fuse", 0)])
    assert len(merge_adjacent(extract_spans(page))) == 2


def test_merge_rotated_text():
    page = _page([((50, 300), "Pres", 90), ((50, 300 - 17.2), "sure", 90)])
    merged = merge_adjacent(extract_spans(page))
    assert len(merged) == 1 and merged[0].text.replace(" ", "") == "Pressure"


def test_continuation_line_lowercase():
    page = _page([((50, 50), "Engine speed sen-", 0), ((50, 60), "sor", 0)])
    m = merge_adjacent(extract_spans(page))
    assert len(m) == 1 and "sensor" in m[0].text.replace(" ", "")
