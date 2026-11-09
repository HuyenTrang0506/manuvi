# Hosting the Manuvi web app (one small instance + queue)

Design: one small EC2 instance runs the web app behind Caddy. Jobs go through an SQS queue; model workers pull from it (first the founder's Mac with Ollama). Files live in the existing S3 bucket. The account is locked to ap-southeast-2 (Sydney) and Bedrock is blocked, so nothing here needs Bedrock. See `docs/DEPLOYMENT.md` for the overall plan.

Contract with the app (owned by the `src/` agent): the container runs `uvicorn --factory $MANUVI_APP_MODULE` (default `src.webapp.hosted:create_app`) on port 8000, serves `GET /healthz`, and reads `MANUVI_WEB_USERS` (password hashes, injected at start from SSM), `MANUVI_S3_BUCKET`, `MANUVI_QUEUE_URL`, `MANUVI_AWS_REGION`, `MANUVI_DATA_DIR`. Until that module exists the container will not start.

## 1. Prerequisites
1. The foundation stack is deployed (`docs/AWS-SETUP.md`), same profile and region (ap-southeast-2).
2. Choose a pinned source URL, for example `https://github.com/<owner>/<repo>/archive/refs/tags/<tag>.tar.gz` (the repo must be public, or use a public release asset).

## 2. Create the stack
```bash
export AWS_PROFILE=<your-sso-profile>
export AWS_REGION=ap-southeast-2
export TRUSTED_PRINCIPAL_ARN=<same ARN as the foundation stack>
export SOURCE_TARBALL_URL=<tag tarball URL>
# optional: DOMAIN_NAME=app.example.com  INSTANCE_TYPE=t3.small ARCHITECTURE=x86_64
bash scripts/aws_deploy_hosting.sh
```
It prints profile, account and region and asks you to type the account id. It looks up the bucket from the foundation stack and the default VPC/subnet, then deploys with `CAPABILITY_NAMED_IAM` (this is the IAM acknowledgement: the stack creates the roles `manuvi-web-instance` and `manuvi-worker`). Instance type and architecture must match: `t4g.*` with `arm64`, `t3.*` with `x86_64`.

## 3. Set the web password hash (before first login)
Generate a hash with the app's tool (see the app README), then:
```bash
aws ssm put-parameter --name /manuvi/prod/web/users --type SecureString --overwrite \
  --value '<user:hash lines as the app expects>'
```
The value is read by the container at start, never stored in UserData, the image or the repo. After changing it, restart the stack: `aws ssm start-session --target <WebInstanceId>` then `sudo systemctl restart manuvi`.

## 4. HTTPS: pick one
- **Option A, domain (recommended).** Buy or use a domain, create an `A` record to the `WebPublicIp` output, and redeploy with `DOMAIN_NAME=<domain>`. Caddy gets a Let's Encrypt certificate automatically (ports 80 and 443 must be reachable).
- **Option B, Cloudflare Tunnel (no domain on AWS, no inbound ports needed).** Create a tunnel in Cloudflare Zero Trust with a public hostname pointing to `http://caddy:8080`. Store its token: `aws ssm put-parameter --name /manuvi/prod/tunnel/token --type SecureString --value '<token>'`. Then in an SSM session add `MANUVI_TUNNEL=1` to `/opt/manuvi/.env` and `sudo systemctl restart manuvi`.
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

## 8. Security checklist
- [ ] No SSH anywhere: the security group opens only 80 and 443; shell access is SSM Session Manager.
- [ ] IMDSv2 required; root volume encrypted; queues use SSE-SQS and deny non-TLS access.
- [ ] Password hash only in SSM SecureString `/manuvi/prod/web/users`; no secrets in UserData, compose or the repo.
- [ ] Source tarball URL pinned to a tag or commit.
- [ ] HTTPS only (domain or tunnel); do not accept the self-signed certificate for real use.
- [ ] `TrustedPrincipalArn` is one specific principal; the worker uses `AWS_PROFILE` role assumption, no access keys.
- [ ] Instance role is limited to the queue, `jobs/` and `/manuvi/*`; worker role to the queue, `jobs/` and `traces/`.
- [ ] Stop the instance when not in use; review the DLQ for failed jobs.

## 9. Teardown
```bash
aws cloudformation delete-stack --stack-name manuvi-hosting
aws cloudformation wait stack-delete-complete --stack-name manuvi-hosting
```
This removes the instance, Elastic IP, queues (pending messages are lost) and both roles. The bucket belongs to the foundation stack and is untouched; remove that with `scripts/aws_teardown.sh`. Delete the SSM parameters `/manuvi/prod/web/users` and `/manuvi/prod/tunnel/token` yourself if you no longer need them.
