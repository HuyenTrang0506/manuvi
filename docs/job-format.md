# Job directory format

A job is one directory. The pipeline (`src/pipeline`) writes it; the review app (`src/webapp`) reads and edits `segments.json`.

| File | Written by | Content |
|---|---|---|
| `chunks.json` | intake / `--pdf-text-fixture` | input: list of `{id, page, section, source, kind?}` |
| `segments.json` | pipeline, then web app | output, see below |
| `trace.jsonl` | pipeline | one record per graph node run (no customer text) |
| `job.sqlite` | pipeline | chunk table, finished segments, run meta |
| `ledger.sqlite` | pipeline | step ledger: one row per gateway call (idempotency guard) |
| `checkpoints.sqlite` | LangGraph | `SqliteSaver`, `thread_id` = job id |
| `llm_cache.sqlite` | gateway | response cache and spend ledger |

## segments.json

JSON list, one object per processed chunk (schema: `src/pipeline/schema.py`, class `Segment`).

```json
{"id": "C003", "page": 1, "section": "23", "source": "WARNING: ...", "target": "CẢNH BÁO: ...",
 "status": "needs_review", "issues": ["SAFETY: signal segment needs human approval"],
 "risk": 0.8, "kind": "signal", "decision": "pending", "_pv": 1}
```

- `status`: `ok | warn | error | needs_review`.
  - `needs_review`: a deterministic QA error survived 2 fix attempts, a chunk could not be masked, or the chunk is a safety (`signal`) segment. Signal segments are never `ok` from the pipeline; the human decides in the app.
  - `error`: the gateway failed for this chunk (no target).
- `kind`: `prose | step | signal | table | label`. A signal word (DANGER, WARNING, CAUTION, NOTE, IMPORTANT) in the source forces `signal`.
- `issues`: strings `"RULE_ID: message"` from `guard.qa_check` (plus `MASK001`, `GW001`, `SAFETY`).
- `risk`: 0 to 1, from kind, issue counts and status.
- `decision`, `_pv`: added by the pipeline for the app. `_pv` is the segment version in `job.sqlite`. On every export, an entry whose `_pv` still matches is kept exactly as the app left it (decisions, human edits); a re-run bumps the version, so that entry is replaced and reset to `decision: pending`.
- Chunks not yet processed (waiting at a gate, budget stop) are absent; `status` reports them as `pending`.

## Graph and gates

`load -> classify -> pick -> mask -> prompt -> translate -> unmask -> qa -> (fix -> unmask -> qa) x2 -> record -> gate -> pick ... -> finalize`.

- Fix attempts use alias `strong` only when the profile allows paid calls (or `allow_strong` is set); otherwise they re-prompt `bulk` with the QA `fix_hint`.
- The gate interrupts after each safety segment (always) and after every `batch_size` chunks. `--approve-batches` auto-approves batch gates; safety gates need `--approve-safety`, which stands for an explicit human act. Exit codes: 0 done, 3 waiting at a gate, 4 budget cap reached.
- Resume: run the same command again (same job dir). The ledger key is `(job, chunk, g<generation>:<stage>)`; a billed key is reused, never sent again. Known gap: a call charged by the provider but killed before the ledger write cannot be seen (see `spikes/sp05_resume/REPORT.md`).
- Budget: `--budget USD` sets the job cap in the gateway; `BudgetExceeded` stops the run with the checkpoint at the failing node, so a run with a higher cap continues from there.
- Re-run: `rerun --pages 3,4` or `rerun --failed` bumps the generation of the selected chunks (new ledger keys, new prompt version `pipe-v1.rN`, so no cache hit) and runs only them in thread `<job>#rN`.

## Trace record (`trace.jsonl`)

`ts, job_id, chunk_id, node, node_latency_ms` plus, for gateway nodes, `stage, tokens_in, tokens_out, cost_usd, latency_ms, model, prompt_version, cache_hit, ledger (billed|reused), attempt, input_hash, output_hash, input_len, output_len`. Only allow-listed keys are ever written. The Langfuse exporter (`--langfuse` or `MANUVI_LANGFUSE=1`, off by default) receives the same sanitized records: hashes and lengths, never text. It has not been run against a live Langfuse server.

## CLI

```
python -m src.pipeline run --pdf-text-fixture chunks.json --profile dev --job-dir workspace/job
python -m src.pipeline approve --job-dir workspace/job --safety   # resume after a gate
python -m src.pipeline status --job-dir workspace/job
python -m src.pipeline rerun --job-dir workspace/job --failed
python -m src.pipeline demo                                       # synthetic chunks, fake adapter, $0
```
