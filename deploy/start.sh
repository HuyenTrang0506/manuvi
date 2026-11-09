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
