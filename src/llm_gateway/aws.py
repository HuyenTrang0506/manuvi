"""boto3 client factory. No keys in code: credentials come from the named profile / default chain."""
from __future__ import annotations

import os
from typing import Any, Mapping, Optional


def make_session(env: Optional[Mapping[str, str]] = None):
    import boto3  # lazy: offline dev never imports it

    env = os.environ if env is None else env
    return boto3.Session(profile_name=env.get("MANUVI_AWS_PROFILE") or None,
                         region_name=env.get("MANUVI_AWS_REGION") or None)


def make_client(service: str, env: Optional[Mapping[str, str]] = None, read_timeout: float = 60,
                connect_timeout: float = 10) -> Any:
    """Client with fixed timeouts and botocore retries OFF (the gateway owns retries)."""
    from botocore.config import Config

    cfg = Config(connect_timeout=connect_timeout, read_timeout=read_timeout,
                 retries={"max_attempts": 0, "mode": "standard"})
    return make_session(env).client(service, config=cfg)
