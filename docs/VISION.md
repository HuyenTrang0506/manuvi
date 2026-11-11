# Reading text without a text layer (vision model, no OCR)

Founder decision: no traditional OCR. Pages or picture areas without usable text are read by a vision-language model (VLM) behind the gateway alias `vision`, with `data_class=customer`, so only local/private models receive the page image. The gateway keeps refusing free hosted APIs (ADR-010). Real manual facts: 1012 pages, all 64 sampled pages have a text layer, so this path is the exception.

## Pieces

| File | Job |
|---|---|
| `src/intake/vision_detect.py` | Per page: chars per area, image coverage, glyph-less spans; decides `none`, `page` or `regions` (picture areas with no text-layer text on a page that has text). |
| `src/intake/vision_read.py` | Render PNG (dpi from page size, tiles when large or dense), prompt and JSON schema, call the gateway, map boxes back to PDF coordinates (respects `/Rotate` and CropBox), validate, merge with the text layer, budget. |
| `src/intake/vision_eval.py` | 10 synthetic image-label cases scored on exact text and box IoU. |
| `config/vision.yaml` | Render sizes, thresholds and the per-job caps (model calls, seconds). |
| `config/profiles.yaml` | `vision` alias per profile and the verified Ollama tags. |
| `scripts/mac/vision_smoke.sh` | One synthetic page, real model on the Mac, prints PASS or FAIL. |

## Validation (a failed check flags the block `needs_review`)

`low_confidence` (below 0.5 or missing), `bbox_oob` (box sticks out; clamped), `overlap` (more than 50% of the smaller box), `script` (CJK and other non-Latin scripts unless the page text layer has Japanese; also look-alike letters inside codes), `code_confusable` (O/0, l/1, S/5, B/8 swaps that would match a protected pattern), `code_vs_textlayer` (one edit away from a different code on the same page), `text_box_mismatch` (too much text for the box), `repetition`, `conflicts_text_layer`. Empty text and exact repeats are dropped. `script` and an unplaceable box are blocking: reported, never translated or drawn.

## Judging a model

```
scripts/mac/vision_smoke.sh            # 1 case, PASS/FAIL
scripts/mac/vision_smoke.sh --full     # 10 cases: text exact match, mean IoU, invented blocks
```

Bar before trusting a model on the real manual: exact match >= 90%, mean IoU >= 0.6, no invented blocks, no failed case. This is a floor on clean synthetic labels. The next step is to add cases copied in structure from real Section 43 diagram labels, then compare tags (`qwen3-vl:8b`, `qwen3-vl:4b`, `qwen2.5vl:7b`, `gemma3:12b`) with the same script.

## Known limits

- Box coordinate conventions differ per model; `bbox_scale` in `config/vision.yaml` must match (1 for 0..1, 1000 for 0..1000). Unverified per model until the smoke test is run.
- Rotated text inside a picture (not the page) is not requested separately.
- The renderer draws over the original pixels; it does not edit the raster.
