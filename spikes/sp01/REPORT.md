# SP-01 report (metrics only)
Page 189 (rotated, text reads upward). 735 text spans (spec expected ~486 labels; spans are not merged here).
Placeholder = same text lower-cased except tokens containing digits. Box = span bbox, thickness tolerance +0.6 x font size, floor 5 pt.
- Length x1.0: fit in box 99.9% (8 at floor, 1 not fitting); fit and no overlap with other placed text 88.2%.
- Length x1.25 (VI expansion): fit in box 50.3% (single line) / 50.5% (wrap); no overlap 48.2%.
- Redaction removes underlying text; output still vector, 0 images. Redaction fills add vector drawings (1723 -> 2458).
- Output page is 25.6 MB vs 25.4 MB for the untouched single page (overhead about 0.17 MB; the page itself is heavy).
- Overlap failures come mostly from source bboxes that already touch or overlap, not from the text growing.
