# SP-05: LangGraph kill-and-resume with a SQLite checkpointer

## Conclusion
LangGraph with `SqliteSaver` resumes correctly after SIGKILL. A restart with the same `thread_id` continues from the last checkpoint in every trial (20 random-time kills plus one scripted kill). Checkpoint writes average 0.9 ms (max 2.4 ms).

LangGraph alone does **not** guarantee zero re-billed calls. A node in flight at kill time re-runs from its start, so a call the provider already charged can be sent again. In a scripted stall after billing, an unguarded run billed chunk 20 twice. In 20 random-time kills no duplicate occurred, because the window is only milliseconds.

**Recommendation:** keep LangGraph for resume, and add a step ledger as an idempotency guard on every paid call. Before the call, the node looks for a `billed` row (thread, chunk, stage) and reuses the stored output if found. With the guard on, the scripted kill billed every chunk once.

## Measurements
| Case | Result |
|---|---|
| Scripted kill at chunk 20, guard on | Resume starts at index 20; 0 chunks replayed; 0 re-billed; 1 in-flight call served from the ledger |
| Scripted control, guard off | Chunk 20 translate billed 2x |
| Random SIGKILL, 10 trials each | Guard on: 0/10 duplicates. Guard off: 0/10 duplicates. Every run ended with 100 billed calls, 50 translations, 50 QA results |
| Clean full run | 106 checkpoints; 0.89 ms average and 2.39 ms max per write; about 300 KB checkpoint payload; 576 KiB DB file |

State payload grows roughly quadratically because translated text is kept in graph state. Store large outputs in the ledger and keep only references in state.

## Remaining gap
A call that the provider charged but whose response was never recorded (kill between charge and ledger write) cannot be seen by the guard. If the provider supports idempotency keys, pass the ledger key. Not verified for our providers.

## Caveats
- Fake provider: latency and billing behaviour are assumptions.
- Gates are auto-approved with `--approve`. Without it the run exits (code 3) and a later run resumes after approval, which the test covers. A real flow calls the same `Command(resume=...)`.
- One process per thread; concurrent resumes were not tested.
- Dependencies are not added to `pyproject.toml`. Tested with langgraph 1.2.20, langgraph-checkpoint-sqlite 3.1.3, Python 3.13.

## How to run
```
python3 -m pytest tests/spikes/test_sp05_resume.py -q
python3 spikes/sp05_resume/run.py --workdir <dir> --approve --latency 0.02
```
