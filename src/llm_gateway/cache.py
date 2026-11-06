from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Optional

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
            c.execute("CREATE TABLE IF NOT EXISTS spend (month TEXT PRIMARY KEY, usd REAL)")

    def _conn(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

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

    def month_spend(self, month: str) -> float:
        with self._conn() as c:
            r = c.execute("SELECT usd FROM spend WHERE month=?", (month,)).fetchone()
        return r[0] if r else 0.0

    def add_spend(self, month: str, usd: float) -> None:
        with self._conn() as c:
            c.execute(
                "INSERT INTO spend VALUES (?,?) ON CONFLICT(month) DO UPDATE SET usd=usd+?",
                (month, usd, usd),
            )
