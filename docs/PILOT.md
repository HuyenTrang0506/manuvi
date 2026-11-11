# Section 43 pilot

Turns 8 to 10 real pages of a section into a Vietnamese PDF that keeps the original page layout. Fully local (Ollama), $0. The manual never leaves the machine.

## Run

On the Mac, one command (safe to run again; it resumes):

```
scripts/mac/run_pilot.sh                      # default manual path, 10 pages
scripts/mac/run_pilot.sh /path/to/manual.pdf
```

It starts Ollama if needed, pulls the model, updates the virtual environment, runs the pilot and opens the output folder. Overrides: `MANUVI_PILOT_MODEL=ollama:<tag>`, `MANUVI_PILOT_VISION_MODEL=ollama:<tag>`, `MANUVI_PILOT_PAGES=8`, `MANUVI_PILOT_OUT=<folder>`.

The vision model (see below) is not downloaded by this script. Install and try it once with `scripts/mac/vision_smoke.sh`.

Without the script: `manuvi pilot --pdf manual.pdf --section 43 --pages 10 --model ollama:<tag> [--vision-model ollama:<tag>] --out workspace/pilot`.

## What it does

1. Finds the pages of the section by their printed labels (`43-12`), so non-consecutive pages work.
2. Classifies them (prose, table, diagram) and picks the sample deterministically: all page types are covered, spread across the section. The same manual and `--pages` give the same pages.
3. Translates each page on its own with the guard (codes, numbers and safety words are protected). Each page is checkpointed: run again and finished pages cost zero model calls.
4. Renders the Vietnamese text in place on the original page, in the original font family, size, colour, alignment and rotation (rules and measurements: `docs/FIDELITY.md`). Page labels, part and connector codes and short abbreviations stay as printed.

5. Text that has no text layer is read by a local vision-language model, not by OCR (founder decision). Per page the pilot measures text-layer coverage: a page with no or unusable text (image-only scan, unmapped glyphs, a picture with a few words) is read as a whole; on a page that has text, only picture areas without any text-layer text are read, as cropped regions. The model returns text blocks with boxes. Those blocks are translated and drawn like normal blocks. A block with low confidence, a box outside the page or overlapping another, a foreign script (CJK unless the page has Japanese) or a code that looks misread is marked `[!]` in the PDF and listed in the report; a block with a foreign script or an unplaceable box is only listed, never translated or drawn. Details: `docs/VISION.md`.

A failure on one page is logged by error class only (never page text) and the run continues. A page that needs the vision model and cannot get it (model not installed, unreadable answer) carries a banner and is reported as failed. The last line says how many pages succeeded, how many were read by vision and how many vision blocks were flagged.

## Outputs (in the output folder)

| File | Use |
|---|---|
| `section43-vi.pdf` | Main deliverable: Vietnamese only, original layout. Same page count as the sample. |
| `section43-interleaved-review.pdf` | Original page, then its Vietnamese page. Review aid only. |
| `pilot-report.md` | Per page: type, segments, repaired, routed to review, leftover English words, vision (`page`/`regions`, blocks read, blocks flagged, skipped or failed reads), layout flags (`overflow`, `font_below_min` = a block drawn below 90 % of its original size, `layout_needs_review`, `legend_fallback`, `vision_incomplete`), seconds. Lists every routed segment and every flagged vision block (reasons only, no text). |
| `review-form.xlsx` | One row per page for the workshop. |
| `work/` | Checkpoints. Delete the folder to start from scratch. |

Segments routed to a human (safety text, failed checks, no model output) are marked in the PDF with a dashed frame, a warning triangle and the text `[!]`. Untranslated text stays in English and is prefixed `[!] CHƯA DỊCH`. Pages that could not be processed carry a banner on top.

## Workshop review

For each page, compare the Vietnamese page with the English one and choose in column Verdict: `usable as is`, `usable with fixes` or `not usable`. Write what is wrong in Comment (term, number, sentence, layout). Check the `[!]` marks first. Send the file back; every comment becomes an eval case or a glossary change.

## Known limits

- Pages whose own `/Rotate` is set may place legends and markers off the visible area.
- Leftover English is counted from words that also occur in the source, so it misses English words the model invented.
- Vision reading quality on the real manual is not measured yet; only the synthetic eval exists. Treat every `[!]` block as unchecked.
- Vision reads are capped per run (`config/vision.yaml`: calls and seconds). A page that hits the cap is flagged `vision_incomplete`, never silently left unread.
- Pictures read by the model are covered by the Vietnamese text; the original pixels stay under it in the PDF.
