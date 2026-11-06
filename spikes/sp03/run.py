"""SP-03: VI font embedding + fitting into text-block boxes (geometry only from the real PDF)."""
import json, random, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from _common import PDF, OUT
from src.render.fonts import find_vi_font
from src.render.overlay import box_extent, mask_rects, place_text, reading_rotation, register_font

WORDS = "bộ điều khiển động cơ được kiểm tra trước khi lắp đặt áp suất dầu thủy lực van đảo chiều cảm biến tốc độ quay nhiệt độ nước làm mát".split()
rnd = random.Random(7)
def vi_text(n_chars):
    s = ""
    while len(s) < n_chars:
        s += rnd.choice(WORDS) + " "
    return s.strip()[:max(n_chars, 3)]

font_path = find_vi_font()
src = pymupdf.open(PDF)
res = {"font": font_path.name if font_path else None}
def run(pages, tag, factor=1.3):
    doc = pymupdf.open()
    n = fit_ok = floor_hits = 0; sizes = []
    for pno in pages:
        sp = src[pno]
        page = doc.new_page(width=sp.mediabox.width, height=sp.mediabox.height)
        font = register_font(page, str(font_path))
        for bl in sp.get_text("dict")["blocks"]:
            if bl["type"] != 0 or not bl["lines"]:
                continue
            r = pymupdf.Rect(bl["bbox"]); rot = reading_rotation(tuple(bl["lines"][0]["dir"]))
            orig = " ".join(s["text"] for l in bl["lines"] for s in l["spans"]).strip()
            if not orig: continue
            size = max(s["size"] for l in bl["lines"] for s in l["spans"])
            fit = place_text(page, font, r, vi_text(int(len(orig) * factor)), rot, size, 5.0)
            n += 1; fit_ok += fit.fits; floor_hits += fit.size <= 5.0; sizes.append(fit.size)
    full = len(doc.tobytes(deflate=True)); 
    doc.subset_fonts(); sub = len(doc.tobytes(garbage=4, deflate=True))
    imgs = sum(len(p.get_images()) for p in doc)
    txt_ok = all(len(p.get_text().strip()) > 0 for p in doc)
    fpath = OUT / f"sp03_{tag}.pdf"; doc.save(fpath, garbage=4, deflate=True)
    res[tag] = dict(pages=len(pages), boxes=n, fit_rate=round(fit_ok / n, 3), at_floor=floor_hits,
                    mean_size=round(sum(sizes) / len(sizes), 2), bytes_before_subset=full,
                    bytes_after_subset=sub, images=imgs, text_extractable=txt_ok)
run([187], "p188")
run(list(range(186, 228)), "sec23_pages187_228")
res["font_file_bytes"] = font_path.stat().st_size
print(json.dumps(res, indent=1))
