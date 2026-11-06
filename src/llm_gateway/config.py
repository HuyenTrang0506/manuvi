"""Profile resolution: env vars > SSM Parameter Store > yaml."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping, Optional, Protocol

import yaml
from pydantic import BaseModel

from .errors import NotConfigured

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

    def split(self, alias: str) -> tuple[str, str]:
        spec = self.models[alias]
        adapter, _, model_id = spec.partition(":")
        if not model_id:
            raise NotConfigured(f"bad model spec {spec!r} for alias {alias}")
        return adapter, model_id


class ParameterReader(Protocol):
    def get(self, path: str) -> Optional[str]: ...


class FakeParameterStore:
    """In-memory SSM stand-in. Real reader (boto3) is intentionally not implemented yet."""

    def __init__(self, values: Optional[Mapping[str, str]] = None):
        self.values = dict(values or {})

    def get(self, path: str) -> Optional[str]:
        return self.values.get(path)


def ssm_path(profile: str, *parts: str) -> str:
    return "/".join(["/manuvi", profile, *parts])


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on")


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
        v = ssm.get(ssm_path(profile, "allow_paid"))
        if v is not None:
            allow_paid = _truthy(v)
        for a in ALIASES:
            v = ssm.get(ssm_path(profile, "model", a))
            if v:
                models[a] = v

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
