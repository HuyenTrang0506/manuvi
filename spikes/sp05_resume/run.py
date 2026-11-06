"""SP-05 runner: the subprocess that gets SIGKILLed and restarted.

Run the same command again with the same --workdir and --thread to resume.
Exit codes: 0 finished, 3 waiting for human approval (no --approve given).

Examples (from the repo root):
  python spikes/sp05_resume/run.py --workdir /tmp/sp05 --approve
  python spikes/sp05_resume/run.py --workdir /tmp/sp05 --approve --no-guard --stall-at translate:20
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from langgraph.types import Command  # noqa: E402

from graph import (  # noqa: E402
    INITIAL_STATE,
    FakeProvider,
    Ledger,
    TimedSqliteSaver,
    build_graph,
)


def parse_stall(value: str) -> tuple[str, int] | None:
    if not value:
        return None
    stage, chunk = value.split(":")
    return stage, int(chunk)


def checkpoint_stats(path: Path) -> dict:
    conn = sqlite3.connect(path)
    try:
        rows, payload = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(LENGTH(checkpoint) + LENGTH(metadata)), 0) FROM checkpoints"
        ).fetchone()
        writes_payload = conn.execute(
            "SELECT COALESCE(SUM(LENGTH(value)), 0) FROM writes"
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "checkpoint_rows": rows,
        "checkpoint_payload_bytes": payload,
        "writes_payload_bytes": writes_payload,
        "db_file_bytes": path.stat().st_size,
    }


def ledger_stats(path: Path, thread_id: str) -> dict:
    conn = sqlite3.connect(path)
    try:
        billed = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE thread_id=? AND outcome='billed'", (thread_id,)
        ).fetchone()[0]
        reused = conn.execute(
            "SELECT COUNT(*) FROM calls WHERE thread_id=? AND outcome='reused'", (thread_id,)
        ).fetchone()[0]
        dupes = conn.execute(
            "SELECT COUNT(*) FROM (SELECT chunk, stage FROM calls WHERE thread_id=? "
            "AND outcome='billed' GROUP BY chunk, stage HAVING COUNT(*) > 1)",
            (thread_id,),
        ).fetchone()[0]
    finally:
        conn.close()
    return {"billed_calls": billed, "reused_calls": reused, "duplicated_billed_keys": dupes}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--thread", default="sp05-thread-1")
    parser.add_argument("--chunks", type=int, default=50)
    parser.add_argument("--gate-every", type=int, default=10)
    parser.add_argument("--latency", type=float, default=0.02, help="fake provider latency per call (s)")
    parser.add_argument("--approve", action="store_true", help="auto-approve gates (stands in for a human)")
    parser.add_argument("--no-guard", action="store_true", help="disable the step-ledger guard (control run)")
    parser.add_argument("--stall-at", default="", help="stage:chunk, e.g. translate:20 (fault injection)")
    parser.add_argument("--stall-seconds", type=float, default=2.0)
    args = parser.parse_args(argv)

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    run_id = uuid.uuid4().hex[:8]
    guard = not args.no_guard

    ledger = Ledger(
        str(workdir / "ledger.sqlite"),
        thread_id=args.thread,
        run_id=run_id,
        guard=guard,
        stall_at=parse_stall(args.stall_at),
        stall_seconds=args.stall_seconds,
    )
    conn = sqlite3.connect(workdir / "checkpoints.sqlite", check_same_thread=False)
    saver = TimedSqliteSaver(conn)
    graph = build_graph(
        ledger=ledger,
        provider=FakeProvider(args.latency),
        checkpointer=saver,
        n_chunks=args.chunks,
        gate_every=args.gate_every,
    )
    config = {"configurable": {"thread_id": args.thread}}

    snapshot = graph.get_state(config)
    fresh = not snapshot.values
    if fresh:
        mode = "fresh"
    elif snapshot.next:
        mode = f"resume from checkpoint at index={snapshot.values['index']} next={list(snapshot.next)}"
    else:
        mode = "already finished"
    print(f"RUN {run_id} guard={guard} {mode}", flush=True)

    t0 = time.perf_counter()
    graph_input = INITIAL_STATE if fresh else None
    if fresh or snapshot.next:
        while True:
            out = graph.invoke(graph_input, config)
            if "__interrupt__" not in out:
                break
            gate_after = out["__interrupt__"][0].value["approve_after_chunk"]
            print(f"GATE_PENDING after_chunk={gate_after}", flush=True)
            if not args.approve:
                print("WAITING_APPROVAL", flush=True)
                conn.close()
                return 3
            print(f"GATE_APPROVED after_chunk={gate_after}", flush=True)
            graph_input = Command(resume={"approved": True})
    wall = time.perf_counter() - t0

    final = graph.get_state(config).values
    put_ms = [s * 1000 for s in saver.put_seconds]
    summary = {
        "run_id": run_id,
        "guard": guard,
        "translations": len(final.get("translations", [])),
        "qa": len(final.get("qa", [])),
        "wall_seconds": round(wall, 3),
        "checkpoint_puts_this_run": len(put_ms),
        "put_ms_avg": round(sum(put_ms) / len(put_ms), 3) if put_ms else None,
        "put_ms_max": round(max(put_ms), 3) if put_ms else None,
    }
    conn.close()
    summary.update(checkpoint_stats(workdir / "checkpoints.sqlite"))
    summary.update(ledger_stats(workdir / "ledger.sqlite", args.thread))
    print("SUMMARY " + json.dumps(summary, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
