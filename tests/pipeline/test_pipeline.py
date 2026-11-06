import json
import os
import re
import signal
import sqlite3
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

from src.guard import load_patterns, mask
from src.llm_gateway import Prices
from src.pipeline.demo import DemoTranslator, synthetic_chunks, write_chunks
from src.pipeline.runner import job_status, make_gateway, rerun_selection, run_job
from src.pipeline.schema import Segment
from src.pipeline.tracing import LangfuseExporter, sanitize
from tests.pipeline.conftest import REPO, full, run, segs


# ---------------------------------------------------------------- end to end
def test_end_to_end_synthetic(job):
    res, ad = full(job)
    assert res.state == "done" and res.exit_code == 0
    s = segs(job)
    assert len(s) == 9
    for d in s.values():
        Segment.model_validate({k: v for k, v in d.items() if k in Segment.model_fields})
        assert d["status"] in ("ok", "warn", "error", "needs_review") and 0 <= d["risk"] <= 1
    assert s["C008"]["status"] == "ok" and s["C008"]["target"].startswith("VI: ")  # fixed by retry
    assert ad.calls == 10  # 9 translations + 1 fix, all through the fake adapter
    assert s["C003"]["kind"] == "signal" and s["C003"]["status"] == "needs_review"
    assert "CẢNH BÁO" in s["C003"]["target"]
    st = job_status(job)
    assert st["pending"] == 0 and st["cost_usd"] == 0 and sum(st["by_status"].values()) == 9


def test_protected_codes_byte_identical(job):
    full(job)
    pats = load_patterns()
    for d in segs(job).values():
        if d["status"] in ("ok", "warn"):
            codes = list(mask(d["source"], pats)[1].values())
            assert all(c in d["target"] for c in codes), d["id"]
            assert Counter(mask(d["target"], pats)[1].values()) == Counter(codes)
    assert "YN20-01234-A" in segs(job)["C008"]["target"]


def test_unfixable_goes_to_needs_review_after_two_fixes(tmp_path):
    d = tmp_path / "j"
    write_chunks(d, [{"id": "A1", "page": 1, "source": "Replace YT12-AB3C. ALWAYSBAD", "kind": "prose"}])
    res, ad = full(d)
    s = segs(d)["A1"]
    assert s["status"] == "needs_review" and any("PROT001" in i or "PH001" in i for i in s["issues"])
    assert ad.calls == 3  # 1 translate + 2 fix attempts, no more


@pytest.mark.parametrize("strong", [True, False])
def test_fix_uses_strong_only_if_allowed(tmp_path, strong):
    d = tmp_path / "j"
    write_chunks(d, [{"id": "A1", "page": 1, "source": "Replace YT12-AB3C. DROPME", "kind": "prose"}])
    _, ad = full(d, allow_strong=strong)
    assert [m for m, fix in ad.log if fix] == (["fake-strong"] if strong else ["fake-bulk"])
    assert segs(d)["A1"]["status"] == "ok"


# ---------------------------------------------------------------- safety gate
def test_safety_segments_always_stop_for_a_human(job):
    res, _ = run(job, approve_batches=True)  # batches auto-approved, safety is not
    assert res.state == "waiting" and res.exit_code == 3 and "safety" in res.gate["reasons"]
    assert res.gate["chunk_id"] == "C003"
    s = segs(job)
    assert s["C003"]["status"] == "needs_review" and "C004" not in s  # stopped before moving on
    # still stops when called again: no silent progress
    res2, _ = run(job, approve_batches=True)
    assert res2.state == "waiting" and res2.gate["chunk_id"] == "C003"
    # a human approves safety: the job proceeds to the next safety gate (C007) and stops again
    res3, _ = run(job, approve_batches=True, approve_safety=True)
    assert res3.state == "done"
    assert segs(job)["C003"]["status"] == "needs_review"  # approval never turns safety text ok


def test_batch_gate_waits_without_approval(tmp_path):
    d = tmp_path / "j"
    write_chunks(d, [{"id": f"P{i}", "page": 1, "source": f"Check item {i} of the harness assembly."} for i in range(5)])
    res, _ = run(d, batch_size=2)
    assert res.state == "waiting" and res.gate["reasons"] == ["batch"] and res.gate["after"] == 2
    assert set(segs(d)) == {"P0", "P1"}
    res, _ = run(d, batch_size=2, approve_batches=True)
    assert res.state == "done" and len(segs(d)) == 5


# ---------------------------------------------------------------- resume after SIGKILL
def _cli(job, *extra, env=None):
    return [sys.executable, "-m", "src.pipeline", "run", "--job-dir", str(job), "--job-id", "job1",
            "--batch-size", "4", "--approve-batches", *extra]


def _ledger(job):
    c = sqlite3.connect(job / "ledger.sqlite")
    rows = c.execute("SELECT chunk, stage, outcome FROM calls").fetchall()
    c.close()
    return rows


def _gateway_calls(path):
    return len([l for l in Path(path).read_text().splitlines() if l.strip()])


@pytest.mark.parametrize("guard", [True, False])
def test_resume_after_kill_zero_rebilled(tmp_path, guard):
    job = tmp_path / "job1"
    job.mkdir()
    (job / "fixture.json").write_text(json.dumps([
        {"id": f"C{i:03d}", "page": 1, "source": f"Check item {i} of YT12-AB3C." + (" DROPME" if i == 8 else "")}
        for i in range(1, 13)]))
    log = tmp_path / "calls.log"
    env = {**os.environ, "MANUVI_LLM_CALL_LOG": str(log)}
    extra = [] if guard else ["--no-guard"]
    stall = "C005:g0:translate"
    p = subprocess.Popen(_cli(job, "--pdf-text-fixture", str(job / "fixture.json"), "--stall-at", stall, *extra),
                         cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    for line in p.stdout:
        if line.strip() == "BILLED chunk=C005 stage=g0:translate":
            os.kill(p.pid, signal.SIGKILL)
            break
    else:
        pytest.fail(p.stderr.read())
    p.wait(timeout=10)
    assert p.returncode == -signal.SIGKILL
    assert not (job / "segments.json").exists() or "C005" not in {s["id"] for s in json.loads((job / "segments.json").read_text())}

    r = subprocess.run(_cli(job, *extra), cwd=REPO, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    rows = _ledger(job)
    billed = Counter((c, s) for c, s, o in rows if o == "billed")
    assert len(billed) == 13  # 12 translations + the one DROPME fix; every key billed
    if guard:
        assert max(billed.values()) == 1
        assert ("C005", "g0:translate", "reused") in rows
        assert _gateway_calls(log) == 13  # the gateway was never asked twice: 0 re-billed calls
    else:  # control: without the guard the in-flight call is sent again (the cache hides the money here)
        assert _gateway_calls(log) == 14 and billed[("C005", "g0:translate")] == 2
    assert len(segs(job)) == 12


def test_resume_after_kill_at_gate_keeps_done_work(tmp_path):
    """Waiting at a gate is a clean stop: a later process continues without touching done chunks."""
    d = tmp_path / "j"
    write_chunks(d, [{"id": f"P{i}", "page": 1, "source": f"Check item {i} of the harness assembly."} for i in range(4)])
    run(d, batch_size=2)
    before = _ledger(d)
    run(d, batch_size=2, approve_batches=True)
    after = _ledger(d)
    assert after[: len(before)] == before and len([r for r in after if r[2] == "billed"]) == 4


# ---------------------------------------------------------------- budget cap
def _paid_gateway(tmp_path, job):
    (tmp_path / "prices.yaml").write_text("models:\n  fake-bulk: {input_per_1k: 1.0, output_per_1k: 1.0}\n  fake-strong: {input_per_1k: 1.0, output_per_1k: 1.0}\n")
    (tmp_path / "profiles.yaml").write_text(
        "default_profile: p\nprofiles:\n  p:\n    allow_paid: true\n    caps_usd: {job: 100}\n"
        "    models: {bulk: 'fake:fake-bulk', strong: 'fake:fake-strong', vision: 'fake:fake-bulk'}\n")
    ad = DemoTranslator(paid=True)
    return make_gateway(job, "p", adapters={"fake": ad}, profiles_path=tmp_path / "profiles.yaml",
                        prices=Prices(tmp_path / "prices.yaml")), ad


def test_budget_cap_stops_the_job(tmp_path, job):
    gw, ad = _paid_gateway(tmp_path, job)
    res = run_job(job, gateway=gw, job_id="job1", batch_size=50, approve_batches=True, approve_safety=True, budget_usd=1.0)
    assert res.state == "budget_stopped" and res.exit_code == 4
    st = job_status(job, "job1")
    assert 0 < st["chunks"] - st["pending"] < 9
    assert st["cost_usd"] <= 1.0 and gw.job_spent_usd("job1") <= 1.0
    calls_at_stop = ad.calls
    # a higher cap continues from the checkpoint; finished chunks are not billed again
    gw2, ad2 = _paid_gateway(tmp_path, job)
    res2 = run_job(job, gateway=gw2, job_id="job1", batch_size=50, approve_batches=True, approve_safety=True, budget_usd=50.0)
    assert res2.state == "done" and calls_at_stop + ad2.calls == 10
    assert max(Counter((c, s) for c, s, o in _ledger(job) if o == "billed").values()) == 1


# ---------------------------------------------------------------- status and rerun
def test_rerun_only_failed_chunks(tmp_path):
    d = tmp_path / "j"
    write_chunks(d, [
        {"id": "A1", "page": 1, "source": "Replace YT12-AB3C.", "kind": "prose"},
        {"id": "A2", "page": 2, "source": "Replace YN20-01234-A. ALWAYSBAD", "kind": "prose"},
        {"id": "A3", "page": 3, "source": "WARNING: Hot.", "kind": "prose"},
    ])
    res, _ = full(d)
    st = job_status(d)
    assert st["by_status"]["needs_review"] == 2  # A2 (QA) and A3 (always-on safety flag)
    before = segs(d)
    rows_before = _ledger(d)

    gw = make_gateway(d, "dev", adapters={"fake": (ad := DemoTranslator())})
    res = rerun_selection(d, gateway=gw, failed=True, job_id="job1", approve_batches=True, approve_safety=True)
    assert res.state == "done"
    assert ad.calls == 3  # A2 only: translate + 2 fixes. A1 and the safety chunk A3 are untouched.
    new_rows = _ledger(d)[len(rows_before):]
    assert {c for c, _, _ in new_rows} == {"A2"} and all(s.startswith("g1:") for _, s, _ in new_rows)
    after = segs(d)
    assert after["A1"] == before["A1"] and after["A3"] == before["A3"]


def test_rerun_selected_pages(job):
    full(job)
    gw = make_gateway(job, "dev", adapters={"fake": (ad := DemoTranslator())})
    res = rerun_selection(job, gateway=gw, pages=[2], job_id="job1", approve_batches=True, approve_safety=True)
    assert res.state == "done" and ad.calls == 3  # C004, C005, C006 are on page 2
    st = job_status(job)
    assert st["calls"] == 10 + 3 and st["pending"] == 0


def test_rerun_keeps_web_app_decisions_of_untouched_segments(job):
    full(job)
    p = job / "segments.json"
    data = json.loads(p.read_text())
    data[0]["decision"] = "approved"
    data[0]["target"] = "human edit"
    p.write_text(json.dumps(data))
    gw = make_gateway(job, "dev", adapters={"fake": DemoTranslator()})
    rerun_selection(job, gateway=gw, pages=[2], job_id="job1", approve_batches=True, approve_safety=True)
    s = segs(job)
    assert s["C001"]["decision"] == "approved" and s["C001"]["target"] == "human edit"
    assert s["C004"]["decision"] == "pending"


# ---------------------------------------------------------------- tracing
def test_trace_records_and_no_customer_text(job):
    full(job)
    recs = [json.loads(l) for l in (job / "trace.jsonl").read_text().splitlines()]
    tr = [r for r in recs if r["node"] == "translate"]
    assert len(tr) == 9
    for k in ("tokens_in", "tokens_out", "cost_usd", "latency_ms", "node_latency_ms", "prompt_version", "model", "chunk_id"):
        assert k in tr[0], k
    blob = (job / "trace.jsonl").read_text()
    assert "relay box" not in blob and "VI:" not in blob


def test_langfuse_exporter_is_off_by_default_and_never_gets_text(job, monkeypatch):
    monkeypatch.delenv("MANUVI_LANGFUSE", raising=False)
    full(job)  # no langfuse module installed and none requested: must not matter
    events = []

    class Stub:
        def create_event(self, **kw):
            events.append(kw)

    full_job = job.parent / "j2"
    write_chunks(full_job, synthetic_chunks(3))
    gw = make_gateway(full_job, "dev", adapters={"fake": DemoTranslator()})
    run_job(full_job, gateway=gw, job_id="j2", approve_batches=True, approve_safety=True, langfuse=LangfuseExporter(Stub()))
    assert events and all(e["name"].startswith("pipeline.") for e in events)
    assert "relay box" not in json.dumps(events) and "VI:" not in json.dumps(events)
    assert sanitize({"node": "x", "text": "secret", "source": "secret"}) == {"node": "x"}


# ---------------------------------------------------------------- review fixes
def test_signal_detection_is_case_insensitive(tmp_path):
    d = tmp_path / "j"
    write_chunks(d, [{"id": "A", "page": 1, "source": "Warning: hot surface.", "kind": "prose"},
                     {"id": "B", "page": 1, "source": "CAUTION", "kind": "label"},
                     {"id": "C", "page": 1, "source": "Caution: sharp edge near YT12-AB3C.", "kind": "step"}])
    res, _ = run(d)
    assert res.state == "waiting"
    assert {k: v["kind"] for k, v in segs(d).items()} == {"A": "signal"}  # stopped at the first safety gate
    from src.pipeline.db import JobDB
    db = JobDB(d)
    assert [db.get_chunk(c).kind for c in ("A", "B", "C")] == ["signal"] * 3
    db.close()


def test_safety_gate_needs_recorded_human_approval(job):
    res, _ = run(job, approve_batches=True)
    assert res.state == "waiting" and res.gate["chunk_id"] == "C003"
    p = job / "segments.json"
    data = json.loads(p.read_text())
    for s in data:
        if s["id"] == "C003":
            s["decision"] = "rejected"
    p.write_text(json.dumps(data))
    assert run(job, approve_batches=True)[0].gate["chunk_id"] == "C003"  # still stopped
    for s in data:
        if s["id"] == "C003":
            s["decision"] = "approved"
    p.write_text(json.dumps(data))
    res, _ = run(job, approve_batches=True)
    assert res.state == "waiting" and res.gate["chunk_id"] == "C007"  # next safety segment needs its own click
    r = subprocess.run([sys.executable, "-m", "src.pipeline", "run", "--approve-safety", "--job-dir", str(job)],
                       cwd=REPO, capture_output=True, text=True)
    assert r.returncode != 0  # the option no longer exists


def test_export_preserves_decisions_with_interleaved_write(job):
    from src.common.filelock import file_lock
    from src.pipeline.db import JobDB
    full(job)
    p = job / "segments.json"
    with file_lock(p):  # the web app writes a decision between two pipeline exports
        data = json.loads(p.read_text())
        data[1]["decision"] = "approved"
        p.write_text(json.dumps(data))
    db = JobDB(job)
    db.export_segments()
    db.close()
    assert segs(job)["C002"]["decision"] == "approved"


def test_output_is_nfc_and_gw_error_has_no_provider_text(tmp_path):
    import unicodedata
    from src.llm_gateway import ServerError

    class Leaky(DemoTranslator):
        def complete(self, req, model_id):
            if "boom" in req.messages[-1]["content"]:
                raise ServerError("provider said: SECRET customer text")
            r = super().complete(req, model_id)
            return {**r, "text": unicodedata.normalize("NFD", r["text"] + " tiếng việt")}

    d = tmp_path / "j"
    write_chunks(d, [{"id": "A", "page": 1, "source": "Check relay."}, {"id": "B", "page": 1, "source": "boom"}])
    gw = make_gateway(d, "dev", adapters={"fake": Leaky()}, max_retries=0)
    run_job(d, gateway=gw, job_id="job1", approve_batches=True)
    s = segs(d)
    assert unicodedata.is_normalized("NFC", s["A"]["target"]) and "tiếng việt" in s["A"]["target"]
    assert s["B"]["status"] == "error" and "SECRET" not in json.dumps(s["B"]) and "ServerError" in s["B"]["issues"][0]


def test_started_row_without_result_is_flagged_not_rebilled(tmp_path):
    from src.pipeline.db import Ledger
    d = tmp_path / "j"
    write_chunks(d, [{"id": "A", "page": 1, "source": "Check relay."}])
    lg = Ledger(d, "job1")
    lg._mark("started", "A", "g0:translate")  # a process died after this row, before the result was recorded
    lg.close()
    res, ad = run(d)
    s = segs(d)["A"]
    assert ad.calls == 0 and s["status"] == "needs_review" and s["issues"][0].startswith("LEDGER001")
