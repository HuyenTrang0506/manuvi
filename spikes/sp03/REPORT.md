# SP-03 report (metrics only)
Font: Noto Sans Regular (OFL), fetched from notofonts GitHub into gitignored workspace/fonts; Be Vietnam Pro not installable via pip; fallback DejaVu Sans Condensed (covers all VI diacritics tested).
Synthetic VI text (random words, 1.3x original char count) in text-block bboxes, floor 5 pt.
- Page 188: 5 boxes, fit rate 80% (1 at 5 pt floor and not fitting), mean size 8.6 pt.
- Pages 187-228: 1787 boxes, fit rate 80.8%, 344 boxes hit the floor, mean size 6.9 pt.
- Font subset: page 188 335,214 B -> 29,431 B after subset_fonts (font file 621,572 B). 42-page doc: 801,394 B -> 492,659 B.
- Output is vector: 0 images added, text extractable (Vietnamese strings found in get_text).
