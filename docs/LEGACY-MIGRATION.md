# Legacy migration

The first version of this repo was a Cursor-driven, semi-manual EN->VI pipeline (`src/cli.py` with ~30 commands). The new architecture is `intake`, `guard`, `terms`, `pipeline`, `llm_gateway`, `webapp`, `render`, `labels`, `common`.

## Method

1. Built an import graph with `ast` over `src/`, `tests/`, `scripts/`, `spikes/`.
2. Result: **no new package imports any old module.** The only consumers of old code are the old CLI, the old modules themselves, `tests/test_label_semantics.py` and `scripts/reextract_vision_legends.py`.
3. Verdict rules: KEEP if needed by the new design (PDF parsing, figures, noise filter, diagram labels, glossary seed) or if it is in the import closure of those or of a live test; MOVE to `legacy/` if superseded or unused but worth reading; DELETE only if dead and superseded. **Nothing was deleted**: every file is either kept or preserved in `legacy/` (git history keeps the moves traceable with `git log --follow`).
4. The glossary seed `src/glossary/seeds/construction_electrical_en_vi.csv` was not touched and is now declared as package data.

`legacy/` is not imported, not packaged (`pyproject.toml` excludes it) and not collected by pytest (`testpaths = ["tests"]`).

## Table

| Module | What it does | Verdict | Reason | Replacement |
|---|---|---|---|---|
| `src/ingest/parser.py` | PDF -> blocks with page/section refs | KEEP | Needed by the new design (PDF parsing) | none yet; `intake` only classifies |
| `src/ingest/text_structure.py` | Keeps line breaks and refs, XML cleaning | KEEP | Parser dependency | - |
| `src/ingest/noise_filter.py` | Strips repeated headers/footers, defines figure-ref pattern | KEEP | Noise filter is required | - |
| `src/ingest/figure_extractor.py`, `figure_regions.py` | Extract figures / diagram regions by bbox | KEEP | Figure extraction is required | - |
| `src/ingest/diagram_labels.py`, `diagram_filter.py` | Extract labels on wiring diagrams | KEEP | Diagram label extraction is required (feeds `src/labels`) | - |
| `src/glossary/manager.py` + `seeds/*.csv` | Load/merge EN->VI glossary, seed data | KEEP | Seed data must be preserved; `ingest` imports the manager | `src/terms` store (future: import the seed into it) |
| `src/models.py` | Pydantic models for the old document model | KEEP | `ingest`, `glossary` and live tests import it | `src/guard/models.py` for QA types |
| `src/qa/label_validator.py`, `mix_detector.py` | Mixed/untranslated label checks | KEEP | In the import closure of `ingest` | `src/guard` for new checks |
| `src/translate/label_translator.py`, `wire_labels.py`, `equipment_list.py`, `harness_callouts.py`, `ocr_guess.py` | Rule-based label / wire-code translation | KEEP | `ingest.diagram_labels` imports them | - |
| `src/translate/chunker.py`, `engine.py`, `list_translator.py`, `section43_map.py`, `vision_legend.py` | Chunks, glossary engine, list/heading phrases, section 43 map, vision legend loading | KEEP (temporary) | Import closure of `tests/test_label_semantics.py` (regression tests for labels/TOC). `vision_legend` now calls models only through the gateway (alias `vision`, customer data, local models); a test fails if any module under `src` imports an LLM SDK or HTTP client outside `src/llm_gateway`. Extract the pure parts, then move it | `pipeline` + `llm_gateway` |
| `src/assemble/label_table.py`, `toc.py`, `wire_appendix.py` | Label legend table, TOC, wire appendix | KEEP (temporary) | Same test closure | `src/render`, `src/labels` |
| `src/assemble/docx_builder.py`, `pdf_builder.py`, `pdf_exporter.py`, `pdf_fonts.py`, `figure_index.py` | DOCX/PDF build with TOC and figure index | MOVE | Not imported; superseded by layout-preserving `render` | `src/render` |
| `src/excel/io.py` | Export/import chunks and glossary to Excel | MOVE | Tied to old chunk files | `src/terms/excel_io.py` |
| `src/qa/checker.py`, `output_test.py`, `page_classifier.py`, `page_sampler.py`, `page_validator.py`, `legend_auditor.py`, `untranslated_collector.py` | Old QA reports and audits | MOVE | Not imported; superseded | `src/guard`, `src/intake/classify.py` |
| `src/translate/apply.py`, `auto_captions.py`, `batch_text.py`, `fix_chunks.py`, `remap.py`, `mark_diagrams.py` | Cursor-driven apply/fix/remap of translated chunks | MOVE | Manual workflow, unused | `src/pipeline` |
| `src/translate/flowchart.py`, `flowchart_redraw.py`, `hybrid_page.py`, `label_translator`-adjacent `ocr_labels.py`, `section43_table.py` | Flowchart redraw, hybrid pages, OCR labels, section 43 table | MOVE | Useful reference for later features, not imported | `src/render` / `src/labels` (planned) |
| `src/cli.py` (30 commands) | Old Typer CLI | MOVE to `legacy/src/cli_old.py`; new `src/cli.py` written | Most commands call moved modules | `manuvi intake / pipeline / terms / webapp` |
| `prompts/translate_electrical.md` | Old Cursor translation prompt | MOVE | Reference for prompt design | `src/terms/prompt.py` |
| `scripts/reextract_vision_legends.py` | Re-run vision legend extraction | MOVE | Imports the removed `_load_dotenv` (no API keys any more); port to a gateway call before use | gateway |
| `scripts/check_no_uncached_paid_calls.py`, `make_demo_job.py` | New tooling | KEEP | New design | - |
| `docs/00..09-*.md`, `docs/README.md` | Original Vietnamese design docs | MOVE to `legacy/docs/` | Superseded by ADRs and `AGENT-CONTEXT.md`; `job-format.md` kept | - |
| `.cursor/skills/*` | Cursor skills for the old workflow | MOVE to `legacy/cursor/skills/` | Cursor-specific | `docs/TEAM-ROLES.md` |

## Other changes

- `src/cli.py` now exposes only `intake`, `pipeline` (run, approve, demo, status, rerun), `terms` (propose, export, import) and `webapp`. Entry point renamed `translate-manual` -> `manuvi`.
- `pyproject.toml`: removed unused `python-docx`, `rich`, `docx2pdf`; `requires-python >=3.13`; pytest `testpaths`; seed CSV declared as package data; `legacy*` excluded from packaging.
- Test suite unchanged (no tests removed: every existing test targets a kept module).

## Follow-ups

- Move `vision_legend`, `label_table`, `toc`, `engine` out of `src/` once `test_label_semantics.py` is split into pure-function tests.
- Import the glossary seed into the `src/terms` store, then retire `src/glossary/manager.py`.
