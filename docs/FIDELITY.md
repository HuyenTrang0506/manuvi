# Page fidelity: how close is the Vietnamese page to the original

Goal: the Vietnamese PDF looks like the original manual page. Same positions, same relative text sizes, same line
structure, tables and diagrams intact, only the words change.

## Measure it

```
manuvi fidelity --pdf manual.pdf --section 43 --pages 12 --engine both --out workspace/fidelity
```

Local, $0, no model. Vietnamese is simulated by a deterministic expansion of the English words (every plain word
gets diacritics and grows 15 to 35 percent, codes, numbers and short abbreviations stay), then the real renderer
draws it. `--engine legacy` is the old renderer (one regular font, size cap 11 pt, soft floor 7 pt), `faithful` is
the current one, `both` prints the two reports. The report (`fidelity.md`, `fidelity.json`) holds numbers and font
names only, never page text. `--keep-pdf` also writes the simulated pages (they contain page content, keep them
on the machine).

Per block: font family match (original to chosen), size ratio (Vietnamese size / original size), position drift of
the text anchor (pt), line count change, overflow, legend fallback, `needs_review`, share below 90 % of the
original size. Per page also: new overlaps between text boxes.

## How the renderer keeps the look

| Aspect | Rule |
|---|---|
| Font | Original family and weight drive the choice: Arial / Helvetica / MS Gothic (Vietnamese text) / Symbol to Arial on a Mac, Liberation Sans (metric-compatible with Arial and Arimo) on Linux; bold, italic and bold italic variants; serif and mono originals to Liberation Serif / Mono. A CJK face is used only when the target text contains Japanese. Override: `MANUVI_VI_FONT` (one file for everything), `MANUVI_FONT_DIR`. |
| Size | Original size first. If it does not fit: shrink in 0.25 pt steps down to 88 % of the original. Then widen the box into free space on the page (towards the free side; both sides for centred text). Then tighten the line pitch to 0.92 of the original. Then extend downward into free space. Last resort: draw the full text at the floor and mark the block `needs_review` (dashed frame in the pilot PDF, marker in the viewer). Diagram labels that still do not fit go to the legend, as before. |
| Position | Box top and first baseline of the original are kept; alignment (left, centre, right) and rotation (0, 90, 180, 270) are kept; widening never crosses another text block, line, image or the page margin. |
| Colour, weight | Dominant span colour, bold and italic of the original block. |
| Redaction | Only the characters of each original span are removed (span rectangles, shrunk 20 % vertically, no fill). Lines, shading and images under or beside the text stay. |
| Z-order | Vietnamese text is drawn on top of the page content, like the original text normally is. |

## Results on the real manual

Section 43, 12 diverse pages (deterministic sample, all page types), simulated Vietnamese.

NUMBERS_PLACEHOLDER

## Known limits

- A block with mixed styles (for example one bold word in a sentence) is drawn in its dominant style.
- Text that the original placed under a filled shape is drawn on top of it.
- Pages with their own `/Rotate` are drawn correctly in the PDF; the legend of such pages may land outside the visible area.
- The numbers use simulated Vietnamese. Real translations are shorter or longer per sentence; re-run the tool on the pilot output pages when real text exists.
