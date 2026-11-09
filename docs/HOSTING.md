# Hosting the Manuvi web app (one small instance + queue)

Design: one small EC2 instance runs the web app behind Caddy. Jobs go through an SQS queue; model workers pull from it (first the founder's Mac with Ollama). Files live in the existing S3 bucket. The account is locked to ap-southeast-2 (Sydney) and Bedrock is blocked, so nothing here needs Bedrock. See `docs/DEPLOYMENT.md` for the overall plan.

Contract with the app (owned by the `src/` agent): the container runs `uvicorn --factory $MANUVI_APP_MODULE` (default `src.webapp.hosted:create_app`) on port 8000, serves `GET /healthz`, and reads `MANUVI_WEB_USERS` (password hashes, injected at start from SSM), `MANUVI_S3_BUCKET`, `MANUVI_QUEUE_URL`, `MANUVI_AWS_REGION`, `MANUVI_DATA_DIR`. Until that module exists the container will not start.

## 1. Prerequisites
1. The foundation stack is deployed (`docs/AWS-SETUP.md`), same profile and region (ap-southeast-2).
2. Choose a pinned source URL, for example `https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.tar.gz` (the repo must be public, or use a public release asset).
3. Compute its SHA-256 from a copy you trust: `curl -fsSL "$SOURCE_TARBALL_URL" | sha256sum`. The instance verifies it at boot and stops before building anything when it does not match (parameter `SourceSha256`). A branch tarball changes with every commit, so use a tag. If a hash ever changes for the same tag, treat that as a reason to look, not to update the hash.

## 2. Create the stack
```bash
export AWS_PROFILE=<your-sso-profile>
export AWS_REGION=ap-southeast-2
export TRUSTED_PRINCIPAL_ARN=<same ARN as the foundation stack>
export SOURCE_TARBALL_URL=<tag tarball URL>
export SOURCE_SHA256=<64 lowercase hex characters from step 1.3>
# optional: DOMAIN_NAME=app.example.com  INSTANCE_TYPE=t3.small ARCHITECTURE=x86_64
bash scripts/aws_deploy_hosting.sh
```
It prints profile, account and region and asks you to type the account id. It looks up the bucket from the foundation stack and the default VPC/subnet, then deploys with `CAPABILITY_NAMED_IAM` (this is the IAM acknowledgement: the stack creates the roles `manuvi-web-instance` and `manuvi-worker`). Instance type and architecture must match: `t4g.*` with `arm64`, `t3.*` with `x86_64`. The instance has termination protection (`DisableApiTermination`, default `true`); see section 9 for deleting the stack.

## 3. Set the web password hash (before first login)
Generate a hash with the app's tool (see the app README), then:
```bash
aws ssm put-parameter --name /manuvi/prod/web/users --type SecureString --overwrite \
  --value '<user:hash lines as the app expects>'
```
The value is read by the container at start, never stored in UserData, the image or the repo. After changing it, restart the stack: `aws ssm start-session --target <WebInstanceId>` then `sudo systemctl restart manuvi`.

## 4. HTTPS: pick one
- **Option A, domain (recommended).** Buy or use a domain, create an `A` record to the `WebPublicIp` output, and redeploy with `DOMAIN_NAME=<domain>`. Caddy gets a Let's Encrypt certificate automatically (ports 80 and 443 must be reachable).
- **Option B, Cloudflare Tunnel (no domain on AWS, no inbound ports needed).** Create a tunnel in Cloudflare Zero Trust with a public hostname pointing to `http://caddy:8080`. Store its token: `aws ssm put-parameter --name /manuvi/prod/tunnel/token --type SecureString --value '<token>'`. Then in an SSM session add `MANUVI_TUNNEL=1` to `/opt/manuvi/.env` and `sudo systemctl restart manuvi`. With the tunnel on, the app believes `CF-Connecting-IP` (for the login rate limit and the audit log) only when the direct peer is inside the compose network (`172.29.0.0/24`, where cloudflared runs); from any other address the header is ignored and the peer address is used. Without the tunnel (`MANUVI_TRUST_CF=0`) the header is never read.
- **Default without either:** Caddy serves HTTPS with a self-signed certificate on the Elastic IP and redirects HTTP to HTTPS. Login over plain HTTP is not acceptable and is never served. The browser warning is expected; use this only for a first check, then switch to A or B.

## 5. Run the worker on your Mac
Add a profile that assumes the worker role through your SSO profile (no long-lived keys), in `~/.aws/config`:
```ini
[profile manuvi-worker]
role_arn = <WorkerRoleArn output>
source_profile = <your-sso-profile>
region = ap-southeast-2
```
```bash
AWS_PROFILE=manuvi-worker MANUVI_AWS_REGION=ap-southeast-2 \
MANUVI_QUEUE=sqs MANUVI_STORE=s3 \
MANUVI_S3_BUCKET=<bucket> MANUVI_QUEUE_URL=<JobQueueUrl output> manuvi worker
```
Ollama must be running locally. The worker only makes outbound connections.

## 6. Stop and start to save money
```bash
AWS_PROFILE=<profile> bash scripts/aws_stop_start.sh stop     # or start / status
```
The stack starts again on boot automatically. The Elastic IP and the domain stay the same.

## 7. Monthly cost (all figures: verify on the AWS pricing page, region ap-southeast-2)
| Item | Driver | Price |
|---|---|---|
| EC2 `t4g.small` or `t3.small` | hours running per month; zero compute while stopped | verify on the AWS pricing page |
| EBS gp3 root volume (default 20 GiB) | billed while the volume exists, also when stopped | verify on the AWS pricing page |
| Public IPv4 / Elastic IP | billed per hour for public IPv4 addresses | verify on the AWS pricing page |
| SQS standard queue and DLQ | requests; very low volume | verify on the AWS pricing page |
| S3 | storage and requests in the existing bucket | verify on the AWS pricing page |
| Data transfer out | download of results | verify on the AWS pricing page |
| Domain (optional) | yearly, bought by the founder | registrar price |
| Cloudflare Tunnel (optional) | no AWS cost | check Cloudflare |
Free Plan credits and any free trial for the instance type may apply; check Billing. The foundation budget (alerts only) stays on.

## 8. Where state lives (instance replacement)
The instance disk holds only caches. Everything that must survive replacing the instance is in the S3 bucket:

| State | Location | Notes |
|---|---|---|
| Segments, terms, review decisions | `jobs/<id>/segments.json`, `terms.json` | Written with S3 conditional writes (`If-Match`): the web app and the worker never overwrite each other. A stale write shows "Someone else changed this, reload." |
| Per-job audit (who approved or edited what) | `jobs/<id>/audit.jsonl` | Hash chain; two writers are merged and re-chained. |
| Portal audit (logins, uploads, downloads, access changes) | `portal/audit/<seq>.json` | One new object per event, created with `If-None-Match: *`, so nothing is overwritten. `prev_hash` is the SHA-256 of the previous object, so the chain runs across objects. Check it with `GET /api/events/verify` (founder). The instance role may read and write this prefix and has no delete right; the foundation bucket is versioned, so an overwrite by someone with other credentials still leaves the old version. |

Not persisted on purpose: login sessions (users sign in again after a replacement), and the rate-limit counters. No separate data volume is needed. Failed logins are logged as a keyed hash of the typed user name (never the raw field, which may be a mistyped password), its length, and whether it matched an account.

Roles: the reviewer sees nothing of a job until the founder ticks "Let the reviewer approve non-safety segments" (`reviewer_can_review`: opens the segments and the review page). Downloads are a separate grant (`reviewer_can_read`, default off), because the output files hold the whole manual.

Limits on untrusted PDFs (worker): `MANUVI_MAX_PAGES` (default 600) and `MANUVI_PREPARE_TIMEOUT` seconds (default 120). The PDF is parsed in a child process that is killed at the deadline; the job then fails once with `PDF_LIMIT`, `PDF_TIMEOUT` or `PDF_UNREADABLE`.

## 9. Security checklist
- [ ] No SSH anywhere: the security group opens only 80 and 443; shell access is SSM Session Manager.
- [ ] IMDSv2 required; root volume encrypted; queues use SSE-SQS and deny non-TLS access.
- [ ] Password hash only in SSM SecureString `/manuvi/prod/web/users`; no secrets in UserData, compose or the repo.
- [ ] Source tarball URL pinned to a tag or commit, and `SourceSha256` set (verified at boot).
- [ ] `DisableApiTermination` left at `true`.
- [ ] Caddy on-demand TLS (self-signed mode) only issues certificates the app approves (`/internal/tls-ask`, not reachable from outside); uvicorn believes `X-Forwarded-*` only from the Caddy container (`172.29.0.2`).
- [ ] HTTPS only (domain or tunnel); do not accept the self-signed certificate for real use.
- [ ] `TrustedPrincipalArn` is one specific principal; the worker uses `AWS_PROFILE` role assumption, no access keys.
- [ ] Instance role is limited to the queue, `jobs/`, `portal/audit/` and `/manuvi/*`; worker role to the queue, `jobs/` and `traces/`.
- [ ] Stop the instance when not in use; review the DLQ for failed jobs.

## 10. Teardown
The instance is protected against termination. Turn that off first (same environment variables as in section 2), then delete:
```bash
DISABLE_API_TERMINATION=false bash scripts/aws_deploy_hosting.sh
aws cloudformation delete-stack --stack-name manuvi-hosting
aws cloudformation wait stack-delete-complete --stack-name manuvi-hosting
```
This removes the instance, Elastic IP, queues (pending messages are lost) and both roles. The bucket belongs to the foundation stack and is untouched; remove that with `scripts/aws_teardown.sh`. Delete the SSM parameters `/manuvi/prod/web/users` and `/manuvi/prod/tunnel/token` yourself if you no longer need them.
