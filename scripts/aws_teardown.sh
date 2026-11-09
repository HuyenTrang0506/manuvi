#!/usr/bin/env bash
# Delete the Manuvi stack, then empty and delete the retained bucket.
# Order matters: the stack goes first (the bucket is Retain, so it survives); the bucket is
# touched only after the stack is confirmed gone. Requires typing the account id and DELETE.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/aws_common.sh
source "$here/aws_common.sh"

require_profile_and_confirm

bucket="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='ArtifactBucketName'].OutputValue" --output text 2>/dev/null || true)"
[[ "$bucket" == "None" ]] && bucket=""

echo "This deletes stack ${STACK_NAME} (role, policy, log group, parameters, budget)."
[[ -n "$bucket" ]] && echo "Then bucket ${bucket} will be EMPTIED and DELETED, including all versions."
read -r -p "Type DELETE to continue: " word
[[ "$word" == "DELETE" ]] || { echo "Aborted. Nothing was changed." >&2; exit 1; }

aws cloudformation delete-stack --stack-name "$STACK_NAME"
if ! aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME"; then
  echo "Stack deletion failed. Bucket and data were left untouched." >&2
  exit 1
fi

if [[ -n "$bucket" ]]; then
  echo "Emptying ${bucket} ..."
  aws s3 rm "s3://${bucket}" --recursive --only-show-errors
  for kind in Versions DeleteMarkers; do
    while :; do
      # JMESPath backticks are not shell expansions; the payload is JSON for delete-objects.
      # shellcheck disable=SC2016
      payload="$(aws s3api list-object-versions --bucket "$bucket" --max-items 1000 \
        --query "{Objects: ${kind}[].{Key:Key,VersionId:VersionId}, Quiet: \`true\`}" --output json)"
      [[ "$payload" == *'"Objects": null'* || "$payload" == *'"Objects": []'* ]] && break
      aws s3api delete-objects --bucket "$bucket" --delete "$payload" >/dev/null
    done
  done
  aws s3api delete-bucket --bucket "$bucket"
fi
echo "Teardown complete."
