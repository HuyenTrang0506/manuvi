# Agent context (read this first, it is short)

Manuvi translates technical manuals EN -> VI and keeps layout, codes and figures intact. Product docs live in `product/` on the founder's machine and are not in this repo. Everything you need is in your task spec.

## Rules (all tasks)
1. Work on your own branch `feat/<task-id>-<slug>`. Never push to `main`. One task = one small PR.
2. Python 3.13, pydantic v2, pytest. Tests run offline and cost $0 (`dev` profile: fakes, recorded fixtures, Ollama optional).
3. Never commit customer files (Kobelco PDFs, outputs, traces with customer text), keys, or `.env`.
4. Protected codes (part numbers, wire/connector codes like `YT12...`, pin numbers, values + units, Fig./page refs) must never change. Add a test for any code path that touches text.
5. Never call a paid model. Paid calls only go through the gateway with caps (ADR-010).
6. Uncertain -> flag it (`needs_review`), never guess.
7. Commits: author is the founder (repo git config is already set). Do not add `Co-authored-by` or tool attribution lines. Message style: `feat(scope): summary` in English.
8. Docs and code comments in English. Keep PR descriptions short: what, how tested, risks.
9. Token discipline: read only the files you need (use grep and line ranges), do not re-read files, no exploratory refactors, stop when the acceptance criteria pass.

## Definition of done (short)
Acceptance criteria covered by tests, all tests pass offline, protected-code tests pass, no customer data in the diff, PR opened with the checklist in `.github/pull_request_template.md`.
