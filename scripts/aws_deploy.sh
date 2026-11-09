#!/usr/bin/env bash
# Deploy infra/manuvi-foundation.yaml. Never prints credentials.
# Required env: AWS_PROFILE, ALERT_EMAIL, TRUSTED_PRINCIPAL_ARN
# Optional env: MONTHLY_BUDGET_USD (20), ARTIFACT_BUCKET_NAME, BEDROCK_MODEL_ARNS (comma list), REQUIRE_MFA (false)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/aws_common.sh
source "$here/aws_common.sh"

: "${ALERT_EMAIL:?Set ALERT_EMAIL}"
: "${TRUSTED_PRINCIPAL_ARN:?Set TRUSTED_PRINCIPAL_ARN}"

template="$here/../infra/manuvi-foundation.yaml"
aws cloudformation validate-template --template-body "file://$template" >/dev/null 2>&1 \
  || { echo "Template validation failed (or no access)." >&2; exit 1; }

require_profile_and_confirm

overrides=(
  "AlertEmail=${ALERT_EMAIL}"
  "TrustedPrincipalArn=${TRUSTED_PRINCIPAL_ARN}"
  "MonthlyBudgetUsd=${MONTHLY_BUDGET_USD:-20}"
  "RequireMfa=${REQUIRE_MFA:-false}"
)
[[ -n "${ARTIFACT_BUCKET_NAME:-}" ]] && overrides+=("ArtifactBucketName=${ARTIFACT_BUCKET_NAME}")
[[ -n "${BEDROCK_MODEL_ARNS:-}" ]] && overrides+=("BedrockModelArns=${BEDROCK_MODEL_ARNS}")

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$template" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides "${overrides[@]}"

aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
