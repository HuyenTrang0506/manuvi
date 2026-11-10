# Translation quality evaluation (`manuvi eval`)

Goal: make translation quality measurable and stop regressions, offline and at $0. The ELECTRICAL content of the
Kobelco manual is the priority. Nothing here hard-codes a manual section number: cases carry `domains` tags
(for example `electrical`, `safety`, `wire_color`) and a `priority` tag (`p0` = founder-critical).

Related: ADR-007 (eval gate), ADR-010 (gateway profiles, data classes).

## What is in the box

| Piece | Where |
|---|---|
| Eval set (55 synthetic, original electrical-service sentences, frozen by hash) | `src/eval/data/electrical_v1.jsonl` + `.manifest.json` |
| Proposed glossary (66 terms, status `proposed`) | `src/eval/data/electrical_terms_proposed.csv` |
| Metrics | `src/eval/metrics.py` |
| Report + regression gate | `src/eval/report.py` |
| Runner (gateway, recorded replay) | `src/eval/runner.py` |
| LLM-as-judge design (not executed) | `src/eval/judge.py` |
| Baseline (reference replay) | `src/eval/baselines/electrical_v1.recorded-reference.json` |
| CI/worker hook | `scripts/eval_gate.sh`, `src.eval.runner.run_eval()` |

No sentence is copied from a real manual. The Vietnamese references and glossary terms are PROPOSALS written by the
engineering side: they are not approved by the founder or the uncle, and the reports say so.

## Case format (JSONL, one object per line)

| field | meaning |
|---|---|
| `id` | unique, never reused |
| `page` | synthetic page number, only used for cost per page |
| `source` | English text |
| `domains` | tags such as `electrical`, `safety`, `wire_color`, `connector`, `fuse_relay`, `torque`; at least one |
| `priority` | `p0` / `p1` / `p2` |
| `severity` | `critical` / `major` / `minor`: impact if this case is wrong |
| `expected_terms` | EN glossary keys that must appear with their VI term |
| `protected` | strings that must survive byte for byte (codes, IDs, values with units) |
| `reference_vi` | optional human reference |

The set is frozen: `electrical_v1.manifest.json` holds the SHA-256 of the canonical content (NFC-normalised). Loading a
modified set fails (`SetModified`, CLI exit 2). To change cases create `electrical_v2.jsonl`, run
`manuvi eval freeze <file>`, and create a new baseline in the same PR.

### How to add cases
1. Write an original English sentence (never paste manual text). Add `protected` strings for every code, ID and value.
2. Add a human `reference_vi` using the glossary terms; add the `expected_terms` keys (they must exist in the CSV).
3. Tag `domains`, `priority`, `severity`. Put new founder-critical content at `p0`.
4. Put it in a new set version, run `python -m pytest tests/eval` (the references must satisfy their own cases), freeze,
   regenerate the baseline (`manuvi eval run ... --write-baseline`), open a PR.

## Metrics

All deterministic, offline, NFC-normalised (Vietnamese NFD input is accepted everywhere).

| Metric | Kind | Definition |
|---|---|---|
| `protected_codes` | HARD | Every explicit `protected` string and every code the guard detects (`harness`, `part`, `pin`, `Fig./page`, `value+unit` patterns) appears in the output exactly (NFC only; a no-break space is a change). Codes in the output that are absent from the source also fail. Case passes only when nothing is lost or invented. |
| `number_unit` | HARD | Multiset of number(+unit) tokens equals the source's: `24 V` equals `24V`, `N·m`/`N•m`, `℃`, `Ω` variants and spacing around `±` are tolerated; a different digit, a lost `±`, a changed unit or a changed decimal separator (`0.5` to `0,5`, policy keep_source) fails. Digits that belong to codes (F23, C-12) are not counted as measurements. |
| `safety_signals` | HARD | Each `WARNING/CAUTION/DANGER/NOTE/IMPORTANT` in the source has its fixed VI form (`config/signal_words.yaml`) in the output, case-insensitive. A shouted VI signal that the source lacks (level changed) fails. |
| `glossary_hit` | soft | Expected terms found with the glossary VI term (micro-average over term occurrences). Per-term table in the report. Forbidden variants (`forbidden_vi`) make a case fail. |
| consistency | soft | Document level: a term rendered correctly in some segments must not be missing or replaced by a forbidden variant in others (same EN term, same VI term). Reported with the list of offending terms. |
| `length_ratio` | soft | VI/EN character ratio inside 0.6 to 1.9 (catches truncation and runaway output). |
| `leakage` | soft, lower is better | Share of output words that are English words from the source left untranslated (outside protected strings, ALL-CAPS tokens and expected VI words). |
| `placeholder_roundtrip` | soft | Placeholders `⟦Pnnn⟧` sent to the model come back once each, none dropped or invented (reordering is reported, not failed). Not applicable when scoring external hypotheses. |

### Thresholds and why

- **Hard metrics gate on any drop**, overall, per domain, per priority and per case (a case that passed in the baseline
  must still pass). A wrong code, number or warning level can hurt a technician or damage a machine, and these checks
  are exact, so there is no noise to tolerate. This is ADR-007's "100%" rule applied as "never worse than baseline".
- **Soft metrics gate on a relative drop above `--tolerance` (default 3%)**; for `leakage`, an increase of more than 3
  percentage points. They depend on wording choices and on a glossary still under review, so a small wobble is normal;
  3% on ~150 term occurrences is about 4 terms.
- The gate refuses to compare runs of different eval sets and warns when the glossary changed.
- Absolute targets for a release (not enforced by the gate): hard metrics 100%, `glossary_hit` >= 98% once the terms are approved.

## Running

```
# harness self-test and CI gate (replay of the human references; must pass)
scripts/eval_gate.sh

# default model: the fake adapter (plumbing check; it fails the gate, by design)
manuvi eval run --baseline src/eval/baselines/electrical_v1.recorded-reference.json

# replay recorded model output
manuvi eval run --model recorded --fixture my_model_run.jsonl --baseline <baseline.json>

# score translations made elsewhere ({"id","vi"} per line)
manuvi eval run --hypotheses translations.jsonl --baseline <baseline.json>
```

Results go to `workspace/eval/<run-id>/` (gitignored): `results.jsonl`, `report.json`, `report.md`, `gate.json`.
Exit codes: 0 pass, 1 regression, 2 modified/unfrozen set. The report includes cost per page (tokens x price table, from
the gateway ledger); it is $0 for fake, recorded and Ollama.

`--model` goes through the gateway on the `dev` profile (`MANUVI_MODEL_BULK` override), so ADR-010 applies: caps, cache,
and `data_class=customer` reaches only private adapters. Paid models stay off unless `allow_paid` and caps are set
explicitly; real-model runs are designed here but not executed.

### On the Mac with Ollama
```
ollama pull qwen2.5:7b            # or the model chosen for the qa profile
manuvi eval run --model ollama:qwen2.5:7b --baseline src/eval/baselines/electrical_v1.recorded-reference.json
```
Ollama on `localhost` is allowed for customer data; the run is slow but free. Compare against the baseline to see which
metrics a local model fails. Do not tune prompts on Ollama results alone (ADR-010).

### Worker / CI hook
`src.eval.runner.run_eval(...)` is the one entry point and returns `EvalRun(report, run_dir, gate)`; `scripts/eval_gate.sh`
wraps it for CI. A GitHub Actions job can call the script on PRs touching prompts or translation code; a worker can call
`run_eval(model=..., baseline=...)` before delivering a job and attach `report.md`.

## LLM-as-judge (design only, not executed)

`src/eval/judge.py` holds the rubric prompt, the JSON schema (scores 1 to 5 for terminology, fluency, completeness,
safety; blocking issues; verdict) and the call wrapper.

- Routed through the gateway with `data_class=customer` fixed (no parameter relaxes it), alias `strong`. The gateway
  refuses any adapter that does not accept customer data, so a free hosted tier cannot receive it (test:
  `test_judge_refuses_free_hosted_adapter_for_customer_data`). Allowed: Ollama on localhost, recorded replay, or an
  adapter explicitly opted in for customer data.
- The judge reports, it does not gate (ADR-007) until calibrated. `effective_verdict()` only makes the verdict stricter
  than the model's own claim (safety <= 2 or a safety issue means fail).

### Calibration plan
1. Pick 50 segments from the uncle's workshop material, stratified: 20 safety/warnings, 15 wiring/connectors/fuses,
   15 sensors/controllers and procedures. Include some deliberately bad translations (about 30%).
2. Two people label each segment pass / needs_review / fail plus the four dimension scores, with a written guideline
   (the rubric text). The uncle labels terminology and safety, an internal reviewer labels fluency and completeness.
3. Resolve disagreements; record human-human Cohen's kappa (`cohen_kappa()` in `judge.py`). If it is below 0.6 fix the
   guideline first.
4. Run the judge on the same 50 locally; compute judge-vs-human kappa on the verdict and on binarised dimensions.
5. Targets: kappa >= 0.6 on the verdict and >= 0.7 on `safety`; recall of human `fail` >= 0.9. Below target the judge stays
   informational. Re-run calibration when the judge model or rubric changes.

## What to measure on the real manual
1. Run the deterministic metrics on every translated segment of the electrical chapter (use `--hypotheses`, or add the
   segments as a private set kept out of git, same JSONL format). Do not hard-code the chapter number: tag segments
   `electrical` when the section is confirmed.
2. Per `p0` segment: codes, numbers and signal words must be 100%; list every failing segment for human review.
3. Terminology: approve the proposed terms first (CSV/Excel template below), then track `glossary_hit` per term and the
   consistency list over the whole document.
4. Cost per page and latency per model, from the report's cost section, for each candidate model.
5. A human spot check of 50 segments (the calibration set) per candidate model, to see what the metrics miss.
6. Watch `leakage` and `length_ratio` outliers: they flag omitted or untranslated text the other metrics cannot see.

## Glossary review template

`src/eval/data/electrical_terms_proposed.csv` (also `manuvi eval glossary-template out.xlsx` for Excel, with a README
sheet) lists 66 electrical terms with a proposed Vietnamese term, `status=proposed`, and empty `reviewer_decision`,
`reviewer_vi`, `reviewer_comment` columns. The founder/uncle decide; approved terms enter the term store through the
existing import (needs citation and approver). Nothing in this harness marks a term approved.

## First real-model run (2026-11-09, `ollama:qwen2.5:7b`, Apple M1)

Run before the signal-word split and the script guard existed. 55 cases, cost $0.

| Metric | Kind | Value |
|---|---|---|
| protected_codes | hard | 97.3% |
| number_unit | hard | 95.5% |
| safety_signals | hard | 83.3% (n = 6) |
| glossary_hit | soft | 94.4% |
| length_ratio | soft | 98.2% |
| leakage | soft | 0.3% |
| placeholder_roundtrip | soft | 96.2% |

**Not release quality.** Both hard failures were p0 safety cases:

- `EL-004`: Chinese text appended after the translation and the `24 V` placeholder dropped.
- `EL-005`: `CAUTION` rendered as a wrong signal word, and "tighten" rendered as "quấn chặt".

What changed because of this run:

1. Signal words are split off before the model call and re-attached from `config/signal_words.yaml` (proposed table).
2. Output containing foreign scripts fails the guard, enters the fix loop (max 2) and is otherwise routed to review.
   Failed output is never shipped.
3. `manuvi eval` runs the same guard path as the pipeline and reports repaired and routed cases separately.
4. Glossary entries may set `must_use_in_prompt` (for example tighten -> siết).

Policy: a 7B local model is for plumbing tests only. **p0 cases (safety, warnings) must run on the `strong` alias.**
The electrical chapter of the Kobelco SK70SR-1ES manual is Section 43 (founder, 2026-11-10); real-manual checks start there.
