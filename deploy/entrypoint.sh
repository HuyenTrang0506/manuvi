#!/bin/sh
# Container entrypoint. Reads the web users (password hashes) from SSM SecureString at start,
# so no secret is baked into the image, UserData, compose file or host disk.
set -eu
if [ -n "${MANUVI_USERS_PARAM:-}" ]; then
  MANUVI_WEB_USERS="$(python - <<'PY'
import os, sys
import boto3
ssm = boto3.client("ssm", region_name=os.environ.get("MANUVI_AWS_REGION"))
try:
    r = ssm.get_parameter(Name=os.environ["MANUVI_USERS_PARAM"], WithDecryption=True)
except Exception as exc:  # no secret in the message
    print(f"cannot read {os.environ['MANUVI_USERS_PARAM']}: {type(exc).__name__}", file=sys.stderr)
    sys.exit(1)
print(r["Parameter"]["Value"], end="")
PY
)"
  export MANUVI_WEB_USERS
fi
exec "$@"
