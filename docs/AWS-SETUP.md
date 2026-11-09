# AWS setup (Manuvi foundation)

One CloudFormation stack (`infra/manuvi-foundation.yaml`) creates: a private S3 bucket for job artifacts and traces, free SSM Parameter Store config per profile (dev/qa/prod), a least-privilege role `manuvi-app`, a 30-day log group `/manuvi/app`, and a monthly budget with email alerts. Hard budget: USD 20/month; target far lower.

Important: AWS Budgets only sends alerts. The real hard stop is the app-level cap (`cap/job_usd`, `cap/month_usd`) enforced by the gateway (ADR-010). Paid models stay off while `allow_paid` is `false`.

## 1. Founder prerequisites (do these yourself, in the console)
1. Enable MFA on the root user, then stop using root. NEVER create access keys for root.
2. Check the Free Plan credits (Billing > Credits) and turn on billing alerts / Free Tier usage alerts in Billing preferences.
3. Create an IAM Identity Center user (preferred) or a limited IAM user with MFA. Run `aws configure sso` and note the profile name.
4. In Bedrock > Model access, enable the chosen model in the chosen region (for example Amazon Nova Lite). Note the model ARN (and the inference-profile ARN if the model requires one).
5. After deploying, confirm the budget email subscription sent to `AlertEmail`.
6. NEVER paste keys or tokens in chat, issues or commits. Credentials live only in your AWS profile.

## 2. Deploy
```bash
export AWS_PROFILE=<your-sso-profile>
export AWS_REGION=<region where Bedrock model access is enabled>
export ALERT_EMAIL=you@example.com
export TRUSTED_PRINCIPAL_ARN=arn:aws:iam::<account-id>:role/<your-sso-or-user-arn>
# optional: BEDROCK_MODEL_ARNS="arn:aws:bedrock:...,arn:aws:bedrock:..."  MONTHLY_BUDGET_USD=20
bash scripts/aws_deploy.sh
```
The script prints profile, account id and region, and asks you to type the account id before it changes anything. It runs `aws cloudformation deploy ... --capabilities CAPABILITY_NAMED_IAM`.
Re-run it any time to update `BedrockModelArns`; leaving it empty grants no Bedrock access.

## 3. Switch profile or model
Config is read from `/manuvi/<profile>/...`. Examples:
```bash
aws ssm put-parameter --name /manuvi/qa/model/bulk --type String --overwrite \
  --value bedrock:amazon.nova-lite-v1:0
# Paid models run only after you opt in on purpose:
aws ssm put-parameter --name /manuvi/qa/allow_paid --type String --overwrite --value true
aws ssm put-parameter --name /manuvi/qa/cap/job_usd --type String --overwrite --value 1
```
Defaults: dev uses `ollama:qwen2.5:14b` with caps 0; qa caps 1 / 3 USD; prod caps 5 / 15 USD (job / month); `allow_paid` is `false` everywhere; strong and vision are `none` for qa/prod. A model must also be listed in `BedrockModelArns`, or IAM denies the call.
Note: re-deploying the stack resets these parameters to the template defaults.

## 4. Verify
```bash
aws sts get-caller-identity
aws ssm get-parameters-by-path --path /manuvi/dev --recursive --query 'Parameters[].[Name,Value]' --output table
aws s3api get-public-access-block --bucket <bucket-output>
aws logs describe-log-groups --log-group-name-prefix /manuvi/app
aws budgets describe-budgets --account-id <account-id>
```
Offline checks (no AWS needed): `cfn-lint infra/manuvi-foundation.yaml` and `pytest tests/infra`.

## 5. Tear down
```bash
AWS_PROFILE=<profile> bash scripts/aws_teardown.sh
```
Asks for the account id and the word `DELETE`, empties the bucket (all versions), deletes the stack, then deletes the bucket. Download anything you need first.

## 6. Cost expectations
Prices change; verify every figure on the AWS pricing page before relying on it.

| Item | Expectation |
|---|---|
| SSM Parameter Store, Standard tier | USD 0 (Standard parameters have no charge) |
| IAM, CloudFormation (this stack), AWS Budgets (first budgets) | Normally USD 0; verify on the AWS pricing page |
| S3 storage and requests | Small; lifecycle expires old versions (30 d) and `traces/` (90 d). Verify on the AWS pricing page |
| CloudWatch Logs | Ingestion and storage per GB, 30-day retention. Verify on the AWS pricing page |
| Bedrock model calls | Per input/output token, model and region dependent. Verify on the AWS pricing page |

## 7. Security checklist
- [ ] Root has MFA and no access keys; daily work uses SSO or a limited user.
- [ ] No keys in the repo, chat, `.env` or logs.
- [ ] `TrustedPrincipalArn` is a single specific principal; consider `RequireMfa=true` for IAM users.
- [ ] `BedrockModelArns` lists only the models you need.
- [ ] `allow_paid` is `false` until you decide otherwise; caps are set.
- [ ] Budget email confirmed; billing alerts on.
- [ ] Bucket stays private (public access block on, TLS only); do not upload customer files unless intended.
- [ ] Review the stack periodically and tear down unused environments.
