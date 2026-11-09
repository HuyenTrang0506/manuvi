#!/usr/bin/env bash
# Delete the Manuvi stack. The bucket is retained by the stack, so this script offers to
# empty (all versions) and delete it. Requires typing the account id and the word DELETE.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/aws_common.sh
source "$here/aws_common.sh"

require_profile_and_confirm

bucket="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text 2>/dev/null || true)"

echo "This deletes stack ${STACK_NAME} (role, policy, log group, parameters, budget)."
[[ -n "$bucket" && "$bucket" != "None" ]] && echo "Bucket ${bucket} will be EMPTIED and DELETED, including all versions."
read -r -p "Type DELETE to continue: " word
[[ "$word" == "DELETE" ]] || { echo "Aborted. Nothing was changed." >&2; exit 1; }

if [[ -n "$bucket" && "$bucket" != "None" ]]; then
  echo "Emptying ${bucket} ..."
  # JMESPath literals use backticks; they are not shell expansions.
  # shellcheck disable=SC2016
  query='[Versions,DeleteMarkers][][].[Key,VersionId]'
  while :; do
    pairs="$(aws s3api list-object-versions --bucket "$bucket" --max-items 500 \
      --query "$query" --output text | grep -v '^None' || true)"
    [[ -z "$pairs" ]] && break
    while read -r key vid; do
      aws s3api delete-object --bucket "$bucket" --key "$key" --version-id "$vid" >/dev/null
    done <<<"$pairs"
  done
fi

aws cloudformation delete-stack --stack-name "$STACK_NAME"
aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"
[[ -n "$bucket" && "$bucket" != "None" ]] && aws s3api delete-bucket --bucket "$bucket"
echo "Teardown complete."
