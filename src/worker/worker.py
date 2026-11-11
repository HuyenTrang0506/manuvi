"""Pull-based worker: claim -> download -> intake -> pipeline -> upload, with heartbeats.

Outbound connections only. Logs carry job ids, counts and error class names, never document text.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import threading
import time
import uuid
from pathlib import Path
from typing import Callable, Optional

from src.common.filelock import file_lock
from src.intake.jobspec import IntakeError
from src.jobs import (TERMINAL, JobRecord, JobState, JobStatus, LostMessage, Message, Queue, Store, VersionConflict)
from src.jobs.model import utcnow
from src.llm_gateway.adapters import Adapter
from src.pipeline.runner import job_status, make_gateway, run_job
from src.pipeline.schema import CHECKPOINT_FILE, CACHE_FILE, DB_FILE, LEDGER_FILE, SEGMENTS_FILE

from src.formats import FormatError
from src.formats.job import FORMAT_FILE, build_file_output, input_name, prepare_file

from .outputs import build_outputs, segments_xlsx
from .prepare import JOBSPEC_FILE, LAYOUT_FILE, TERMS_FILE, PdfLimitError, prepare

log = logging.getLogger("manuvi.worker")
STATE_FILES = (DB_FILE, LEDGER_FILE, CHECKPOINT_FILE, CACHE_FILE)  # resume state, uploaded as state/<name>
META_FILES = ("chunks.json", LAYOUT_FILE, JOBSPEC_FILE, TERMS_FILE, FORMAT_FILE)
MERGE_TRIES = 6


class StopRequested(Exception):
    """Raised right after a billed call was recorded; the checkpoint/ledger make the resume free."""


def merge_segments(local: list[dict], remote: list[dict]) -> list[dict]:
    """Human state wins: for an id whose `_pv` is equal on both sides, keep the remote entry
    (decisions and edits made in the web app); otherwise the local (newer pipeline run) entry."""
    rem = {s["id"]: s for s in remote}
    out = []
    for s in local:
        r = rem.get(s["id"])
        out.append(r if r is not None and r.get("_pv") == s.get("_pv") else s)
    seen = {s["id"] for s in local}
    out += [s for s in remote if s["id"] not in seen]
    return out


class Worker:
    def __init__(self, queue: Queue, store: Store, work_root: str | Path, *, profile: str = "qa",
                 adapters: Optional[Callable[[], dict[str, Adapter]]] = None, worker_id: Optional[str] = None,
                 visibility: int = 300, heartbeat: float = 30.0, poll_wait: float = 10.0, batch_size: int = 10,
                 approve_batches: bool = True, budget_usd: Optional[float] = None, max_attempts: int = 3):
        self.queue, self.store, self.root = queue, store, Path(work_root)
        self.profile, self.adapters = profile, adapters
        self.id = worker_id or f"w-{uuid.uuid4().hex[:8]}"
        self.visibility, self.heartbeat, self.poll_wait = visibility, heartbeat, poll_wait
        self.batch_size, self.approve_batches, self.budget = batch_size, approve_batches, budget_usd
        self.max_attempts = max_attempts
        self.stop = threading.Event()        # SIGTERM / SIGINT
        self._status_lock = threading.RLock()  # serialises status read-modify-write (main thread vs heartbeat)
        self._halt: Optional[threading.Event] = None
        self._beat_thread: Optional[threading.Thread] = None
        self._cancel = threading.Event()     # founder cancelled the job
        self._lost = threading.Event()       # visibility lost to another worker

    # ---- lifecycle
    def request_stop(self, *_a) -> None:
        self.stop.set()

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run_forever(self) -> None:
        log.info("worker %s started", self.id)
        while not self.stop.is_set():
            self.run_once(wait=self.poll_wait)
        log.info("worker %s stopped", self.id)

    def run_once(self, wait: float = 0) -> Optional[str]:
        msg = self.queue.receive(wait_seconds=wait, visibility_timeout=self.visibility)
        if msg is None:
            return None
        return self.process(msg)

    # ---- status helpers
    def _status(self, job_id: str, state: JobState, **kw) -> JobStatus:
        with self._status_lock:
            st = self.store.get_status(job_id)
            st = st.model_copy(update={"state": state, "updated_at": utcnow(), "worker": self.id, **kw})
            self.store.put_status(job_id, st)
            return st

    def _quiesce(self) -> None:
        """Stop the heartbeat before any terminal/waiting status write so it cannot overwrite it with 'running'."""
        if self._halt is not None:
            self._halt.set()
        t = self._beat_thread
        if t is not None and t.is_alive() and t is not threading.current_thread():
            t.join(timeout=5)

    def _progress(self, job_id: str, work: Path) -> dict:
        try:
            s = job_status(work, job_id)
        except Exception:
            return {}
        return {"chunks": s["chunks"], "pending": s["pending"], "by_status": s["by_status"],
                "calls": s["calls"], "reused": s["reused_from_ledger"], "cost_usd": s["cost_usd"],
                "page_states": self._page_states(work), "sections": self._section_progress(work, job_id)}

    @staticmethod
    def _section_progress(work: Path, job_id: str) -> dict[str, dict]:
        """Per section: text blocks, blocks still pending and ledger cost in USD. No text."""
        import sqlite3
        try:
            chunks = json.loads((work / "chunks.json").read_text("utf-8"))
            sp = work / SEGMENTS_FILE
            done = {s["id"] for s in json.loads(sp.read_text("utf-8"))} if sp.exists() else set()
        except (OSError, ValueError, KeyError):
            return {}
        cost: dict[str, float] = {}
        try:
            con = sqlite3.connect(f"file:{work / LEDGER_FILE}?mode=ro", uri=True, timeout=5)
            try:
                cost = dict(con.execute("SELECT chunk, COALESCE(SUM(cost_usd),0) FROM calls "
                                        "WHERE job_id=? AND outcome='billed' GROUP BY chunk", (job_id,)).fetchall())
            finally:
                con.close()
        except sqlite3.Error:
            pass
        out: dict[str, dict] = {}
        for c in chunks:  # chunks.json is in processing order, so priority sections come first
            d = out.setdefault(c["section"], {"chunks": 0, "pending": 0, "cost_usd": 0.0})
            d["chunks"] += 1
            d["pending"] += c["id"] not in done
            d["cost_usd"] = round(d["cost_usd"] + cost.get(c["id"], 0.0), 6)
        return out

    @staticmethod
    def _page_states(work: Path) -> dict[str, str]:
        """Printed page label (e.g. "23-4") -> pending | review | done. Counts only, no text."""
        try:
            spec = json.loads((work / JOBSPEC_FILE).read_text("utf-8"))
            chunks = json.loads((work / "chunks.json").read_text("utf-8"))
            sp = work / SEGMENTS_FILE
            segs = {s["id"]: s for s in json.loads(sp.read_text("utf-8"))} if sp.exists() else {}
        except (OSError, ValueError, KeyError):
            return {}
        by_page: dict[int, list[str]] = {}
        for c in chunks:
            s = segs.get(c["id"])
            state = "pending" if s is None else ("review" if s.get("status") != "ok" or s.get("decision") == "pending"
                                                 and s.get("kind") == "signal" else "done")
            by_page.setdefault(c["page"], []).append(state)
        out = {}
        for p in spec["pages"]:
            st = by_page.get(p["pdf_page"], [])
            out[p["label"]] = ("pending" if "pending" in st else "review" if "review" in st else "done") if st \
                else ("review" if p.get("scanned") else "done")
        return out

    # ---- file sync
    def _pull(self, job_id: str, work: Path, fmt: str = "pdf") -> None:
        name = "input.pdf" if fmt == "pdf" else input_name(fmt)
        pdf = work / name
        if not pdf.exists():
            self.store.get_file(job_id, name, pdf)
        for n in META_FILES:
            if not (work / n).exists() and self.store.exists(job_id, n):
                self.store.get_file(job_id, n, work / n)
        for n in STATE_FILES:
            if not (work / n).exists() and self.store.exists(job_id, f"state/{n}"):
                self.store.get_file(job_id, f"state/{n}", work / n)
        self._merge_segments(job_id, work, upload=False)
        if self.store.exists(job_id, TERMS_FILE):  # web decisions on terms win
            self.store.get_file(job_id, TERMS_FILE, work / TERMS_FILE)

    def _merge_segments(self, job_id: str, work: Path, upload: bool = True) -> list[dict]:
        """Merge local pipeline output with the store's copy. The upload is a conditional write (If-Match on the
        version we merged against): if the web app saved a decision in between, we merge again with that newer
        copy instead of overwriting it."""
        path = work / SEGMENTS_FILE
        with file_lock(path):
            local = json.loads(path.read_text("utf-8")) if path.exists() else []
            for _ in range(MERGE_TRIES):
                try:
                    raw, etag = self.store.get_versioned(job_id, SEGMENTS_FILE)
                    remote = json.loads(raw)
                except FileNotFoundError:
                    remote, etag = [], None
                merged = merge_segments(local, remote) if local else remote
                data = json.dumps(merged, ensure_ascii=False, indent=1)
                if merged or path.exists():
                    tmp = path.with_suffix(".json.tmp")
                    tmp.write_text(data, "utf-8")
                    os.replace(tmp, path)
                if not (upload and merged != remote and (merged or path.exists())):
                    return merged
                try:
                    self.store.put_if_match(job_id, SEGMENTS_FILE, data.encode("utf-8"), etag)
                    return merged
                except VersionConflict:
                    continue  # a web edit landed after our read: merge against it
            raise VersionConflict(SEGMENTS_FILE)

    def _push_state(self, job_id: str, work: Path) -> None:
        for n in STATE_FILES:
            if (work / n).exists():
                self.store.put_file(job_id, f"state/{n}", work / n)

    def _push_meta(self, job_id: str, work: Path) -> None:
        for n in META_FILES:
            if (work / n).exists() and not self.store.exists(job_id, n):
                self.store.put_file(job_id, n, work / n)

    # ---- heartbeat
    def _beat(self, msg: Message, job_id: str, work: Path, halt: threading.Event) -> None:
        while not halt.wait(self.heartbeat):
            try:
                self.queue.extend(msg, self.visibility)
            except LostMessage:
                self._lost.set()
                return
            except Exception as e:  # network blip: try again next beat
                log.warning("heartbeat failed: %s", type(e).__name__)
            try:
                if self.store.is_cancelled(job_id):
                    self._cancel.set()
                with self._status_lock:
                    if halt.is_set():
                        return
                    self._merge_segments(job_id, work)
                    self._status(job_id, JobState.running, **self._progress(job_id, work))
            except Exception as e:
                log.warning("sync failed: %s", type(e).__name__)

    # ---- main
    def process(self, msg: Message) -> str:
        job_id = msg.job_id
        self._cancel.clear()
        self._lost.clear()
        try:
            rec = self.store.get_job(job_id)
        except (FileNotFoundError, ValueError):
            self.queue.ack(msg)
            return "unknown"
        st = self.store.get_status(job_id)
        rebuild = self.store.exists(job_id, "rebuild.json")
        if st.state in TERMINAL and not (rebuild and st.state == JobState.done):
            self.queue.ack(msg)
            return "skipped"
        if self.store.is_cancelled(job_id):
            self._status(job_id, JobState.cancelled)
            self.queue.ack(msg)
            return "cancelled"
        work = self.root / job_id
        work.mkdir(parents=True, exist_ok=True)
        halt = threading.Event()
        beat = threading.Thread(target=self._beat, args=(msg, job_id, work, halt), daemon=True)
        self._halt, self._beat_thread = halt, beat
        outcome = "failed"
        try:
            self._status(job_id, JobState.running if st.state not in TERMINAL else JobState.done,
                         attempts=st.attempts + 1, error_code=None)
            self._pull(job_id, work, rec.format)
            if rebuild:
                return self._finish(msg, job_id, rec, work, final_state=JobState.done, outcome="rebuilt")
            if not (work / "chunks.json").exists() and rec.format != "pdf":
                info = prepare_file(work, work / input_name(rec.format), original_name=rec.name)
                self._push_meta(job_id, work)
                self._status(job_id, JobState.running, pages=info["pages"], chunks=info["chunks"],
                             pending=info["chunks"])
            elif not (work / "chunks.json").exists():
                info = prepare(work, work / "input.pdf", rec.sections, rec.page_map, rec.priority_sections)
                self._push_meta(job_id, work)
                self._status(job_id, JobState.running, pages=info["pages"], chunks=info["chunks"],
                             pending=info["chunks"])
            beat.start()
            outcome = self._run(msg, job_id, rec, work)
        except IntakeError:
            self._fail(msg, job_id, "INTAKE", ack=True)
            outcome = "failed"
        except FormatError as e:  # hostile or unsupported file: retrying cannot help
            self._fail(msg, job_id, e.code, ack=True)
            outcome = "failed"
        except PdfLimitError as e:  # retrying the same PDF cannot help: fail once, with a code the page explains
            self._fail(msg, job_id, e.code, ack=True)
            outcome = "failed"
        except LostMessage:
            outcome = "lost"
        except Exception as e:
            log.error("job %s crashed: %s", job_id, type(e).__name__)
            final = msg.receive_count >= self.max_attempts
            self._fail(msg, job_id, f"ERR_{type(e).__name__}"[:40], ack=final)
            outcome = "failed" if final else "retry"
        finally:
            halt.set()
            if beat.is_alive():
                beat.join(timeout=5)
        return outcome

    def _run(self, msg: Message, job_id: str, rec: JobRecord, work: Path) -> str:
        def emit(line: str) -> None:  # stop only right after a billed call was written to the ledger
            if line.startswith("BILLED") and (self.stop.is_set() or self._cancel.is_set() or self._lost.is_set()):
                raise StopRequested

        gw = make_gateway(work, self.profile, adapters=self.adapters() if self.adapters else None)
        try:
            res = run_job(work, gateway=gw, job_id=job_id, batch_size=self.batch_size,
                          approve_batches=self.approve_batches, budget_usd=self.budget, emit=emit)
        except StopRequested:
            self._quiesce()
            self._merge_segments(job_id, work)
            self._push_state(job_id, work)
            if self._lost.is_set():
                return "lost"
            if self._cancel.is_set():
                self._status(job_id, JobState.cancelled, **self._progress(job_id, work))
                self.queue.ack(msg)
                return "cancelled"
            self._status(job_id, JobState.queued, **self._progress(job_id, work))
            self.queue.release(msg)  # graceful stop: someone else resumes from the checkpoint
            return "interrupted"
        self._quiesce()
        prog = self._progress(job_id, work)
        self._merge_segments(job_id, work)
        self._push_state(job_id, work)
        if res.state == "waiting":
            self._status(job_id, JobState.needs_review, **prog)
            self.queue.ack(msg)
            return "waiting"
        if res.state == "budget_stopped":
            self._status(job_id, JobState.failed, error_code="BUDGET", **prog)
            self.queue.ack(msg)
            return "failed"
        return self._finish(msg, job_id, rec, work, final_state=JobState.done, outcome="done")

    def _finish(self, msg, job_id, rec, work, final_state, outcome) -> str:
        segs = self._merge_segments(job_id, work)
        if rec.format != "pdf":
            names, err = self._file_outputs(work, rec, segs)
        else:
            names, err = build_outputs(work, work / "input.pdf", segs)
        for n in names:
            self.store.put_file(job_id, n, work / n)
        self._push_state(job_id, work)
        self._status(job_id, final_state, error_code=err, outputs=sorted(names), **self._progress(job_id, work))
        self.queue.ack(msg)
        return outcome

    @staticmethod
    def _file_outputs(work: Path, rec: JobRecord, segs: list[dict]) -> tuple[list[str], str | None]:
        """Same-format output `out/<name>.vi.<ext>` plus the segments Excel; a rebuild failure keeps the Excel."""
        names = []
        out = work / "out"
        out.mkdir(exist_ok=True)
        segments_xlsx(segs, out / "segments.xlsx")
        names.append("out/segments.xlsx")
        try:
            dest, _rep = build_file_output(work, work / input_name(rec.format), out, name=rec.name)
        except FormatError as e:
            return names, e.code
        except Exception:
            return names, "REBUILD_FAILED"
        return [f"out/{dest.name}", *names], None

    def _fail(self, msg: Message, job_id: str, code: str, ack: bool) -> None:
        if ack:
            self._status(job_id, JobState.failed, error_code=code)
            self.queue.ack(msg)
        else:
            self._status(job_id, JobState.queued, error_code=code)
            try:
                self.queue.release(msg)
            except LostMessage:
                pass
