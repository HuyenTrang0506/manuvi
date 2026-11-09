#!/usr/bin/env bash
# Stop or start the Manuvi web instance to save money (EBS and the Elastic IP are kept).
# Usage: AWS_PROFILE=<profile> bash scripts/aws_stop_start.sh stop|start|status
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=scripts/aws_common.sh
source "$here/aws_common.sh"

action="${1:-}"
case "$action" in stop | start | status) ;; *) echo "usage: $0 stop|start|status" >&2; exit 2 ;; esac

STACK_NAME="${HOSTING_STACK_NAME:-manuvi-hosting}"
require_profile_and_confirm

iid="$(aws cloudformation describe-stacks --stack-name "$STACK_NAME" \
  --query "Stacks[0].Outputs[?OutputKey=='WebInstanceId'].OutputValue" --output text)"
[[ -n "$iid" && "$iid" != "None" ]] || { echo "Instance id not found in stack ${STACK_NAME}." >&2; exit 1; }

state() { aws ec2 describe-instances --instance-ids "$iid" --query 'Reservations[0].Instances[0].State.Name' --output text; }

case "$action" in
  stop)
    aws ec2 stop-instances --instance-ids "$iid" >/dev/null
    aws ec2 wait instance-stopped --instance-ids "$iid"
    echo "Stopped ${iid}. Compute billing pauses; storage and the Elastic IP still bill (verify on the AWS pricing page)."
    ;;
  start)
    aws ec2 start-instances --instance-ids "$iid" >/dev/null
    aws ec2 wait instance-running --instance-ids "$iid"
    echo "Running ${iid}. The web stack restarts by itself; allow a minute or two."
    ;;
  status) echo "${iid}: $(state)" ;;
esac
