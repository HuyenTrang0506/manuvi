from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Optional

import yaml

from .adapters import Adapter, BedrockAdapter, FakeAdapter, OllamaAdapter, estimate_tokens
from .cache import SqliteStore, cache_key
from .config import DEFAULT_PRICES, ParameterReader, ResolvedConfig, resolve_config
from .errors import BudgetExceeded, DataClassViolation, NotConfigured, TransientError
from .models import LLMRequest, LLMResult


class Prices:
    def __init__(self, path: Path = DEFAULT_PRICES):
        self.table = yaml.safe_load(Path(path).read_text()).get("models", {})

    def cost(self, model_id: str, tokens_in: int, tokens_out: int) -> float:
        p = self.table.get(model_id)
        if p is None:
            raise NotConfigured(f"no price entry for paid model {model_id!r}")
        return tokens_in / 1000 * p["input_per_1k"] + tokens_out / 1000 * p["output_per_1k"]


class Job:
    """Pins the resolved config at start; later env/SSM changes do not affect it."""

    def __init__(self, job_id: str, config: ResolvedConfig):
        self.job_id = job_id
        self.config = config
        self.spent_usd = 0.0


class Gateway:
    def __init__(
        self,
        cache_path,
        env: Optional[Mapping[str, str]] = None,
        ssm: Optional[ParameterReader] = None,
        adapters: Optional[dict[str, Adapter]] = None,
        prices: Optional[Prices] = None,
        call_log_path=None,
        max_retries: int = 3,
        backoff_base: float = 0.5,
        sleep: Callable[[float], None] = time.sleep,
        rng: Optional[random.Random] = None,
        profiles_path=None,
    ):
        self.store = SqliteStore(cache_path)
        self._env, self._ssm = env, ssm
        self._profiles_path = profiles_path
        self.adapters = adapters or {}
        self.prices = prices or Prices()
        self.call_log_path = call_log_path
        self.max_retries, self.backoff_base = max_retries, backoff_base
        self._sleep, self._rng = sleep, rng or random.Random()
        self.run_spent_usd = 0.0
        self.jobs: dict[str, Job] = {}

    # --- jobs / config -------------------------------------------------
    def start_job(self, job_id: str) -> Job:
        kw = {"profiles_path": self._profiles_path} if self._profiles_path else {}
        job = Job(job_id, resolve_config(self._env, self._ssm, **kw))
        self.jobs[job_id] = job
        return job

    def _adapter(self, name: str, cfg: ResolvedConfig) -> Adapter:
        if name in self.adapters:
            return self.adapters[name]
        if name == "fake":
            a = FakeAdapter()
        elif name == "ollama":
            a = OllamaAdapter()
        elif name == "bedrock":
            a = BedrockAdapter(allow_paid=cfg.allow_paid)
        else:
            raise NotConfigured(f"adapter {name!r} not available (recorded needs explicit fixture)")
        self.adapters[name] = a
        return a

    # --- main entry ----------------------------------------------------
    def complete(self, req: LLMRequest) -> LLMResult:
        job = self.jobs.get(req.job_id) or self.start_job(req.job_id)
        cfg = job.config
        adapter_name, model_id = cfg.split(req.model_alias)
        adapter = self._adapter(adapter_name, cfg)

        if req.data_class not in adapter.data_classes:
            raise DataClassViolation(
                f"adapter {adapter_name!r} accepts only {sorted(adapter.data_classes)}, "
                f"got {req.data_class!r}"
            )

        key = cache_key(req, model_id)
        hit = self.store.get(key)
        if hit:
            self._log(req, adapter, model_id, cache_hit=True, cost=0.0)
            return LLMResult(text=hit["text"], tokens_in=hit["tokens_in"],
                             tokens_out=hit["tokens_out"], cost_usd=0.0, latency_ms=0.0,
                             cache_hit=True, model_id=model_id)

        if adapter.paid:
            if not cfg.allow_paid:
                raise NotConfigured("paid call refused: allow_paid is false")
            self._check_caps(job, req, model_id)

        t0 = time.perf_counter()
        out = self._call_with_retries(adapter, req, model_id)
        latency = (time.perf_counter() - t0) * 1000

        cost = 0.0
        if adapter.paid:
            cost = self.prices.cost(model_id, out["tokens_in"], out["tokens_out"])
            job.spent_usd += cost
            self.run_spent_usd += cost
            self.store.add_spend(self._month(), cost)
        self.store.put(key, out["text"], out["tokens_in"], out["tokens_out"], model_id)
        self._log(req, adapter, model_id, cache_hit=False, cost=cost)
        return LLMResult(text=out["text"], tokens_in=out["tokens_in"], tokens_out=out["tokens_out"],
                         cost_usd=cost, latency_ms=latency, cache_hit=False, model_id=model_id)

    # --- internals -----------------------------------------------------
    @staticmethod
    def _month() -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _check_caps(self, job: Job, req: LLMRequest, model_id: str) -> None:
        cfg = job.config
        est = self.prices.cost(model_id, estimate_tokens(req.messages), req.max_tokens or 1024)
        checks = (
            ("run", cfg.cap_run_usd, self.run_spent_usd),
            ("job", cfg.cap_job_usd, job.spent_usd),
            ("month", cfg.cap_month_usd, self.store.month_spend(self._month())),
        )
        for name, cap, spent in checks:
            if cap is not None and spent + est > cap:
                raise BudgetExceeded(
                    f"{name} cap ${cap:.2f} would be exceeded (spent ${spent:.4f}, est ${est:.4f})"
                )

    def _call_with_retries(self, adapter: Adapter, req: LLMRequest, model_id: str) -> dict:
        attempt = 0
        while True:
            try:
                return adapter.complete(req, model_id)
            except TransientError:
                if attempt >= self.max_retries:
                    raise
                delay = self.backoff_base * (2 ** attempt) * (1 + self._rng.random())
                self._sleep(delay)
                attempt += 1

    def _log(self, req, adapter, model_id, cache_hit: bool, cost: float) -> None:
        import os
        path = self.call_log_path or os.environ.get("MANUVI_LLM_CALL_LOG")
        if not path:
            return
        rec = {"job_id": req.job_id, "target_id": req.target_id, "adapter": adapter.name,
               "model_id": model_id, "paid": adapter.paid, "cache_hit": cache_hit,
               "cost_usd": cost}
        with open(path, "a") as f:
            f.write(json.dumps(rec) + "\n")
