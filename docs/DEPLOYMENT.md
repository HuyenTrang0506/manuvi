# Deployment plan: web app on AWS, model workers anywhere

**Conclusion:** host a small web app and a job queue on AWS, and run the translation model on a separate worker that pulls jobs. The worker can be the founder's Mac (free, Ollama) today, a CPU or Spot GPU instance on AWS later, and Bedrock only if the account ever allows it. Nothing in the app depends on Bedrock.

## Why not Bedrock first
The AWS account is an organization-managed Free Plan account. The organization has an explicit deny (SCP) on Bedrock actions, the account is locked to Sydney (ap-southeast-2), and the Bedrock serverless catalog is empty there. Credits do not override an SCP. So the design treats Bedrock as an optional adapter that is probed at startup (`manuvi aws doctor`), not a requirement.

## Architecture
```
Browser (founder / reviewer)  --HTTPS-->  Web app (FastAPI, login)  --> S3 (private, encrypted): uploads, job files, outputs
                                              |                         
                                              +--> Queue (SQS, or a file queue in dev) --> Worker(s)
Worker (Mac with Ollama, or EC2) --> gateway (profiles, cache, ledger, caps) --> model adapter: ollama | bedrock | free API (public docs only)
```
- **Web app:** the existing review app plus upload (PDF + section list), job status, download of the PDF/DOCX/Excel results. One user account to start (founder), a second read-only reviewer later. Login is mandatory because the content is copyrighted.
- **Queue:** SQS standard queue (free tier covers it). Messages carry only job ids. Dev uses a local directory queue with the same interface.
- **Storage:** one private S3 bucket (already in `infra/`). Job files live under `jobs/<id>/`, traces under `traces/` (masked).
- **Worker:** `manuvi worker` polls the queue, claims a job, runs the pipeline with the gateway, uploads results, writes status. Outbound connections only, so no tunnel or open port is needed on the founder's Mac.
- **Models, in order of preference per data class:**
  1. `customer` data (the Kobelco manual): local or private models only: Ollama on the founder's Mac, or a model on an EC2 instance in the same account.
  2. `public` documents (demos, public-domain manuals): free API tiers are allowed.
  3. Bedrock: only if `aws doctor` shows it works, and only when `allow_paid` is on.
- **Hosting the web app:** one small instance (EC2 or Lightsail) running the container behind Caddy with automatic HTTPS. A custom domain is optional: start on the instance's default DNS name or a free subdomain, and move to a bought domain later (about 12 USD a year, bought by the founder).

## Cost guard
Everything stays inside the existing caps: dev 0, qa 3, prod 15 USD a month. Fixed cost of the web instance is the main line item; stop the instance when not in use. Budgets alert at 50/80/100 percent.

## What the founder does herself
Anything that touches identity and security: confirm the account is hers, create the stack with the IAM acknowledgement, add a user for the reviewer, buy a domain. Claude prepares templates, scripts and checks, and verifies afterwards.

## Open points to verify in the real account
Which services the organization allows (EC2, Lightsail, SQS, S3 are expected to work; Bedrock does not). EC2 GPU quota for new accounts is usually 0, so GPU workers need a quota request or stay on the Mac. Prices must be checked on the AWS pricing pages.
