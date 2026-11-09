import json
import signal

import pytest

from src.jobs import DirQueue, JobRecord, JobState, LocalStore
from src.render.fonts import find_vi_font
from src.pipeline.demo import DemoTranslator
from src.worker import Worker, merge_segments

JID = "job123abc"


@pytest.fixture
def env(tmp_path, pdf_bytes):
    store = LocalStore(tmp_path / "store")
    q = DirQueue(tmp_path / "q", sleep=lambda s: None)
    store.put_job(JobRecord(id=JID, name="t", owner="founder", sections="23:1-3"))
    store.put_bytes(JID, "input.pdf", pdf_bytes)
    q.enqueue(JID)
    return store, q, tmp_path


def mk_worker(env, adapter, **kw):
    store, q, tmp = env
    kw.setdefault("heartbeat", 0.05)
    return Worker(q, store, tmp / "work", profile="dev", adapters=lambda: {"fake": adapter}, **kw)


def test_end_to_end_waits_for_safety_then_finishes(env):
    store, q, _ = env
    ad = DemoTranslator()
    w = mk_worker(env, ad)
    assert w.run_once() == "waiting"          # safety segments need a human click
    st = store.get_status(JID)
    assert st.state == JobState.needs_review and st.chunks > 0 and st.pending > 0
    segs = json.loads(store.get_bytes(JID, "segments.json"))
    assert any(s["kind"] == "signal" for s in segs) and all(s["decision"] == "pending" for s in segs)
    # the founder approves every safety segment in the web app (written to the store copy)
    for s in segs:
        if s["kind"] == "signal":
            s["decision"] = "approved"
    store.put_bytes(JID, "segments.json", json.dumps(segs).encode())
    q.enqueue(JID)                            # "Resume" in the web app
    calls_before = ad.calls
    for _ in range(4):                        # one gate per safety segment
        r = w.run_once()
        if r == "done":
            break
        segs = json.loads(store.get_bytes(JID, "segments.json"))
        for s in segs:
            if s["kind"] == "signal":
                s["decision"] = "approved"
        store.put_bytes(JID, "segments.json", json.dumps(segs).encode())
        q.enqueue(JID)
    assert r == "done"
    st = store.get_status(JID)
    assert st.state == JobState.done and st.pending == 0
    assert set(st.outputs) >= {"out/segments.xlsx", "out/terms.xlsx"}
    for n in st.outputs:
        assert store.exists(JID, n)
    if find_vi_font():
        assert st.error_code is None
        assert store.get_bytes(JID, "out/vi.pdf").startswith(b"%PDF")
        assert store.get_bytes(JID, "out/interleaved.pdf").startswith(b"%PDF")
    assert ad.calls >= calls_before
    assert w.run_once() is None               # queue drained
    # terminal job: a stray duplicate message is dropped without work
    q.enqueue(JID)
    assert w.run_once() == "skipped"


def test_sigterm_then_resume_rebills_nothing(env, tmp_path):
    store, q, _ = env
    baseline = DemoTranslator()
    # uninterrupted reference run on a separate store
    from tests.worker.conftest import make_pdf
    store2 = LocalStore(tmp_path / "store2")
    q2 = DirQueue(tmp_path / "q2")
    store2.put_job(JobRecord(id=JID, name="t", owner="f", sections="23:1-3"))
    store2.put_bytes(JID, "input.pdf", (tmp_path / "in.pdf").read_bytes())
    q2.enqueue(JID)
    Worker(q2, store2, tmp_path / "work2", profile="dev", adapters=lambda: {"fake": baseline}).run_once()
    ref_calls = baseline.calls

    class Killer(DemoTranslator):
        def complete(self, req, model_id):
            out = super().complete(req, model_id)
            if self.calls == 3:
                signal.raise_signal(signal.SIGTERM)   # the real signal, handled by the worker
            return out

    killer = Killer()
    w = mk_worker(env, killer)
    old = signal.getsignal(signal.SIGTERM)
    w.install_signal_handlers()
    try:
        assert w.run_once() == "interrupted"
    finally:
        signal.signal(signal.SIGTERM, old)
        signal.signal(signal.SIGINT, signal.default_int_handler)
    assert store.get_status(JID).state == JobState.queued
    assert store.exists(JID, "state/ledger.sqlite")
    assert killer.calls == 3
    # a fresh worker (new scratch dir: state comes from the store) resumes
    resumed = DemoTranslator()
    w2 = Worker(q, store, tmp_path / "work-other", profile="dev", adapters=lambda: {"fake": resumed},
                heartbeat=0.05)
    assert w2.run_once() == "waiting"
    assert killer.calls + resumed.calls == ref_calls     # 0 re-billed calls


def test_cancel_marker(env):
    store, q, _ = env
    store.request_cancel(JID, "founder")
    assert mk_worker(env, DemoTranslator()).run_once() == "cancelled"
    assert store.get_status(JID).state == JobState.cancelled


def test_bad_pdf_fails_with_code_only(env):
    store, q, _ = env
    store.put_bytes(JID, "input.pdf", b"%PDF-1.4\nnot really")
    r = mk_worker(env, DemoTranslator(), max_attempts=1).run_once()
    st = store.get_status(JID)
    assert r == "failed" and st.state == JobState.failed and st.error_code


def test_unknown_job_is_dropped(env):
    store, q, _ = env
    q.receive()                                   # drain the real job
    q.enqueue("ghost12345")
    assert mk_worker(env, DemoTranslator()).run_once() == "unknown"
    assert q.receive() is None


def test_merge_keeps_human_state_for_same_version():
    local = [{"id": "a", "_pv": 1, "decision": "pending", "target": "x"},
             {"id": "b", "_pv": 2, "decision": "pending", "target": "new"}]
    remote = [{"id": "a", "_pv": 1, "decision": "approved", "target": "edited"},
              {"id": "b", "_pv": 1, "decision": "approved", "target": "old"}]
    m = {s["id"]: s for s in merge_segments(local, remote)}
    assert m["a"]["decision"] == "approved" and m["a"]["target"] == "edited"
    assert m["b"]["target"] == "new" and m["b"]["decision"] == "pending"   # re-run replaced it


def test_priority_sections_are_processed_first_and_reported(tmp_path):
    from tests.worker.conftest import make_pdf
    import pymupdf
    doc = pymupdf.open()
    for label, text in (("23-1", "Remove cover plate."), ("43-1", "Check relay YT12-AB3C."),
                        ("43-2", "Tighten bolt to 12 Nm.")):
        page = doc.new_page()
        page.insert_textbox(pymupdf.Rect(72, 72, 520, 120), text, fontsize=11)
        page.insert_text((280, 820), label, fontsize=9)
    doc.save(str(tmp_path / "two.pdf"))
    store, q = LocalStore(tmp_path / "s"), DirQueue(tmp_path / "q")
    store.put_job(JobRecord(id=JID, name="t", owner="f", sections="23:1-1,43:1-2", priority_sections=["43"]))
    store.put_bytes(JID, "input.pdf", (tmp_path / "two.pdf").read_bytes())
    q.enqueue(JID)

    class Spy(DemoTranslator):
        seen: list = []

        def complete(self, req, model_id):
            Spy.seen.append(next(m["content"] for m in req.messages if m["role"] == "user"))
            return super().complete(req, model_id)

    Spy.seen = []
    w = Worker(q, store, tmp_path / "w", profile="dev", adapters=lambda: {"fake": Spy()})
    assert w.run_once() == "done"
    chunks = json.loads(store.get_bytes(JID, "chunks.json"))
    secs = [c["section"] for c in chunks]
    assert secs.index("23") > max(i for i, s in enumerate(secs) if s == "43")   # priority section first
    first_cover = next(i for i, t in enumerate(Spy.seen) if "cover plate" in t)
    assert "relay" in Spy.seen[0] and all("cover plate" not in t for t in Spy.seen[:first_cover])
    assert any("relay" in t for t in Spy.seen[:first_cover]) and any("bolt" in t for t in Spy.seen[:first_cover])
    st = store.get_status(JID)
    assert list(st.sections)[0] == "43" and st.sections["43"]["pending"] == 0 and st.sections["23"]["chunks"] >= 1
    assert all("cost_usd" in v for v in st.sections.values())
    assert list(st.page_states)[0].startswith("43-")
