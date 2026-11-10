# Manuvi improvement plan (PM/BA)

**Date:** 2026-11-10 · **Owner:** product (PM/BA role) · **Review:** end of every sprint

## 1. Outcome we are working towards

The workshop owner opens a web address, uploads the Kobelco SK70SR-1ES manual, and receives a Vietnamese
version of **Section 43 (electrical)** that a mechanic can work from without checking the English page.

"Usable" is decided by the workshop, not by us. Until a mechanic has reviewed pages, every quality number
below is an internal proxy.

## 2. Where we are (facts only)

| Area | State | Evidence |
|---|---|---|
| Pipeline, guard, review app, renderer | Built, 675 automated tests | `main` |
| Hosted portal, queue, worker, AWS templates | Built, never deployed | PR #14, #16 |
| Quality on a real model | One run, 7B local model, 55 synthetic cases | `docs/EVAL.md` |
| Quality on the real manual | **Not measured** | - |
| Terminology | 66 electrical terms + 5 signal words, all *proposed* | `src/eval/data/`, `config/signal_words.yaml` |
| Client feedback | **None** | - |

First real run: hard metrics 97.3% / 95.5% / 83.3%. Both hard failures were safety sentences (p0).
The guard now blocks those two failure types, but that fix has only been tested on recorded outputs.

## 3. Main risks, in order

1. **We do not know real-manual quality.** Synthetic sentences are cleaner than scanned wiring pages.
2. **Terms are not approved.** Wrong terms repeated on every page are the most visible defect to a mechanic.
3. **The local model is too weak for safety text.** 7B models are for plumbing tests only.
4. **Nothing is deployed.** First deploys always find problems; the AWS account is a 6-month free plan with
   restricted services and an organisation policy that blocks Bedrock.
5. **No client review loop.** Without it, effort goes to the wrong defects.

## 4. Workstreams

### A. Choose the model by measurement (cost $0, on the founder's Mac)

- Hardware limit: Apple M1, about 11.8 GB usable for the model. 12B-class models at 4-bit fit tightly; 14B is at the limit.
- Candidates to run through `manuvi eval`, same set, same guard: current `qwen2.5:7b` (baseline), a 12B
  translation-specialised model (TranslateGemma 12B is reported to beat the general 27B Gemma 3 on WMT24++
  MetricX), a 12B general model (Gemma 3 was the best open family in a 2026 46-language study that includes
  Vietnamese), and one 14B Qwen-class model.
- **Unverified, check before pulling:** exact Ollama tags, licences, and whether TranslateGemma's 55 languages
  include Vietnamese.
- Decision rule: hard metrics 100% on accepted output; routed-to-review <= 5% of segments; glossary hit >= 97%;
  speed acceptable for 30 pages overnight. If no local model meets it, go to workstream E.
- Extend the eval: add 20 cases copied in structure (not text) from real Section 43 page types: wiring-diagram
  legends, connector tables, fuse lists.

### B. Section 43 pilot on the real manual

- Pick 8 to 10 pages covering each page type (prose procedure, table, wiring diagram, legend).
- Run intake -> pipeline -> render on the Mac (the manual never leaves the machine).
- Internal check first: page QA report, overflow, untranslated labels.
- Deliverable: one interleaved EN/VI PDF for the workshop.

### C. Terminology approval (largest quality lever)

- Send the 66-term Excel and the 5 signal words to the workshop owner. Ask for: accept / change / comment.
- Import decisions, re-run the eval, lock approved terms as `must_use_in_prompt` where needed.
- Rule: no bulk translation of Section 43 before the electrical terms are approved.

### D. First deployment

- Order: `manuvi aws doctor` (which services the plan allows) -> foundation stack -> hosting stack ->
  password in SSM -> worker on the Mac -> one public-domain test job end to end.
- Founder actions are limited to: approving stack creation, setting the password, choosing domain or tunnel.
- Verify on first deploy: S3 conditional writes, Caddy on-demand TLS check, S3 ListBucket fix.
- Free-plan clock: the plan ends after 6 months or when credits run out. Record the sign-up date and set a
  budget alert at 50%.

### E. Strong model for safety text (only if A fails)

| Option | Data leaves the Mac | Cost | Blocker |
|---|---|---|---|
| Larger local model on a better machine | No | Hardware | None on the software side |
| Paid API with a no-training contract | Yes | Per page | Founder must approve sending manual pages out |
| GPU instance on AWS | Stays in the account | Hourly | Probably not available on the free plan |

No decision is needed until workstream A has numbers.

### F. Client review loop

- One-page review form per pilot page: usable as is / usable with fixes / not usable, plus free comments.
- Every comment becomes an eval case or a glossary change, so the same defect cannot return unnoticed.
- Acceptance for a section: >= 90% of pages "usable as is" or "with fixes", zero safety or number errors.

## 5. Sprints

| Sprint | Goal | Exit criteria |
|---|---|---|
| 3 | Know the truth | Model comparison table for 3 to 4 local models; 8 to 10 real Section 43 pages rendered; term sheet sent |
| 4 | Usable online | Portal reachable at a URL with login; one job end to end through the queue; approved terms imported |
| 5 | First client review | Pilot PDF reviewed by the workshop; defects triaged; decision on the strong-model option |
| 6 | Section 43 complete | Full section translated, reviewed, accepted by the rule in F |

## 6. Working rules

- **Roles and models:** PM/BA, planning, research and final review use the top model sparingly. Implementation
  and reviews use the mid-tier model. Mechanical work (renames, doc formatting) uses the cheapest model.
- **Definition of done:** tests pass on a fresh clone; an independent review has run; the eval gate did not drop;
  user-visible change has a screenshot.
- **Founder involvement:** only for approvals, credentials, money, and anything shown to the client.
- **Reporting to the founder:** outcomes and decisions in plain language; no implementation detail.

## 7. Open questions (not blocking Sprint 3)

1. Who at the workshop reviews, and how much time per week do they have?
2. Is a paid external AI service acceptable for the manual's pages if local models fall short?
3. Domain name or a tunnel address for the portal?

## Sources

- TranslateGemma announcement: https://blog.google/innovation-and-ai/technology/developers-tools/translategemma/
- Scaling Model and Data for Multilingual MT with Open LLMs (2026): https://arxiv.org/abs/2602.11961
- AWS Free Tier update (free plan, credits, 6 months): https://aws.amazon.com/blogs/aws/aws-free-tier-update-new-customers-can-get-started-and-explore-aws-with-up-to-200-in-credits
