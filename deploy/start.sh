#!/bin/bash
# up | down. Picks the Caddyfile and optional tunnel profile, then runs docker compose.
# The tunnel token (if any) is read from SSM SecureString /manuvi/prod/tunnel/token into the
# process environment only; it is never written to disk.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
set -a
# shellcheck disable=SC1091
source /opt/manuvi/.env
set +a

if [[ -n "${MANUVI_DOMAIN:-}" ]]; then export CADDYFILE=Caddyfile; else export CADDYFILE=Caddyfile.internal; fi

# Host allow-list of the app (it rejects any other Host header). Domain if set, otherwise the instance's
# public IP / DNS name from IMDSv2 (the Elastic IP shows up a few seconds after boot, so retry).
if [[ -z "${MANUVI_ALLOWED_HOSTS:-}" ]]; then
  if [[ -n "${MANUVI_DOMAIN:-}" ]]; then
    MANUVI_ALLOWED_HOSTS="$MANUVI_DOMAIN"
  else
    MANUVI_ALLOWED_HOSTS=""
    for _ in $(seq 1 30); do
      imds_token="$(curl -fsS -m 3 -X PUT http://169.254.169.254/latest/api/token \
        -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' || true)"
      ip="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $imds_token" \
        http://169.254.169.254/latest/meta-data/public-ipv4 || true)"
      dns="$(curl -fsS -m 3 -H "X-aws-ec2-metadata-token: $imds_token" \
        http://169.254.169.254/latest/meta-data/public-hostname || true)"
      if [[ -n "$ip" ]]; then MANUVI_ALLOWED_HOSTS="$ip${dns:+,$dns}"; break; fi
      sleep 2
    done
  fi
  export MANUVI_ALLOWED_HOSTS
fi
profile_args=()
if [[ "${MANUVI_TUNNEL:-0}" == "1" ]]; then
  TUNNEL_TOKEN="$(aws ssm get-parameter --region "$MANUVI_AWS_REGION" --name /manuvi/prod/tunnel/token \
    --with-decryption --query Parameter.Value --output text)"
  export TUNNEL_TOKEN
  profile_args=(--profile tunnel)
fi

case "${1:-up}" in
  up) docker compose "${profile_args[@]}" up -d --build ;;
  down) docker compose "${profile_args[@]}" down ;;
  *) echo "usage: start.sh up|down" >&2; exit 2 ;;
esac
