"""Profile resolution: env vars > SSM Parameter Store > yaml."""
from __future__ import annotations

import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Mapping, Optional, Protocol

import yaml
from pydantic import BaseModel

from .errors import NotConfigured

log = logging.getLogger(__name__)

ALIASES = ("bulk", "strong", "vision")
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PROFILES = ROOT / "config" / "profiles.yaml"
DEFAULT_PRICES = ROOT / "config" / "prices.yaml"


class ResolvedConfig(BaseModel):
    """Immutable snapshot; a job pins one at start."""

    model_config = {"frozen": True}

    profile: str
    allow_paid: bool
    cap_run_usd: Optional[float] = None
    cap_job_usd: Optional[float] = None
    cap_month_usd: Optional[float] = None
    customer_data_adapters: tuple[str, ...] = ()
    models: dict[str, str]  # alias -> "adapter:model_id"

    def caps_ok(self) -> bool:
        """Paid use needs both a job cap and a month cap above zero."""
        return bool(self.cap_job_usd and self.cap_job_usd > 0 and self.cap_month_usd and self.cap_month_usd > 0)

    def split(self, alias: str) -> tuple[str, str]:
        spec = self.models[alias]
        if spec.strip().lower() == "none":
            raise NotConfigured(f"model alias {alias!r} is disabled (value 'none')")
        adapter, _, model_id = spec.partition(":")
        if not model_id:
            raise NotConfigured(f"bad model spec {spec!r} for alias {alias}")
        return adapter, model_id


class ParameterReader(Protocol):
    def get(self, path: str) -> Optional[str]: ...


class FakeParameterStore:
    """In-memory SSM stand-in for tests (the real reader is SsmConfigSource)."""

    def __init__(self, values: Optional[Mapping[str, str]] = None):
        self.values = dict(values or {})

    def get(self, path: str) -> Optional[str]:
        return self.values.get(path)


def ssm_path(profile: str, *parts: str) -> str:
    return "/".join(["/manuvi", profile, *parts])


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


SSM_TTL_SECONDS = 300
SSM_FAIL_TTL_SECONDS = 60
_MODEL_RE = re.compile(r"^(fake|recorded|ollama|bedrock):[A-Za-z0-9][A-Za-z0-9._:/@+-]{0,199}$")


def _valid_model(v: str) -> bool:
    return v == "none" or bool(_MODEL_RE.match(v))


def _parse_cap(v: str) -> Optional[float]:
    try:
        f = float(v.strip())
    except (ValueError, AttributeError):
        return None
    return f if math.isfinite(f) and f >= 0 else None


class SsmConfigSource:
    """Reads /manuvi/<profile>/... with boto3 `get_parameters_by_path` and caches it for 5 minutes.

    Never raises: with no boto3, no credentials or no network it behaves as an empty source, so the
    yaml values win (dev keeps working offline). The client is created lazily from MANUVI_AWS_PROFILE /
    MANUVI_AWS_REGION; pass `client` to inject a stub.
    """

    def __init__(self, client=None, env: Optional[Mapping[str, str]] = None,
                 ttl: float = SSM_TTL_SECONDS, clock=time.monotonic):
        self._client, self._env = client, os.environ if env is None else env
        self.ttl, self._clock = ttl, clock
        self._cache: dict[str, tuple[float, dict[str, str]]] = {}
        self.last_error: Optional[str] = None
        self.fetches = 0

    @property
    def reachable(self) -> bool:
        return self.last_error is None

    def _get_client(self):
        if self._client is None:
            from .aws import make_client  # lazy: boto3 is only needed when SSM is used
            self._client = make_client("ssm", self._env)
        return self._client

    def _fetch(self, prefix: str) -> dict[str, str]:
        client = self._get_client()
        out: dict[str, str] = {}
        token = None
        while True:
            kw = {"Path": prefix, "Recursive": True, "WithDecryption": True}
            if token:
                kw["NextToken"] = token
            r = client.get_parameters_by_path(**kw)
            for p in r.get("Parameters", []):
                out[p["Name"]] = p["Value"]
            token = r.get("NextToken")
            if not token:
                return out

    def load(self, prefix: str) -> dict[str, str]:
        now = self._clock()
        hit = self._cache.get(prefix)
        if hit and now < hit[0]:
            return hit[1]
        try:
            self.fetches += 1
            values, ttl, self.last_error = self._fetch(prefix), self.ttl, None
        except Exception as e:  # missing creds, offline, access denied, no boto3: fall back to yaml
            values, ttl = {}, min(self.ttl, SSM_FAIL_TTL_SECONDS)
            self.last_error = type(e).__name__
            log.warning("SSM unreachable (%s); using yaml config", self.last_error)
        self._cache[prefix] = (now + ttl, values)
        return values

    def get(self, path: str) -> Optional[str]:
        parts = path.split("/")  # ["", "manuvi", profile, ...]
        if len(parts) < 4:
            return None
        return self.load("/".join(parts[:3]) + "/").get(path)


def ssm_from_env(env: Optional[Mapping[str, str]] = None) -> Optional[SsmConfigSource]:
    """SSM is used only when an AWS profile or region is configured; otherwise stay local and free."""
    env = os.environ if env is None else env
    if env.get("MANUVI_AWS_PROFILE") or env.get("MANUVI_AWS_REGION"):
        return SsmConfigSource(env=env)
    return None


def _apply_ssm(ssm, profile, models, caps, out) -> None:
    """Merge validated SSM values. A parameter can never turn paid models on by itself:
    that needs allow_paid == "true" AND cap/job_usd > 0 AND cap/month_usd > 0, all read from SSM."""
    caps = dict(caps)
    ssm_caps: dict[str, float] = {}
    for key, name in (("job", "job_usd"), ("month", "month_usd")):
        v = ssm.get(ssm_path(profile, "cap", name))
        if v is None:
            continue
        f = _parse_cap(v)
        if f is None:
            log.warning("ignoring invalid SSM cap %s", name)
            continue
        ssm_caps[key] = f
        caps[key] = f
    out["caps"] = caps
    paid_ok = False
    v = ssm.get(ssm_path(profile, "allow_paid"))
    if v is not None:
        flag = v.strip().lower()
        if flag not in ("true", "false"):
            log.warning("ignoring invalid SSM allow_paid (want 'true' or 'false')")
        elif flag == "false":
            out["allow_paid"] = False
        else:
            paid_ok = ssm_caps.get("job", 0) > 0 and ssm_caps.get("month", 0) > 0
            out["allow_paid"] = paid_ok
            if not paid_ok:
                log.warning("SSM allow_paid=true ignored: caps must be set and > 0")
    for a in ALIASES:
        v = ssm.get(ssm_path(profile, "model", a))
        if not v:
            continue
        v = v.strip()
        if not _valid_model(v):
            log.warning("ignoring invalid SSM model value for %s", a)
        elif v.startswith("bedrock:") and not paid_ok:
            log.warning("ignoring SSM paid model for %s: allow_paid and caps not both set in SSM", a)
        else:
            models[a] = v


def resolve_config(
    env: Optional[Mapping[str, str]] = None,
    ssm: Optional[ParameterReader] = None,
    profiles_path: Path = DEFAULT_PROFILES,
) -> ResolvedConfig:
    env = os.environ if env is None else env
    data = yaml.safe_load(Path(profiles_path).read_text())
    profile = env.get("MANUVI_PROFILE") or data.get("default_profile", "dev")
    if profile not in data["profiles"]:
        raise NotConfigured(f"unknown profile {profile!r}")
    y = data["profiles"][profile]
    caps = y.get("caps_usd", {})
    allow_paid = bool(y.get("allow_paid", False))
    models = dict(y["models"])

    if ssm is not None:
        out: dict = {}
        _apply_ssm(ssm, profile, models, caps, out)
        if "allow_paid" in out:
            allow_paid = out["allow_paid"]
        caps = out.get("caps", caps)

    if "MANUVI_ALLOW_PAID" in env:
        allow_paid = _truthy(env["MANUVI_ALLOW_PAID"])
    for a in ALIASES:
        v = env.get(f"MANUVI_MODEL_{a.upper()}")
        if v:
            models[a] = v

    return ResolvedConfig(
        profile=profile,
        allow_paid=allow_paid,
        cap_run_usd=caps.get("run"),
        cap_job_usd=caps.get("job"),
        cap_month_usd=caps.get("month"),
        models=models,
        customer_data_adapters=tuple(y.get("customer_data_adapters", [])),
    )
