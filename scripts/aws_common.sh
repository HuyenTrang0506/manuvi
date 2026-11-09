# shellcheck shell=bash
# Shared guard for aws_deploy.sh / aws_teardown.sh (sourced, not executed).
# Requires AWS_PROFILE, prints account and region, asks to type the account id.

STACK_NAME="${STACK_NAME:-manuvi-foundation}"

require_profile_and_confirm() {
  if [[ -z "${AWS_PROFILE:-}" ]]; then
    echo "AWS_PROFILE must be set explicitly (for example: AWS_PROFILE=manuvi-admin)." >&2
    exit 1
  fi
  command -v aws >/dev/null 2>&1 || { echo "aws CLI not found." >&2; exit 1; }
  ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
  REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region || true)}}"
  if [[ -z "$REGION" ]]; then
    echo "No region configured. Set AWS_REGION or run aws configure sso." >&2
    exit 1
  fi
  export AWS_REGION="$REGION"
  echo "Profile: ${AWS_PROFILE}"
  echo "Account: ${ACCOUNT_ID}"
  echo "Region:  ${REGION}"
  echo "Stack:   ${STACK_NAME}"
  read -r -p "Type the account id to confirm: " typed
  if [[ "$typed" != "$ACCOUNT_ID" ]]; then
    echo "Account id did not match. Nothing was changed." >&2
    exit 1
  fi
}
