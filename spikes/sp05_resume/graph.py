"""SP-05 spike: LangGraph translate -> qa loop with a human gate every N chunks.

Two durable stores, deliberately separate:
- checkpoints.sqlite: LangGraph's SqliteSaver (graph position + state).
- ledger.sqlite: a fake billing ledger. Every provider call is recorded here.
  With the guard enabled, a call that is already 'billed' in the ledger is
  never sent again; the node reuses the stored output instead. This closes the
  window where LangGraph re-runs a node that was killed after the provider
  answered but before its checkpoint was written.
"""

from __future__ import annotations

import sqlite3
import sys
import time
from typing import Callable, TypedDict

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class State(TypedDict):
    index: int
    translations: list[str]
    qa: list[str]


def make_chunks(n: int) -> list[str]:
    return [f"Remove connector X{i:03d} and torque bolt M{i:02d} to 12 Nm." for i in range(n)]


class FakeProvider:
    """Stands in for a paid LLM API. Latency is the time before the answer returns."""

    def __init__(self, latency: float) -> None:
        self.latency = latency

    def translate(self, text: str) -> str:
        time.sleep(self.latency)
        return "VI: " + text

    def qa(self, translation: str) -> str:
        time.sleep(self.latency)
        return "pass" if translation.startswith("VI: ") else "fail"


class Ledger:
    """Fake billing ledger in its own sqlite file. One row per provider call."""

    def __init__(
        self,
        path: str,
        *,
        thread_id: str,
        run_id: str,
        guard: bool,
        stall_at: tuple[str, int] | None,
        stall_seconds: float,
    ) -> None:
        self.conn = sqlite3.connect(path, timeout=30, isolation_level=None)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS calls (
                id INTEGER PRIMARY KEY,
                run_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                chunk INTEGER NOT NULL,
                stage TEXT NOT NULL,
                outcome TEXT NOT NULL,   -- 'billed' (provider called) or 'reused' (guard hit)
                output TEXT NOT NULL,
                ts REAL NOT NULL
            )
            """
        )
        self.thread_id = thread_id
        self.run_id = run_id
        self.guard = guard
        self.stall_at = stall_at
        self.stall_seconds = stall_seconds

    def _lookup(self, chunk: int, stage: str) -> str | None:
        row = self.conn.execute(
            "SELECT output FROM calls WHERE thread_id=? AND chunk=? AND stage=? "
            "AND outcome='billed' ORDER BY id LIMIT 1",
            (self.thread_id, chunk, stage),
        ).fetchone()
        return row[0] if row else None

    def _record(self, chunk: int, stage: str, outcome: str, output: str) -> None:
        self.conn.execute(
            "INSERT INTO calls (run_id, thread_id, chunk, stage, outcome, output, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (self.run_id, self.thread_id, chunk, stage, outcome, output, time.time()),
        )

    def run_step(self, stage: str, chunk: int, call: Callable[[], str]) -> str:
        if self.guard:
            cached = self._lookup(chunk, stage)
            if cached is not None:
                self._record(chunk, stage, "reused", cached)
                print(f"REUSED chunk={chunk} stage={stage}", flush=True)
                return cached
        output = call()  # the provider call: this is the billed event
        self._record(chunk, stage, "billed", output)
        print(f"BILLED chunk={chunk} stage={stage}", flush=True)
        if self.stall_at == (stage, chunk):
            # Fault-injection window: billed and recorded, but the node has not
            # returned yet, so LangGraph has not checkpointed it.
            time.sleep(self.stall_seconds)
        return output


class TimedSqliteSaver(SqliteSaver):
    """SqliteSaver that records how long each checkpoint write takes."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        super().__init__(conn)
        self.put_seconds: list[float] = []

    def put(self, config, checkpoint, metadata, new_versions):  # type: ignore[override]
        t0 = time.perf_counter()
        result = super().put(config, checkpoint, metadata, new_versions)
        self.put_seconds.append(time.perf_counter() - t0)
        return result


def build_graph(
    *,
    ledger: Ledger,
    provider: FakeProvider,
    checkpointer: SqliteSaver,
    n_chunks: int,
    gate_every: int,
):
    chunks = make_chunks(n_chunks)

    def translate(state: State) -> dict:
        i = state["index"]
        out = ledger.run_step("translate", i, lambda: provider.translate(chunks[i]))
        return {"translations": [*state["translations"], out]}

    def qa(state: State) -> dict:
        i = state["index"]
        verdict = ledger.run_step("qa", i, lambda: provider.qa(state["translations"][-1]))
        return {"qa": [*state["qa"], verdict], "index": i + 1}

    def gate(state: State) -> dict:
        # Human approval after every `gate_every` chunks. The node has no side
        # effects before interrupt(), so re-running it on resume is harmless.
        interrupt({"approve_after_chunk": state["index"]})
        return {}

    def after_qa(state: State) -> str:
        if state["index"] >= n_chunks:
            return END
        if state["index"] % gate_every == 0:
            return "gate"
        return "translate"

    builder = StateGraph(State)
    builder.add_node("translate", translate)
    builder.add_node("qa", qa)
    builder.add_node("gate", gate)
    builder.add_edge(START, "translate")
    builder.add_edge("translate", "qa")
    builder.add_conditional_edges("qa", after_qa, {"gate": "gate", "translate": "translate", END: END})
    builder.add_edge("gate", "translate")
    return builder.compile(checkpointer=checkpointer)


INITIAL_STATE: State = {"index": 0, "translations": [], "qa": []}
