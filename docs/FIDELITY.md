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
| Size | Original size first. Then, in this order: widen the box into free space at the original size (towards the free side, both sides for centred text); shrink in 0.25 pt steps down to 88 %; shrink and widen; tighten the line pitch to 0.92 of the original; condense the glyphs horizontally (down to 80 %, horizontal text only); extend downward into free space. Last resort: draw the full text smaller than 88 % (never below 60 %) so that nothing overlaps, and mark the block `needs_review` (dashed frame in the pilot PDF, marker in the viewer). Diagram labels that cannot fit even then go to the legend. |
| Position | Box top and first baseline of the original are kept; alignment (left, centre, right) and rotation (0, 90, 180, 270) are kept; widening never crosses another text block, line, image or the page margin. |
| Colour, weight | Dominant span colour, bold and italic of the original block. |
| Redaction | Only the characters of each original span are removed (span rectangles, shrunk 20 % across the lines, no fill). Lines, shading and images under or beside the text stay. |
| Z-order | Vietnamese text is drawn on top of the page content, like the original text normally is. |

## Results on the real manual

Section 43, 12 diverse pages (deterministic sample, all page types), simulated Vietnamese.

Section 43, 12 pages sampled deterministically (43-1, 2, 3, 4, 6, 7, 9, 12, 15, 17, 20, 22: text, table, mixed and
diagram pages), 411 text blocks, simulated Vietnamese. Run on Linux with Liberation Sans (metric-compatible with
Arial). Original fonts in the sample: ArialMT 382 blocks, Arial-BoldMT 17, Arial 12.

| Measure | Before (legacy renderer) | After (faithful renderer) |
|---|---|---|
| Size ratio, median | 0.85 | 1.00 |
| Size ratio, p10 / min | 0.742 / 0.308 | 0.884 / 0.734 |
| Blocks below 90 % of original size | 275 (66.9 %) | 51 (12.4 %) |
| Overflow (text does not fit) | 17 | 2 |
| Legend fallback (label replaced by [n]) | 73 | 0 |
| Blocks marked `needs_review` for layout | 0 | 21 (5.1 %) |
| Font family and weight kept | 95.9 % (bold lost on 17 of 17 bold blocks) | 100 % |
| Line count changed | 23.4 % | 9.0 % |
| Position drift of the text anchor | 0 pt | 0 pt |
| New overlaps between text boxes | 1 | 0 |
| White boxes painted over page content | 411 (one per block) | 0 |
| Line art or images lost | hidden under the white boxes | 0 |
| Original English words left under the new text | 0 | 0 |

How the 411 blocks were drawn after the change: 114 at original size and box, 175 at original size or close with the
box widened into free space, 50 shrunk (never below 88 %), 2 with tighter line pitch, 49 condensed horizontally, 21
`needs_review` (smaller than 88 % but not below 73 %, nothing overlaps; the 2 overflows are vertical table cells
about 6.5 pt tall with no free space). Pages with the most `needs_review`: 43-3 (7), 43-6 (6), 43-4 (5).

The legacy numbers are the old code on the same pages with the same font file (one regular face, size cap 11 pt,
8 pt for table cells, soft floor 7 pt, hard floor 5 pt).

## Known limits

- A block with mixed styles (for example one bold word in a sentence) is drawn in its dominant style.
- Text that the original placed under a filled shape is drawn on top of it.
- Pages with their own `/Rotate` are drawn correctly in the PDF; the legend of such pages may land outside the visible area.
- The numbers use simulated Vietnamese. Real translations are shorter or longer per sentence; re-run the tool on the pilot output pages when real text exists.
