"""SP-01: in-place overlay on page 189: mask labels, write placeholder text at >=5pt."""
import json, re, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import pymupdf
from _common import PDF, OUT
from src.labels.merge import extract_spans, _axis
from src.render.fonts import find_vi_font
from src.render.overlay import mask_rects, placed_rect, draw_fit, register_font, reading_rotation, box_extent
from src.render.fit import fit_text

PNO = 188
doc = pymupdf.open(PDF)
doc.select([PNO]); page = doc[0]
font_path = find_vi_font()
labels = extract_spans(page)

def placeholder(t):  # same text, uppercase-stripped; tokens with digits (codes) untouched
    return " ".join(w if re.search(r"\d", w) else w.lower() for w in t.split())

font = register_font(page, str(font_path))
items = []
for l in labels:
    r = pymupdf.Rect(l.bbox); rot = reading_rotation(l.direction)
    items.append((l, r, rot, placeholder(l.text)))
mask_rects(page, [it[1] for it in items])
font = register_font(page, str(font_path))

def run(factor, wrap):
    placed = []; fits = 0; floor = 0
    for l, r, rot, t in items:
        L, T = box_extent(r, rot)
        f = fit_text(t, font, L, T + 0.6 * l.size, l.size, 5.0, allow_wrap=wrap, width_factor=factor)
        pr = placed_rect(r, rot, f)
        placed.append((f, pr, r, rot)); fits += f.fits; floor += f.fits and f.size <= 5.0
    ok = 0
    for i, (f, pr, r, rot) in enumerate(placed):
        if not f.fits: continue
        # overlap with any other placed text of another label (ignore sliver overlaps < 0.3pt)
        shrink = pr + (0.3, 0.3, -0.3, -0.3)
        if not any(j != i and placed[j][0].fits and shrink.intersects(placed[j][1]) for j in range(len(placed))):
            ok += 1
    n = len(items)
    return dict(labels=n, fit_in_box=round(fits / n, 3), fit_no_overlap=round(ok / n, 3),
                at_floor=floor, not_fitting=n - fits), placed

res = {"page": PNO + 1, "labels_spans": len(labels), "font": font_path.name}
for factor in (1.0, 1.25):
    for wrap in (False, True):
        r, placed = run(factor, wrap)
        res[f"x{factor}_{'wrap' if wrap else 'single'}"] = r
        if factor == 1.0 and not wrap:
            for (f, pr, rect, rot), (l, *_rest) in zip(placed, items):
                pass
# render the baseline variant
r, placed = run(1.0, False)
for (f, pr, rect, rot) in placed:
    draw_fit(page, rect, rot, f)
doc.subset_fonts()
doc.save(OUT / "sp01_p189.pdf", garbage=4, deflate=True)
o = pymupdf.open(OUT / "sp01_p189.pdf")
res["output_bytes"] = (OUT / "sp01_p189.pdf").stat().st_size
res["orig_images"] = len(pymupdf.open(PDF)[PNO].get_images()); res["out_images"] = len(o[0].get_images())
res["out_drawings_vs_orig"] = [len(pymupdf.open(PDF)[PNO].get_drawings()), len(o[0].get_drawings())]
res["vector_text_extractable"] = len(o[0].get_text().strip()) > 0
print(json.dumps(res, indent=1))
