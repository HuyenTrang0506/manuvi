"""Untrusted PDFs are capped in prepare: page count and wall-clock time (synthetic PDFs only, $0)."""
import time

import pymupdf
import pytest

from src.intake.jobspec import IntakeError
from src.jobs import DirQueue, JobRecord, JobState, LocalStore
from src.worker import Worker
from src.worker.prepare import DEFAULT_MAX_PAGES, DEFAULT_TIMEOUT_S, PdfLimitError, limits, prepare, run_limited

from .conftest import PAGES, make_pdf


def many_pages(path, n):
    return make_pdf(path, [PAGES[0]] * n)


def sleeper(conn, args):  # runs in the child: never answers
    time.sleep(60)


def crasher(conn, args):  # runs in the child: dies without a message
    import os
    os._exit(1)


def test_defaults_and_env_overrides():
    assert (DEFAULT_MAX_PAGES, DEFAULT_TIMEOUT_S) == (600, 120.0)
    assert limits({}) == (600, 120.0)
    assert limits({"MANUVI_MAX_PAGES": "50", "MANUVI_PREPARE_TIMEOUT": "7.5"}) == (50, 7.5)
    assert limits({"MANUVI_MAX_PAGES": "abc", "MANUVI_PREPARE_TIMEOUT": "-3"}) == (600, 120.0)


def test_pdf_over_the_page_cap_is_rejected_with_a_clear_error(tmp_path):
    pdf = many_pages(tmp_path / "big.pdf", 8)
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(PdfLimitError, match=r"8 pages; the limit is 5") as e:
        prepare(work, pdf, "23:1-3", {}, max_pages=5)
    assert e.value.code == "PDF_LIMIT"
    assert not (work / "chunks.json").exists()                       # nothing was parsed or written


def test_pdf_within_the_cap_still_works(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    info = prepare(work, make_pdf(tmp_path / "ok.pdf"), "23:1-3", {}, max_pages=5, timeout=60)
    assert info["pages"] == 3 and info["chunks"] > 0 and (work / "chunks.json").exists()


def test_env_cap_is_used_when_no_argument(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUVI_MAX_PAGES", "2")
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(PdfLimitError):
        prepare(work, make_pdf(tmp_path / "ok.pdf"), "23:1-3", {})


def test_intake_errors_keep_their_type(tmp_path):
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(IntakeError):
        prepare(work, make_pdf(tmp_path / "ok.pdf"), "23:1-99", {}, timeout=60)


def test_hung_parser_is_killed_at_the_deadline():
    t0 = time.monotonic()
    with pytest.raises(PdfLimitError) as e:
        run_limited(sleeper, (), 1.0)
    assert e.value.code == "PDF_TIMEOUT" and time.monotonic() - t0 < 30


def test_crashing_parser_does_not_take_the_worker_down():
    with pytest.raises(PdfLimitError) as e:
        run_limited(crasher, (), 30)
    assert e.value.code == "PDF_UNREADABLE"


def test_garbage_file_is_unreadable_not_a_crash(tmp_path):
    bad = tmp_path / "x.pdf"
    bad.write_bytes(b"%PDF-1.4 this is not a pdf")
    work = tmp_path / "w"
    work.mkdir()
    with pytest.raises(PdfLimitError) as e:
        prepare(work, bad, "23:1-3", {}, timeout=60)
    assert e.value.code == "PDF_UNREADABLE"


def test_worker_fails_the_job_once_with_the_limit_code(tmp_path, monkeypatch):
    monkeypatch.setenv("MANUVI_MAX_PAGES", "2")
    store = LocalStore(tmp_path / "store")
    q = DirQueue(tmp_path / "q", sleep=lambda s: None)
    store.put_job(JobRecord(id="job123abc", name="t", owner="founder", sections="23:1-3"))
    store.put_bytes("job123abc", "input.pdf", make_pdf(tmp_path / "in.pdf").read_bytes())
    q.enqueue("job123abc")
    w = Worker(q, store, tmp_path / "work", profile="dev", heartbeat=0.05)
    assert w.run_once() == "failed"
    st = store.get_status("job123abc")
    assert st.state == JobState.failed and st.error_code == "PDF_LIMIT"
    assert q.receive() is None                                       # acknowledged: no retry storm
    js = open("src/webapp/static/common.js", encoding="utf-8").read()
    assert all(c in js for c in ("PDF_LIMIT", "PDF_TIMEOUT", "PDF_UNREADABLE"))
