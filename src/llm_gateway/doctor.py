"""`manuvi aws doctor`: read-only checks of the AWS wiring. Never prints keys, tokens or full account ids."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from .config import ALIASES, SsmConfigSource, resolve_config, ssm_path
from .errors import GatewayError

OK, WARN, FAIL, SKIP = "ok", "warn", "FAIL", "skip"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""


def _code(e: Exception) -> str:
    """Error class or AWS error code only: messages can carry ARNs or account ids."""
    resp = getattr(e, "response", None)
    if isinstance(resp, dict) and resp.get("Error", {}).get("Code"):
        return resp["Error"]["Code"]
    return type(e).__name__


def run_doctor(env: Optional[Mapping[str, str]] = None,
               client_factory: Optional[Callable[[str], Any]] = None) -> list[Check]:
    env = dict(os.environ if env is None else env)
    profile = env.get("MANUVI_PROFILE") or "dev"
    region = env.get("MANUVI_AWS_REGION") or ""
    checks: list[Check] = []

    if client_factory is None:
        def client_factory(service: str):
            from .aws import make_client
            return make_client(service, env, read_timeout=10)

    # 1. credentials
    if not region:
        checks.append(Check("region", FAIL, "MANUVI_AWS_REGION is not set",
                            "export MANUVI_AWS_REGION=<region> (e.g. the region where the manuvi stack is deployed)"))
    else:
        checks.append(Check("region", OK, region))
    try:
        acct = client_factory("sts").get_caller_identity()["Account"]
        checks.append(Check("credentials", OK, f"account ending {acct[-4:]}, region {region or 'unset'}"))
    except Exception as e:
        checks.append(Check("credentials", FAIL, _code(e),
                            "run `aws sso login --profile <name>` (or configure the profile) and set MANUVI_AWS_PROFILE"))
        return checks + [Check("ssm", SKIP, "needs credentials"), Check("allow_paid", SKIP, "needs credentials"),
                         Check("s3 bucket", SKIP, "needs credentials")]

    # 2. SSM readable
    ssm = SsmConfigSource(client=client_factory("ssm"), env=env)
    prefix = f"/manuvi/{profile}/"
    values = ssm.load(prefix)
    if not ssm.reachable:
        checks.append(Check("ssm", FAIL, ssm.last_error or "unreadable",
                            f"allow ssm:GetParametersByPath on {prefix}* for this identity and check the region"))
    else:
        want = [ssm_path(profile, "model", a) for a in ALIASES] + [
            ssm_path(profile, "allow_paid"), ssm_path(profile, "cap", "job_usd"), ssm_path(profile, "cap", "month_usd")]
        missing = [w.removeprefix(prefix) for w in want if w not in values]
        if missing:
            checks.append(Check("ssm", WARN, f"readable; missing: {', '.join(missing)}",
                                "deploy the infra stack or create them; yaml defaults apply meanwhile"))
        else:
            checks.append(Check("ssm", OK, f"{len(values)} parameter(s) under {prefix}"))

    # 3. allow_paid state (effective, after the paid-safety rules)
    try:
        cfg = resolve_config(env, ssm)
        if cfg.allow_paid and cfg.caps_ok():
            checks.append(Check("allow_paid", WARN, f"PAID ENABLED: job cap ${cfg.cap_job_usd}, month cap ${cfg.cap_month_usd}"))
        elif cfg.allow_paid:
            checks.append(Check("allow_paid", FAIL, "allow_paid is true but job/month caps are not both > 0",
                                f"set {prefix}cap/job_usd and {prefix}cap/month_usd above 0, or set allow_paid to false"))
        else:
            checks.append(Check("allow_paid", OK, "false (free mode)"))
    except GatewayError as e:
        checks.append(Check("allow_paid", FAIL, str(e), "fix MANUVI_PROFILE or config/profiles.yaml"))

    # 4. bucket exists and is private
    bucket = env.get("MANUVI_S3_BUCKET")
    if not bucket:
        checks.append(Check("s3 bucket", SKIP, "MANUVI_S3_BUCKET not set (S3 sink off)"))
    else:
        s3 = client_factory("s3")
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception as e:
            checks.append(Check("s3 bucket", FAIL, f"{bucket}: {_code(e)}",
                                "create the bucket or fix MANUVI_S3_BUCKET; the identity needs s3:ListBucket"))
        else:
            try:
                pab = s3.get_public_access_block(Bucket=bucket)["PublicAccessBlockConfiguration"]
                if all(pab.get(k) for k in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
                    checks.append(Check("s3 bucket", OK, f"{bucket} exists, public access fully blocked"))
                else:
                    checks.append(Check("s3 bucket", FAIL, f"{bucket}: public access block is incomplete",
                                        "enable all four S3 Block Public Access settings on the bucket"))
            except Exception as e:
                if _code(e) in ("AccessDenied", "AccessDeniedException", "403"):
                    checks.append(Check("s3 bucket", WARN, f"{bucket} exists; public access block not readable (AccessDenied)",
                                        "allow s3:GetBucketPublicAccessBlock to verify it is private"))
                    return checks
                checks.append(Check("s3 bucket", FAIL, f"{bucket}: {_code(e)} (public access block unreadable)",
                                    "enable S3 Block Public Access on the bucket and allow s3:GetBucketPublicAccessBlock"))
    return checks


def render(checks: list[Check]) -> str:
    lines = []
    for c in checks:
        lines.append(f"[{c.status:>4}] {c.name}: {c.detail}".rstrip())
        if c.status == FAIL and c.fix:
            lines.append(f"       fix: {c.fix}")
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0
