#!/usr/bin/env bash
# Deploy infra/manuvi-hosting.yaml (needs the foundation stack). Never prints credentials.
# Required env: AWS_PROFILE, TRUSTED_PRINCIPAL_ARN, SOURCE_TARBALL_URL
# Optional env: DOMAIN_NAME, INSTANCE_TYPE (t4g.small), ARCHITECTURE (arm64),
#   VPC_ID, SUBNET_ID (default VPC / first default subnet), FOUNDATION_STACK (manuvi-foundation)
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/aws_common.sh
source "$here/aws_common.sh"

: "${TRUSTED_PRINCIPAL_ARN:?Set TRUSTED_PRINCIPAL_ARN}"
: "${SOURCE_TARBALL_URL:?Set SOURCE_TARBALL_URL (public GitHub .tar.gz, ideally a tag)}"

STACK_NAME="${HOSTING_STACK_NAME:-manuvi-hosting}"
foundation="${FOUNDATION_STACK:-manuvi-foundation}"
template="$here/../infra/manuvi-hosting.yaml"

require_profile_and_confirm

bucket="$(aws cloudformation describe-stacks --stack-name "$foundation" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text)"
[[ -n "$bucket" && "$bucket" != "None" ]] || { echo "Foundation stack ${foundation} not found." >&2; exit 1; }

vpc="${VPC_ID:-$(aws ec2 describe-vpcs --filters Name=is-default,Values=true \
  --query 'Vpcs[0].VpcId' --output text)}"
[[ -n "$vpc" && "$vpc" != "None" ]] || { echo "No default VPC. Set VPC_ID and SUBNET_ID." >&2; exit 1; }
subnet="${SUBNET_ID:-$(aws ec2 describe-subnets --filters "Name=vpc-id,Values=${vpc}" Name=default-for-az,Values=true \
  --query 'Subnets[0].SubnetId' --output text)}"
[[ -n "$subnet" && "$subnet" != "None" ]] || { echo "No default subnet. Set SUBNET_ID." >&2; exit 1; }

aws cloudformation validate-template --template-body "file://$template" >/dev/null \
  || { echo "Template validation failed." >&2; exit 1; }

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$template" \
  --capabilities CAPABILITY_NAMED_IAM \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    "ArtifactBucketName=${bucket}" \
    "TrustedPrincipalArn=${TRUSTED_PRINCIPAL_ARN}" \
    "VpcId=${vpc}" \
    "SubnetId=${subnet}" \
    "InstanceType=${INSTANCE_TYPE:-t4g.small}" \
    "Architecture=${ARCHITECTURE:-arm64}" \
    "SourceTarballUrl=${SOURCE_TARBALL_URL}" \
    "DomainName=${DOMAIN_NAME:-}"

aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table
echo "Next: set the web password hash in SSM (docs/HOSTING.md step 3)."
