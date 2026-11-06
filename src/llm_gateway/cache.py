from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

from .errors import BudgetExceeded

from .models import LLMRequest


def cache_key(req: LLMRequest, model_id: str) -> str:
    payload = {
        "source_hash": req.source_hash,
        "prompt_version": req.prompt_version,
        "model_id": model_id,
        "messages": req.messages,
        "params": {
            "response_format": req.response_format,
            "temperature": req.temperature,
            "max_tokens": req.max_tokens,
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


class SqliteStore:
    """Response cache plus monthly spend ledger, one sqlite file."""

    def __init__(self, path: str):
        self.path = str(path)
        with self._conn() as c:
            c.execute(
                "CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, text TEXT, "
                "tokens_in INT, tokens_out INT, model_id TEXT)"
            )
            c.execute("CREATE TABLE IF NOT EXISTS ledger (scope TEXT PRIMARY KEY, usd REAL NOT NULL)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)

    def get(self, key: str) -> Optional[dict]:
        with self._conn() as c:
            r = c.execute(
                "SELECT text, tokens_in, tokens_out, model_id FROM cache WHERE key=?", (key,)
            ).fetchone()
        return dict(zip(("text", "tokens_in", "tokens_out", "model_id"), r)) if r else None

    def put(self, key: str, text: str, tokens_in: int, tokens_out: int, model_id: str) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO cache VALUES (?,?,?,?,?)",
                (key, text, tokens_in, tokens_out, model_id),
            )

    # --- spend ledger (committed + reserved USD per scope, e.g. "month:2026-11") ---
    def spent(self, scope: str) -> float:
        with self._conn() as c:
            r = c.execute("SELECT usd FROM ledger WHERE scope=?", (scope,)).fetchone()
        return r[0] if r else 0.0

    def reserve(self, scopes: "list[tuple[str, Optional[float]]]", est: float) -> None:
        """Atomically check every (scope, cap) and add est to all scopes, or raise BudgetExceeded."""
        c = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            c.execute("BEGIN IMMEDIATE")
            try:
                for scope, cap in scopes:
                    r = c.execute("SELECT usd FROM ledger WHERE scope=?", (scope,)).fetchone()
                    cur = r[0] if r else 0.0
                    if cap is not None and cur + est > cap:
                        raise BudgetExceeded(
                            f"{scope} cap ${cap:.2f} would be exceeded "
                            f"(spent/reserved ${cur:.4f}, est ${est:.4f})"
                        )
                for scope, _ in scopes:
                    c.execute(
                        "INSERT INTO ledger VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET usd=usd+?",
                        (scope, est, est),
                    )
                c.execute("COMMIT")
            except BaseException:
                c.execute("ROLLBACK")
                raise
        finally:
            c.close()

    def settle(self, scopes: "list[str]", reserved: float, actual: float) -> None:
        """Replace a reservation with the actual cost (actual=0 releases it)."""
        with self._conn() as c:
            for scope in scopes:
                c.execute("UPDATE ledger SET usd=MAX(0, usd-?+?) WHERE scope=?",
                          (reserved, actual, scope))

    def month_spend(self, month: str) -> float:
        return self.spent(f"month:{month}")

    def add_spend(self, month: str, usd: float) -> None:
        with self._conn() as c:
            c.execute("INSERT INTO ledger VALUES (?,?) ON CONFLICT(scope) DO UPDATE SET usd=usd+?",
                      (f"month:{month}", usd, usd))
