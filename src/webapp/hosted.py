"""ASGI factory for the hosted container:

    uvicorn --factory src.webapp.hosted:create_app --host 0.0.0.0 --port 8000

Environment: MANUVI_WEB_USERS (JSON users), MANUVI_ALLOWED_HOSTS (comma separated host[:port], e.g. the public
DNS name; localhost:8000 and 127.0.0.1:8000 are always accepted), MANUVI_TRUSTED_PROXIES (CIDRs allowed to set
X-Forwarded-*; default loopback), MANUVI_TRUST_CF (1: use CF-Connecting-IP, only when the peer is inside MANUVI_TRUSTED_PROXIES, i.e. the Cloudflare
Tunnel network), MANUVI_COOKIE_SECURE (1/0, default: follow a trusted X-Forwarded-Proto),
MANUVI_MAX_UPLOAD_MB, plus MANUVI_QUEUE*/MANUVI_STORE*/MANUVI_DATA_DIR (see src.jobs.config).
"""
from __future__ import annotations

import os
from typing import Mapping, Optional

from fastapi import FastAPI

from src.jobs import data_dir, make_queue, make_store

from .auth import Auth, load_users
from .portal import MAX_UPLOAD_DEFAULT_MB, create_portal_app


def create_app(env: Optional[Mapping[str, str]] = None, **overrides) -> FastAPI:
    env = os.environ if env is None else env
    hosts = {h.strip().lower() for h in env.get("MANUVI_ALLOWED_HOSTS", "").split(",") if h.strip()}
    port = env.get("PORT", "8000")
    hosts |= {f"localhost:{port}", f"127.0.0.1:{port}", "localhost", "127.0.0.1"}
    secure = env.get("MANUVI_COOKIE_SECURE")
    kw = dict(
        auth=Auth(load_users(env)),
        store=make_store(env),
        queue=make_queue(env),
        data_dir=data_dir(env),
        allowed_hosts=hosts,
        secure_cookies=None if secure is None else secure == "1",
        trusted_proxies=env.get("MANUVI_TRUSTED_PROXIES", "127.0.0.1/32,::1/128"),
        max_upload_mb=int(env.get("MANUVI_MAX_UPLOAD_MB", MAX_UPLOAD_DEFAULT_MB)),
        trust_cf=env.get("MANUVI_TRUST_CF") == "1",
    )
    kw.update(overrides)
    kw["data_dir"].mkdir(parents=True, exist_ok=True)
    return create_portal_app(**kw)
