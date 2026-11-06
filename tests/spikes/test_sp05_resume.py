"""SP-05: kill-and-resume with a LangGraph SqliteSaver must not re-bill any chunk.

Offline and fast: the provider is fake and the latency is 10 ms per call.
The subprocess runner is spikes/sp05_resume/run.py.
"""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
RUNNER = REPO / "spikes" / "sp05_resume" / "run.py"
THREAD = "sp05-test-thread"
N_CHUNKS = 50
KILL_CHUNK = 20  # kill while chunk 20's translate call has been billed but not checkpointed
BASE_ARGS = ["--chunks", str(N_CHUNKS), "--gate-every", "10", "--latency", "0.01", "--thread", THREAD]


def _run(workdir: Path, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RUNNER), "--workdir", str(workdir), *BASE_ARGS, *extra],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _kill_at_billed(workdir: Path, chunk: int, stage: str, *extra: str) -> subprocess.Popen:
    """Start the runner and SIGKILL it right after it bills `stage` for `chunk`.

    The runner stalls for several seconds after billing (--stall-at), so the kill
    always lands before LangGraph can checkpoint that node.
    """
    proc = subprocess.Popen(
        [sys.executable, str(RUNNER), "--workdir", str(workdir), *BASE_ARGS, *extra],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    target = f"BILLED chunk={chunk} stage={stage}"
    assert proc.stdout is not None
    for line in proc.stdout:
        if line.strip() == target:
            os.kill(proc.pid, signal.SIGKILL)
            break
    else:
        proc.wait(timeout=10)
        pytest.fail(f"runner exited before reaching {target!r}: {proc.stderr.read()}")
    proc.wait(timeout=10)
    assert proc.returncode == -signal.SIGKILL
    return proc


def _ledger_rows(workdir: Path) -> list[tuple]:
    conn = sqlite3.connect(workdir / "ledger.sqlite")
    try:
        return conn.execute(
            "SELECT run_id, chunk, stage, outcome FROM calls WHERE thread_id=? ORDER BY id",
            (THREAD,),
        ).fetchall()
    finally:
        conn.close()


def _summary(stdout: str) -> dict:
    line = next(l for l in stdout.splitlines() if l.startswith("SUMMARY "))
    return json.loads(line[len("SUMMARY "):])


def _billed_per_key(rows: list[tuple]) -> dict[tuple[int, str], int]:
    counts: dict[tuple[int, str], int] = {}
    for _run, chunk, stage, outcome in rows:
        if outcome == "billed":
            counts[(chunk, stage)] = counts.get((chunk, stage), 0) + 1
    return counts


def test_kill_mid_run_then_resume_bills_each_chunk_once(tmp_path: Path) -> None:
    workdir = tmp_path / "wd"

    _kill_at_billed(workdir, KILL_CHUNK, "translate", "--approve", "--stall-at", f"translate:{KILL_CHUNK}", "--stall-seconds", "5")

    rows_before_kill = _ledger_rows(workdir)
    assert rows_before_kill, "first run should have billed some calls before the kill"

    resumed = _run(workdir, "--approve")
    assert resumed.returncode == 0, resumed.stderr
    assert f"resume from checkpoint at index={KILL_CHUNK}" in resumed.stdout

    rows = _ledger_rows(workdir)
    billed = _billed_per_key(rows)

    # Every (chunk, stage) was billed exactly once, across the kill and the resume.
    assert len(billed) == 2 * N_CHUNKS
    assert set(billed.values()) == {1}, "a chunk was sent to the provider twice"

    # The resumed run started at the checkpoint, not at chunk 0.
    resumed_run_id = _summary(resumed.stdout)["run_id"]
    resumed_rows = [r for r in rows if r[0] == resumed_run_id]
    assert min(chunk for _, chunk, _, _ in resumed_rows) == KILL_CHUNK
    # The in-flight call was served from the ledger instead of being sent again.
    assert [(c, s, o) for _, c, s, o in resumed_rows if o == "reused"] == [(KILL_CHUNK, "translate", "reused")]

    summary = _summary(resumed.stdout)
    assert summary["translations"] == N_CHUNKS
    assert summary["qa"] == N_CHUNKS
    assert summary["duplicated_billed_keys"] == 0
    assert summary["billed_calls"] == 2 * N_CHUNKS


def test_control_without_guard_rebills_the_inflight_call(tmp_path: Path) -> None:
    """Control: same kill, guard off. LangGraph alone re-runs the killed node,
    so exactly the in-flight provider call is billed twice. This is why the guard exists."""
    workdir = tmp_path / "wd"

    _kill_at_billed(workdir, KILL_CHUNK, "translate", "--approve", "--no-guard", "--stall-at", f"translate:{KILL_CHUNK}", "--stall-seconds", "5")
    resumed = _run(workdir, "--approve", "--no-guard")
    assert resumed.returncode == 0, resumed.stderr

    billed = _billed_per_key(_ledger_rows(workdir))
    assert billed[(KILL_CHUNK, "translate")] == 2
    assert {k: v for k, v in billed.items() if k != (KILL_CHUNK, "translate")} == {
        k: 1 for k in billed if k != (KILL_CHUNK, "translate")
    }


def test_human_gate_survives_process_exit(tmp_path: Path) -> None:
    """Without --approve the runner exits at the gate; a later run resumes after approval."""
    workdir = tmp_path / "wd"

    waiting = _run(workdir)
    assert waiting.returncode == 3
    assert "WAITING_APPROVAL" in waiting.stdout
    assert len(_billed_per_key(_ledger_rows(workdir))) == 2 * 10  # chunks 0..9 done, gate at 10

    done = _run(workdir, "--approve")
    assert done.returncode == 0, done.stderr
    billed = _billed_per_key(_ledger_rows(workdir))
    assert len(billed) == 2 * N_CHUNKS
    assert set(billed.values()) == {1}
