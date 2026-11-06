"""Job-dir persistence: chunk/segment table (job.sqlite) and the step ledger (ledger.sqlite)."""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Callable, Optional

from .schema import CHUNKS_FILE, DB_FILE, LEDGER_FILE, SEGMENTS_FILE, Chunk, Segment


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, timeout=30, isolation_level=None, check_same_thread=False)


class JobDB:
    """Chunks, finished segments and run meta. Large outputs live here, never in graph state."""

    def __init__(self, job_dir: Path):
        self.dir = Path(job_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.conn = _connect(self.dir / DB_FILE)
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS chunks (
                id TEXT PRIMARY KEY, idx INTEGER NOT NULL, data TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'pending',   -- pending | done
                gen INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS segments (
                id TEXT PRIMARY KEY, data TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
            """
        )

    # --- meta
    def get_meta(self, k: str, default: str = "") -> str:
        row = self.conn.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else default

    def set_meta(self, k: str, v: str) -> None:
        self.conn.execute("INSERT INTO meta (k, v) VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))

    # --- chunks
    def load_chunks_file(self) -> list[Chunk]:
        raw = json.loads((self.dir / CHUNKS_FILE).read_text("utf-8"))
        chunks = [Chunk.model_validate(c) for c in raw]
        ids = [c.id for c in chunks]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk ids in chunks.json")
        return chunks

    def sync_chunks(self, chunks: list[Chunk]) -> None:
        """Insert unseen chunks; existing rows (and their state) are kept."""
        for i, c in enumerate(chunks):
            self.conn.execute(
                "INSERT OR IGNORE INTO chunks (id, idx, data) VALUES (?, ?, ?)",
                (c.id, i, c.model_dump_json()),
            )

    def chunk_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT id FROM chunks ORDER BY idx")]

    def get_chunk(self, cid: str) -> Chunk:
        row = self.conn.execute("SELECT data FROM chunks WHERE id=?", (cid,)).fetchone()
        return Chunk.model_validate_json(row[0])

    def set_kind(self, cid: str, kind: str) -> None:
        c = self.get_chunk(cid)
        c.kind = kind  # type: ignore[assignment]
        self.conn.execute("UPDATE chunks SET data=? WHERE id=?", (c.model_dump_json(), cid))

    def gen(self, cid: str) -> int:
        return self.conn.execute("SELECT gen FROM chunks WHERE id=?", (cid,)).fetchone()[0]

    def pending_ids(self) -> list[str]:
        return [r[0] for r in self.conn.execute("SELECT id FROM chunks WHERE state='pending' ORDER BY idx")]

    def reset_chunks(self, ids: list[str]) -> None:
        """Mark chunks pending again with a new generation (new ledger keys, new cache keys)."""
        for cid in ids:
            self.conn.execute("UPDATE chunks SET state='pending', gen=gen+1 WHERE id=?", (cid,))

    # --- segments
    def put_segment(self, seg: Segment) -> None:
        row = self.conn.execute("SELECT data FROM segments WHERE id=?", (seg.id,)).fetchone()
        if row and row[0] == seg.model_dump_json():  # replayed node: no version bump
            self.conn.execute("UPDATE chunks SET state='done' WHERE id=?", (seg.id,))
            return
        self.conn.execute(
            "INSERT INTO segments (id, data, version) VALUES (?, ?, 1) "
            "ON CONFLICT(id) DO UPDATE SET data=excluded.data, version=version+1",
            (seg.id, seg.model_dump_json()),
        )
        self.conn.execute("UPDATE chunks SET state='done' WHERE id=?", (seg.id,))

    def segments(self) -> list[tuple[Segment, int]]:
        rows = self.conn.execute(
            "SELECT s.data, s.version FROM segments s JOIN chunks c ON c.id=s.id ORDER BY c.idx"
        ).fetchall()
        return [(Segment.model_validate_json(d), v) for d, v in rows]

    def export_segments(self) -> Path:
        """Write segments.json. Entries already exported at the same version are kept as the web
        app left them (decisions, human edits); re-run segments are replaced and reset to pending."""
        path = self.dir / SEGMENTS_FILE
        existing: dict[str, dict] = {}
        if path.exists():
            existing = {s["id"]: s for s in json.loads(path.read_text("utf-8"))}
        out = []
        for seg, version in self.segments():
            prev = existing.get(seg.id)
            if prev is not None and prev.get("_pv") == version:
                out.append(prev)
                continue
            d = seg.model_dump()
            d["decision"] = "pending"
            d["_pv"] = version
            out.append(d)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(out, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)
        return path

    def close(self) -> None:
        self.conn.close()


class Ledger:
    """Step ledger: idempotency guard around every gateway call (SP-05).

    Key = (job, chunk, stage). Stage already carries the generation and fix attempt, e.g. 'g0:translate'.
    A 'billed' row is the stored provider answer; a later call with the same key reuses it, so a node
    that LangGraph replays after a kill never sends the request again.
    """

    def __init__(self, job_dir: Path, job_id: str, *, guard: bool = True,
                 stall_at: Optional[tuple[str, str]] = None, stall_seconds: float = 3.0,
                 emit: Callable[[str], None] = lambda s: None):
        self.conn = _connect(Path(job_dir) / LEDGER_FILE)
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY, job_id TEXT NOT NULL, chunk TEXT NOT NULL, stage TEXT NOT NULL,
                outcome TEXT NOT NULL,            -- 'billed' (gateway called) | 'reused' (guard hit)
                text TEXT NOT NULL, tokens_in INT, tokens_out INT, cost_usd REAL, cache_hit INT,
                latency_ms REAL, model_id TEXT, ts REAL NOT NULL)"""
        )
        self.conn.execute("CREATE INDEX IF NOT EXISTS ix_calls ON calls (job_id, chunk, stage)")
        self.job_id, self.guard = job_id, guard
        self.stall_at, self.stall_seconds, self.emit = stall_at, stall_seconds, emit

    def _lookup(self, chunk: str, stage: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT text, tokens_in, tokens_out, cost_usd, cache_hit, latency_ms, model_id FROM calls "
            "WHERE job_id=? AND chunk=? AND stage=? AND outcome='billed' ORDER BY id LIMIT 1",
            (self.job_id, chunk, stage),
        ).fetchone()
        if not row:
            return None
        return dict(zip(("text", "tokens_in", "tokens_out", "cost_usd", "cache_hit", "latency_ms", "model_id"), row))

    def _record(self, chunk: str, stage: str, outcome: str, r: dict) -> None:
        self.conn.execute(
            "INSERT INTO calls (job_id, chunk, stage, outcome, text, tokens_in, tokens_out, cost_usd, "
            "cache_hit, latency_ms, model_id, ts) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (self.job_id, chunk, stage, outcome, r["text"], r["tokens_in"], r["tokens_out"], r["cost_usd"],
             int(r["cache_hit"]), r["latency_ms"], r["model_id"], time.time()),
        )

    def run_step(self, chunk: str, stage: str, call: Callable[[], dict]) -> tuple[dict, str]:
        """Returns (result dict, outcome). `call` must return the keys stored in the ledger."""
        if self.guard:
            cached = self._lookup(chunk, stage)
            if cached is not None:
                self._record(chunk, stage, "reused", cached)
                self.emit(f"REUSED chunk={chunk} stage={stage}")
                return cached, "reused"
        res = call()  # the billed event
        self._record(chunk, stage, "billed", res)
        self.emit(f"BILLED chunk={chunk} stage={stage}")
        if self.stall_at == (chunk, stage):
            time.sleep(self.stall_seconds)  # fault window for kill tests: recorded, not yet checkpointed
        return res, "billed"

    def totals(self) -> dict:
        r = self.conn.execute(
            "SELECT COALESCE(SUM(outcome='billed'),0), COALESCE(SUM(outcome='reused'),0), "
            "COALESCE(SUM(CASE WHEN outcome='billed' THEN cost_usd END),0), "
            "COALESCE(SUM(outcome='billed' AND cache_hit=1),0), "
            "COALESCE(SUM(CASE WHEN outcome='billed' THEN tokens_in END),0), "
            "COALESCE(SUM(CASE WHEN outcome='billed' THEN tokens_out END),0) FROM calls WHERE job_id=?",
            (self.job_id,),
        ).fetchone()
        return {"calls": r[0], "reused": r[1], "cost_usd": r[2], "cache_hits": r[3],
                "tokens_in": r[4], "tokens_out": r[5]}

    def close(self) -> None:
        self.conn.close()
