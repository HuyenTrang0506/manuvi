# Translation quality evaluation (`manuvi eval`)

Goal: make translation quality measurable and stop regressions, offline and at $0. The ELECTRICAL content of the
Kobelco manual is the priority. Nothing here hard-codes a manual section number: cases carry `domains` tags
(for example `electrical`, `safety`, `wire_color`) and a `priority` tag (`p0` = founder-critical).

Related: ADR-007 (eval gate), ADR-010 (gateway profiles, data classes).

## What is in the box

| Piece | Where |
|---|---|
| Eval set v2 (75 synthetic, original electrical-service sentences, frozen by hash; v1 = the first 55, kept unchanged) | `src/eval/data/electrical_v2.jsonl` + `.manifest.json` |
| Glossary (68 terms: 31 `verified-usage`, 37 `proposed`, plus 6 keep-English names) | `src/eval/data/electrical_terms_proposed.csv` |
| Metrics | `src/eval/metrics.py` |
| Report + regression gate | `src/eval/report.py` |
| Runner (gateway, recorded replay) | `src/eval/runner.py` |
| LLM-as-judge design (not executed) | `src/eval/judge.py` |
| Baseline (reference replay) | `src/eval/baselines/electrical_v2.recorded-reference.json` |
| Model comparison | `scripts/mac/compare_models.sh`, `src/eval/compare.py` |
| CI/worker hook | `scripts/eval_gate.sh`, `src.eval.runner.run_eval()` |

No sentence is copied from a real manual. The Vietnamese references use the canonical glossary forms.

**Terms policy (founder, 2026-11-11):** terms do not need sign-off from the workshop as long as they match how
Vietnamese mechanics and electricians really write. So each term is checked against Vietnamese sources instead:
`verified-usage` = at least one credible source in the `source` column uses it; `proposed` = no source found, the
`note` says why. A mechanic may still change a term later; the reports say how many terms are in each state.

**English words (founder rule):** the translation keeps English only for names: model and part names, codes,
connector/fuse/relay IDs, abbreviations (ECU, SOL, SW, GND, B+), wire colour codes (W/R, LgY) and brand names. Every
other term has a Vietnamese form, and "Vietnamese (English term)" parentheses are not allowed. The translation prompt
says so (`src/terms/prompt.py`, rule 5, prompt version `eval-v3`), and `leakage` plus `kept_names` measure it.

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
| `keep` | names and abbreviations that must stay in English (`ECU`, `SOL`, `B+`, `W/R`, `LgY`); checked by `kept_names`. Optional; a set without it keeps its old hash |

v2 adds 20 cases (EL-056 to EL-075) shaped like Section 43 page types, all original text: wiring-diagram legends and
abbreviation lists, connector pin tables, fuse and relay lists with ratings, wire colour codes, harness names,
component location notes, and "leakage trap" sentences where a weak model tends to leave `harness`, `connector`,
`switch`, `relay box` in English. v1 is untouched (same hash), so older numbers stay comparable on its 55 cases.

The set is frozen: `electrical_v2.manifest.json` holds the SHA-256 of the canonical content (NFC-normalised). Loading a
modified set fails (`SetModified`, CLI exit 2). To change cases create `electrical_v2.jsonl`, run
`manuvi eval freeze <file>`, and create a new baseline in the same PR.

### How to add cases
1. Write an original English sentence (never paste manual text). Add `protected` strings for every code, ID and value.
2. Add a human `reference_vi` using the canonical glossary terms; add the `expected_terms` keys (they must exist in the CSV) and a `keep` list for every abbreviation or colour code that must stay English.
3. Tag `domains`, `priority`, `severity`. Put new founder-critical content at `p0`.
4. Put it in a new set version, run `python -m pytest tests/eval` (the references must satisfy their own cases), freeze,
   regenerate the baseline (`manuvi eval run ... --write-baseline`), open a PR.

## Metrics

All deterministic, offline, NFC-normalised (Vietnamese NFD input is accepted everywhere).

| Metric | Kind | Definition |
|---|---|---|
| `protected_codes` | HARD | Every explicit `protected` string and every code the guard detects (`harness`, `part`, `pin`, `Fig./page`, `value+unit` patterns) appears in the output exactly (NFC only; a no-break space is a change). Codes in the output that are absent from the source also fail. Case passes only when nothing is lost or invented. |
| `number_unit` | HARD | Multiset of number(+unit) tokens equals the source's: `24 V` equals `24V`, `N·m`/`N•m`, `℃`, `Ω` variants and spacing around `±` are tolerated; a different digit, a lost `±`, a changed unit or a changed decimal separator (`0.5` to `0,5`, policy keep_source) fails. Digits that belong to codes (F23, C-12) are not counted as measurements. |
| `kept_names` | HARD | Every string in the case's `keep` list appears in the output exactly as written (case-sensitive, whole token). `ecu` or a translated `ECU` fails. Not applicable to cases with an empty `keep`. |
| `safety_signals` | HARD | Each `WARNING/CAUTION/DANGER/NOTE/IMPORTANT` in the source has its fixed VI form (`config/signal_words.yaml`) in the output, case-insensitive. A shouted VI signal that the source lacks (level changed) fails. |
| `glossary_hit` | soft | Expected terms found with the canonical VI term **or any listed variant** (see "Variants rule"). Micro-average over term occurrences; the case detail lists which terms matched through a variant. Per-term table in the report. Forbidden variants (`forbidden_vi`, whole words, codes such as `Pin 1` masked first) make a case fail. |
| consistency | soft | Document level: a term rendered with an accepted form in some segments must not be missing or replaced by a forbidden variant in others (same EN term, same VI term). Reported with the list of offending terms, and with `mixed_forms` (terms that used more than one accepted form: allowed, but pick one for the final manual). |
| `length_ratio` | soft | VI/EN character ratio inside 0.6 to 1.9 (catches truncation and runaway output). |
| `leakage` | near-hard, lower is better | Share of output words that are English words from the source left untranslated (outside protected strings, `keep` names, brand names, ALL-CAPS tokens and expected VI words). The report lists the leftover words per case. Gate: **<= 0.5% overall and 0 leftover words in any p0 case**, on top of the usual no-worse-than-baseline check. An English term in parentheses counts as leakage. |
| `placeholder_roundtrip` | soft | Placeholders `⟦Pnnn⟧` sent to the model come back once each, none dropped or invented (reordering is reported, not failed). Not applicable when scoring external hypotheses. |

### Thresholds and why

- **Hard metrics gate on any drop**, overall, per domain, per priority and per case (a case that passed in the baseline
  must still pass). A wrong code, number or warning level can hurt a technician or damage a machine, and these checks
  are exact, so there is no noise to tolerate. This is ADR-007's "100%" rule applied as "never worse than baseline".
- **`leakage` has absolute limits** (0.5% overall, 0 in p0) because the founder does not accept English words in the translation; `kept_names` is hard for the same reason.
- **Soft metrics gate on a relative drop above `--tolerance` (default 3%)**; for `leakage`, an increase of more than 3
  percentage points. They depend on wording choices and on a glossary still under review, so a small wobble is normal;
  3% on ~150 term occurrences is about 4 terms.
- The gate refuses to compare runs of different eval sets and warns when the glossary changed.
- Absolute targets for a release (not enforced by the gate): hard metrics 100%, `glossary_hit` >= 98% once the terms are approved.

## Variants rule (glossary matching)

Mechanics do not all write the same word (`rơ le`, `rơ-le`; `giắc`, `giắc cắm`, `đầu nối`; `củ đề`, `máy khởi động`).
Each glossary row has one canonical `term_vi_proposed` and a `variants_vi` list (separated by `|`). Rules:

1. A term counts as a hit when the output contains the canonical form or any variant.
2. Matching is on NFC text, case-insensitive, ignores hyphens and spaces (`rơ-le` = `rơ le` = `rơle`) and old versus new
   tone placement (`hoà` = `hòa`, `thuỷ` = `thủy`). Vietnamese has no inflection, so a substring test on this key is enough.
3. A variant must be a Vietnamese form used in workshops or textbooks. English words (`relay`, `diode`) are never variants.
4. The prompt and the reference translations use the canonical form only, so the finished manual is consistent;
   variants only stop the metric from punishing a legitimate synonym.
5. Bare words that occur inside other words are not canonical forms (for ground: `nối mát`, not `mát`, which is also
   in `làm mát`).

The five inconsistent terms of the second run were fixed by choosing one canonical form each (connector `giắc nối`,
harness `bó dây điện`, ground `nối mát`, relay `rơ le`, monitor `màn hình hiển thị`); the guess is based on the glossary,
because the run's per-term table was not available here. Re-run to confirm.

## Running

```
# harness self-test and CI gate (replay of the human references; must pass)
scripts/eval_gate.sh

# default model: the fake adapter (plumbing check; it fails the gate, by design)
manuvi eval run --baseline src/eval/baselines/electrical_v2.recorded-reference.json

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
manuvi eval run --model ollama:qwen2.5:7b --baseline src/eval/baselines/electrical_v2.recorded-reference.json
```
Ollama on `localhost` is allowed for customer data; the run is slow but free. Compare against the baseline to see which
metrics a local model fails. Do not tune prompts on Ollama results alone (ADR-010).

### Comparing several models (Apple M1, about 11.8 GB usable)

```
scripts/mac/compare_models.sh                      # default list
scripts/mac/compare_models.sh qwen2.5:7b gemma3:12b-it-q4_K_M   # your own list
NO_PULL=1 MAX_MODEL_GB=9.5 scripts/mac/compare_models.sh        # only installed models, stricter size limit
```

Run it inside the project virtualenv (`PYTHON=python` by default). For each model the script pulls it, skips it when the
pull fails or when the download is larger than `MAX_MODEL_GB` (default 10.0, leaving room for context), runs
`manuvi eval run --model ollama:<tag>`, treats an out-of-memory error as "skipped" and continues, then unloads the model.
It writes `workspace/eval/compare-<date>.md` with one row per model and per-model `report.md` under
`workspace/eval/cmp-<date>-<model>/`. bash 3.2 compatible and shellcheck-clean.

Default list, checked on ollama.com/library on 2026-11-11 (sizes are the download sizes; 4-bit means `q4_K_M`):

| Tag | Size | Why |
|---|---|---|
| `qwen2.5:7b` | about 4.7 GB | baseline of the first two runs |
| `translategemma:12b-it-q4_K_M` | 8.1 GB | translation-specialised 12B. English to Vietnamese is in its WMT24++ evaluation (MetricX 2.20 for 12B versus 2.88 for Gemma 3 12B, lower is better; TranslateGemma technical report, Table 4) |
| `gemma3:12b-it-q4_K_M` | 8.2 GB | general 12B (`gemma3:12b` points to the same build) |
| `qwen2.5:14b-instruct-q4_K_M` | 9.0 GB | 14B-class, at the memory limit |

Left out of the default, on purpose:

- `translategemma:27b`, `gemma3:27b` and any `q8_0`/`bf16` tag: 13 GB or more, they do not fit.
- Licences were not checked; check them before using a model on customer text beyond the pilot.
- Whether TranslateGemma works well with this prompt is unknown: it was trained with its own prompt format, and Ollama's
  template for it was not inspected. Read its row with that in mind and, if it looks poor, test the prompt before dropping it.
- The Ollama pages say "55 languages" without listing them; Vietnamese was confirmed from the technical report, not from Ollama.

**How to read the comparison table.** Columns: the four hard metrics (`protected`, `numbers`, `safety`, `kept names`) on
accepted output; `routed` = segments the guard sent to review (share of the 75); `repaired` = segments the fix loop
repaired; `glossary hit`, `consistency`, `leakage`; `s/segment` = wall time per segment on this machine (the first model
includes loading time). A model is a candidate only when the four hard metrics are 100%, `routed` <= 5%, `glossary hit`
>= 97%, `leakage` <= 0.5% and the speed lets 30 pages finish overnight (about 10 segments per page; at 6 s/segment that
is 30 min per 300 segments). If every model fails a column, look at its `report.md` for the failing cases before judging
the model: a glossary or reference mistake looks the same as a model mistake. If none qualifies, see workstream E of
`docs/IMPROVEMENT-PLAN.md`. Do not choose by one number: p0 (safety) cases must pass on the model that will run them.

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
3. Terminology: use the usage-checked glossary (no workshop sign-off needed), track `glossary_hit` per term and the
   consistency list over the whole document, and add a case or a variant whenever a mechanic corrects a term.
4. Cost per page and latency per model, from the report's cost section, for each candidate model.
5. A human spot check of 50 segments (the calibration set) per candidate model, to see what the metrics miss.
6. Watch `leakage` and `length_ratio` outliers: they flag omitted or untranslated text the other metrics cannot see.

## Glossary (usage-checked)

`src/eval/data/electrical_terms_proposed.csv` (also `manuvi eval glossary-template out.xlsx` for Excel, with a README
sheet) lists 68 electrical terms plus 6 names that stay in English. Columns added in this version: `variants_vi`,
`source`, `keep_english`. Status values: `verified-usage` (31 terms, at least one Vietnamese source supports the canonical
term), `proposed` (37 terms, no source found; the `note` says what was found instead), `keep-english` (ECU, SOL, SW, GND,
B+, ACC). The file name is historical. The reviewer columns stay for a mechanic who wants to change a term; nobody
has to sign off. Approved-term import into the term store still needs a citation and an approver.

Sources used (all read on 2026-11-11): Sao Do University and UPT vocational course outlines and textbook for car
electrics (`saodo.edu.vn` oto213, oto017, oto039, oto317, oto347; `kythuat.upt.edu.vn` engine electrics textbook),
the Vietnamese editions of a Toro operator manual and an Isuzu truck owner manual, a Kyoritsu multimeter manual, the
Vietnamese Cat parts site, and three trade and car-media pages (`mytour.vn`, `oto.com.vn`, `vannuoc.vn`,
`thegioicongnghiep.com`). No Komatsu, Kobelco or Hitachi Vietnamese service manual was found online, so heavy-equipment
usage is inferred from the vehicle and parts sources. Terms with no source (mostly compounds such as `cảm biến nhiệt độ dầu
thủy lực`, wire colours, `bugi sấy`, `bó dây`, `aptomat`) stay `proposed`; `củ đề`, `giắc` and `bó dây` are workshop
words that the founder named and that could not be confirmed from a page (the `củ đề` page title was seen in search
results only), so they are accepted as variants or canonical forms without a `verified-usage` mark.

Signal words (`config/signal_words.yaml`) are all `verified-usage`: NGUY HIỂM, CẢNH BÁO, THẬN TRỌNG, LƯU Ý and QUAN TRỌNG
appear in Vietnamese manuals (Toro, Isuzu, Kyoritsu). Isuzu writes CHÚ Ý for the third level and TCVN 2572 uses "Chú ý!";
THẬN TRỌNG was kept so that CAUTION and NOTE never collide. The TCVN safety-sign pages that could be read are paywalled
and do not show the three ISO words.

## Second real-model run (founder, `ollama:qwen2.5:7b`, Apple M1, v1 set of 55 cases, with the guard)

| Metric | Value |
|---|---|
| hard metrics on accepted output | 100% |
| repaired by the fix loop | 2 |
| routed to review | 2 of 55 (SCR001, foreign script) |
| glossary_hit | 93.5% |
| consistency | 92.2% (5 inconsistent terms) |

The guard worked: nothing wrong was accepted. The weak points are wording: glossary hit and consistency. The variants rule,
the single canonical forms and the new leakage limit address them; the v2 set (75 cases) needs a new run with
`compare_models.sh`, and its numbers are not comparable to this table.

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
