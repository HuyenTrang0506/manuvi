# Supported document formats

Manuvi returns the **same format** it was given. PDF keeps its own intake and renderer (`src/intake`, `src/render`);
Markdown, DOCX and PPTX go through the adapters in `src/formats/`. All formats share the same guard (placeholders for
codes, number/unit and signal-word checks, script guard, glossary prompts, fix loop, review gates).

## Use

```
manuvi translate-file manual.docx --model fake --out out/          # offline demo model
manuvi translate-file manual.docx --model ollama:qwen2.5:7b --out out/
```

Output: `out/<name>.vi.<ext>` and `out/<name>.vi.<ext>.segments.json` (the review data). In the hosted worker a job whose
`JobRecord.format` is `md|docx|pptx` is stored as `input.<format>` and produces `out/<name>.vi.<ext>` and
`out/segments.xlsx`. The web app's download links for the new file name are a follow-up in `src/webapp`.

## How it works

`extract(path) -> list[Segment]` and `rebuild(path, translations, out)` are deterministic and stateless (rebuild re-runs
extract, so ids `S0001...` are stable). A segment has `text`, `kind`, `protected` spans, `context`, `page`, `section`.

- Inline formatting is carried as tags in the text: `<b1>bold</b1>`, `<l2>link text</l2>`, `<r3>run</r3>`, `<x4/>`.
  The model may reorder them; the guard (`FMT001`) requires every tag exactly once and properly nested. If tags are
  wrong after the fix loop, the segment goes to `needs_review` and `rebuild` keeps the source text for it.
- Things that must never change (code spans, URLs, LaTeX, HTML, emoji shortcodes, footnote labels) stay in the text and
  are listed in `protected`; the guard masks them with placeholders like part numbers.
- Founder rule "no English left except names and codes": `LEAK001` (warning) uses the same leakage metric as
  `manuvi eval` (imported read-only).
- `segments.json` has the PDF shape, so the review app shows EN/VI side by side. `source`/`target` contain the tags.

## Markdown (`.md`, `.markdown`)

Parser: markdown-it-py (CommonMark + GFM tables and strikethrough; source line ranges per block).

Translated: paragraphs, headings (ATX and setext), list items and task items, table cells, blockquotes, link text, image
alt text, front matter values of `title`, `description`, `summary`, `subtitle`, footnote definition text.
Never changed: fenced and indented code, code spans, URLs, link and image targets, link titles, reference definitions,
HTML tags and blocks, comments, front matter keys and other values, footnote labels, emoji shortcodes, inline and
display LaTeX, escapes (`\*`), entities, anchors, heading levels, list markers and numbering, table delimiter rows.
Only translated ranges are spliced into the original text, so everything else, including line endings (LF/CRLF) and the
presence or absence of a final newline, is byte-identical. Test: identity round trip on a corpus of tricky files.

## DOCX (`.docx`)

Direct OOXML (zipfile + lxml), not python-docx: footnotes, endnotes, text boxes and tracked-change runs are out of
python-docx's reach, and untouched package entries are copied byte for byte.

Translated: body, headings, list items, table cells (merged cells keep `gridSpan`/`vMerge`), text boxes, headers,
footers, footnotes, endnotes, hyperlink text, accepted text of tracked insertions (`w:ins` markup kept).
Kept as is: styles, numbering, section settings, field codes and field results (PAGE, TOC, REF ...), `w:del` content,
comments, drawings, math. Runs with the same formatting merge into one span; spans, hyperlinks and insertions become tags so
bold/italic/colour/size follow the words. Document language is set to `vi-VN` (document defaults and translated runs).
Fonts are kept; a run whose explicit font is not in the Vietnamese-capable list (`ooxml.VI_FONTS`) gets Arial
(counted in the rebuild report). Theme fonts are never touched.

## PPTX (`.pptx`)

Translated: text in all shapes including groups, table cells, speaker notes, chart titles. Slide numbers, dates and other
fields, pictures, SmartArt, layouts and masters are left alone. Run formatting is kept as for DOCX.
Shrink-to-fit guard: if the translation is estimated to overflow its shape (and is longer than the source was), the shape
gets `normAutofit` with font scale 96%, 92% or 88% (the floor). If 88% is still not enough, the segment is flagged
`needs_review` (`OVERFLOW`) in `segments.json`. The estimate is a character-width heuristic, not a layout engine.

## Safety limits

Format = extension and magic bytes must agree. Rejected before parsing, with a short job error code:
`FILE_TOO_LARGE` (default 50 MB, Markdown 5 MB), `ZIP_BOMB` (more than 5000 entries, 200 MB unzipped, or a compression
ratio above 150 on a large entry), `MACRO_REJECTED` (`.docm/.pptm/.dotm`, legacy `.doc/.ppt`, any `vbaProject.bin`, macro
content types; VBA is never preserved or run), `BAD_FORMAT`, `UNSUPPORTED_FORMAT`, `TOO_MANY_SEGMENTS`.
Limits: `MANUVI_MAX_FILE_MB`, `MANUVI_MAX_MD_MB`, `MANUVI_MAX_UNZIPPED_MB`, `MANUVI_MAX_ZIP_ENTRIES`, `MANUVI_MAX_ZIP_RATIO`.
XML is parsed without entity resolution, DTD loading or network access. Nothing is executed; nothing is extracted to disk.

## Known limits

- Markdown: footnote definitions are translated only when on one line; reference link titles and `<details>` HTML content
  are not translated; shortcut reference links (`[label]`) stay verbatim because the visible text is the label; a
  translated table cell is not re-padded to align with the other rows.
- DOCX: complex-field results (e.g. a HYPERLINK field's visible text, TOC entries) stay English; comments, SmartArt,
  charts and document properties are not translated; text-box copies in `mc:Fallback` are translated separately (extra
  cost); comment range markers may move to the start of a rebuilt paragraph; the font check is a list, not a glyph test.
- PPTX: overflow estimate is heuristic; PowerPoint re-evaluates `normAutofit` only when the text is edited; table rows
  grow automatically and are not checked; SmartArt and embedded charts' data labels are not translated.
- Not supported yet: XLSX, legacy `.doc`/`.ppt`, `.odt`, HTML.
- Identical English paragraphs are sent to the model separately (no translation memory across segments yet).
