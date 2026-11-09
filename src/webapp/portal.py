"""Hosted portal: mandatory login, upload, job list/status, downloads, per-job review.

Security model (all of it also applies behind a reverse proxy):
* every page and API call except /healthz and the login page needs a valid session cookie
  (HttpOnly, SameSite=Strict, Secure on HTTPS);
* Host and Origin must be on the allow-list (MANUVI_ALLOWED_HOSTS);
* state-changing requests must be JSON (or the raw PDF PUT) AND carry the per-session `X-Manuvi-Token`
  header, which a cross-site page cannot set; GET /api calls need that header too;
* safety segments are approved only by the founder, with the single-use nonce flow of the review API;
* X-Forwarded-* headers are honoured only from trusted proxy addresses.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import secrets
import hmac
import tempfile
import threading
import time
from pathlib import Path
from typing import Mapping, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, Field

from src.intake.jobspec import parse_sections
from src.jobs import (OUTPUTS, TERMINAL, JobRecord, JobState, JobStatus, LocalStore, Queue, Store, VersionConflict,
                      valid_job_id)
from src.jobs.model import SECTION_RE, utcnow

from .auth import SESSION_COOKIE, Auth, Session
from .pages import HEADERS, render, render_review_page, static_file
from .review import build_review_router
from .review import STALE_MSG
from .store import JobStore, StaleError

MAX_UPLOAD_DEFAULT_MB = 200
PDF_CT = "application/pdf"
DOWNLOAD_EXT = {"vi_pdf": "pdf", "interleaved_pdf": "pdf", "segments_xlsx": "xlsx", "terms_xlsx": "xlsx"}
XLSX_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class CreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    sections: str = Field(min_length=1, max_length=400)
    page_map: dict[str, int] = Field(default_factory=dict)
    priority_sections: list[str] = Field(default_factory=list, max_length=20)


class LoginBody(BaseModel):
    user: str = Field(max_length=80)
    password: str = Field(max_length=200)


class AccessBody(BaseModel):
    allow: Optional[bool] = None  # reviewer_can_review: read the segments and approve non-safety ones
    read: Optional[bool] = None   # reviewer_can_read: also download the output files


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class PortalAudit:
    """Append-only, hash-chained record of who did what in the portal (no document text, no passwords).

    It lives in the Store (``portal/audit/<seq>.json`` in S3), not on the instance disk, so it survives instance
    replacement. Every event is its own new object, created with "must not exist" semantics, so nothing is ever
    rewritten. ``prev_hash`` is the SHA-256 of the previous object's exact bytes: the chain runs across objects and
    ``verify()`` re-checks it, the sequence numbers and the first link. Bucket versioning (foundation stack) keeps
    old versions if someone with write access ever overwrites an object.
    """

    PREFIX = "audit/"

    def __init__(self, store: Store):
        self.store, self.lock = store, threading.Lock()
        self._seq, self._head = 0, "0" * 64
        self._reload()

    def _keys(self) -> list[str]:
        return [k for k in self.store.global_list(self.PREFIX) if k.endswith(".json")]

    def _reload(self) -> None:
        keys = self._keys()
        if keys:
            self._seq, self._head = int(keys[-1][len(self.PREFIX):-5]), _sha(self.store.global_get(keys[-1]))
        else:
            self._seq, self._head = 0, "0" * 64

    def log(self, user: str, action: str, ip: str = "", **extra) -> None:
        with self.lock:
            for _ in range(8):
                seq = self._seq + 1
                rec = {"seq": seq, "ts": utcnow(), "user": user[:80], "action": action, "ip": ip, **extra,
                       "prev_hash": self._head}
                data = json.dumps(rec, ensure_ascii=False).encode("utf-8")
                if self.store.global_put_new(f"{self.PREFIX}{seq:012d}.json", data):
                    self._seq, self._head = seq, _sha(data)
                    return
                self._reload()  # another writer took that number: continue after its record
            raise RuntimeError("portal audit: could not append")

    def tail(self, n: int = 200) -> list[dict]:
        return [json.loads(self.store.global_get(k)) for k in self._keys()[-n:]][::-1]

    def verify(self) -> bool:
        prev, expect = "0" * 64, 1
        for k in self._keys():
            data = self.store.global_get(k)
            rec = json.loads(data)
            if rec.get("prev_hash") != prev or rec.get("seq") != expect or k != f"{self.PREFIX}{expect:012d}.json":
                return False
            prev, expect = _sha(data), expect + 1
        return True


def _rechain(head: bytes, new_lines: list[bytes]) -> bytes:
    """`head` (the current remote audit) + our new records, re-linked so the hash chain stays valid."""
    lines = [l.decode("utf-8") for l in head.splitlines() if l.strip()]
    prev = _sha(lines[-1].encode("utf-8")) if lines else "0" * 64
    out = list(lines)
    for raw in new_lines:
        rec = json.loads(raw)
        rec["prev_hash"] = prev
        line = json.dumps(rec, ensure_ascii=False)
        prev = _sha(line.encode("utf-8"))
        out.append(line)
    return ("\n".join(out) + "\n").encode("utf-8")


class SyncedJobStore(JobStore):
    """JobStore over a local review directory kept in sync with the job's files in the Store.

    Concurrency: every mutation pulls the files with their version tags, changes them and writes back with a
    conditional put (If-Match). If the worker or another web actor wrote in between, the write is refused, the
    whole mutation is re-run on the fresh files (a few tries), and only then reported as a stale write. The
    audit log is merged instead: our new records are re-chained on top of the newest remote log.
    """

    FILES = ("segments.json", "terms.json", "audit.jsonl")
    TRIES = 4

    def __init__(self, jobdir: Path, store: Store, job_id: str, ttl: float = 2.0):
        self.remote, self.job_id, self.ttl, self._last, self._depth = store, job_id, ttl, 0.0, 0
        self._etag: dict[str, Optional[str]] = {}
        self._base: dict[str, Optional[bytes]] = {}
        self._pull_lock = threading.Lock()  # the page fires several GETs at once; they must not share a tmp file
        self.dir = Path(jobdir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._pull(force=True)
        super().__init__(jobdir)

    def _pull(self, force: bool = False) -> None:
        if self._depth > 0:
            return  # inside a mutation: never replace the files it is changing
        if not force and time.monotonic() - self._last < self.ttl:
            return
        with self._pull_lock:
            if not force and time.monotonic() - self._last < self.ttl:
                return  # another thread just pulled
            for n in self.FILES:
                try:
                    data, tag = self.remote.get_versioned(self.job_id, n)
                except FileNotFoundError:
                    self._etag[n], self._base[n] = None, None
                    continue
                p = self.dir / n
                fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".pull-")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                os.replace(tmp, p)
                self._etag[n], self._base[n] = tag, data
            self._last = time.monotonic()

    def _push(self) -> None:
        """Write back what changed. Raises VersionConflict when the remote file moved since the pull."""
        for n in ("segments.json", "terms.json"):
            p = self.dir / n
            if p.exists() and p.read_bytes() != self._base.get(n):
                self.remote.put_if_match(self.job_id, n, p.read_bytes(), self._etag.get(n))
        self._push_audit()
        self._last = 0.0  # tags are stale now: the next read pulls again

    def _push_audit(self) -> None:
        p = self.dir / "audit.jsonl"
        if not p.exists() or p.read_bytes() == self._base.get("audit.jsonl"):
            return
        data, tag = p.read_bytes(), self._etag.get("audit.jsonl")
        base = self._base.get("audit.jsonl") or b""
        mine = [l for l in data.splitlines() if l.strip()][len([l for l in base.splitlines() if l.strip()]):]
        for _ in range(self.TRIES):
            try:
                self.remote.put_if_match(self.job_id, "audit.jsonl", data, tag)
                p.write_bytes(data)
                return
            except VersionConflict:
                try:
                    head, tag = self.remote.get_versioned(self.job_id, "audit.jsonl")
                except FileNotFoundError:
                    head, tag = b"", None
                data = _rechain(head, mine)
        raise VersionConflict("audit.jsonl")

    def _load_segments(self):
        self._pull()
        return super()._load_segments()

    def _load_terms(self):
        self._pull()
        return super()._load_terms()

    def _mutating(name):  # noqa: N805
        base = getattr(JobStore, name)

        def wrapper(self, *a, **k):
            with self.lock:
                if self._depth:  # nested call (bulk_approve -> decide): the outer call pulls and pushes
                    return base(self, *a, **k)
                for _ in range(self.TRIES):
                    self._pull(force=True)
                    self._depth += 1
                    try:
                        result = base(self, *a, **k)
                    finally:
                        self._depth -= 1
                    try:
                        self._push()
                    except VersionConflict:
                        continue  # someone wrote first: redo this change on the fresh files
                    return result
                raise StaleError(STALE_MSG)
        wrapper.__name__ = name
        return wrapper

    edit = _mutating("edit")
    decide = _mutating("decide")
    comment = _mutating("comment")
    bulk_approve = _mutating("bulk_approve")
    decide_term = _mutating("decide_term")
    del _mutating

    def status(self) -> dict:
        s = super().status()
        st = self.remote.get_status(self.job_id)
        return s | {"cost_usd": st.cost_usd, "profile": os.environ.get("MANUVI_PROFILE", "qa")}


def _networks(spec: str):
    out = []
    for item in (x.strip() for x in spec.split(",") if x.strip()):
        try:
            out.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            pass
    return out


def create_portal_app(*, auth: Auth, store: Store, queue: Queue, data_dir: str | Path,
                      allowed_hosts: set[str], secure_cookies: Optional[bool] = None,
                      trusted_proxies: str = "127.0.0.1/32,::1/128", max_upload_mb: int = MAX_UPLOAD_DEFAULT_MB,
                      trust_cf: bool = False) -> FastAPI:
    data_dir = Path(data_dir)
    audit = PortalAudit(store)  # in the Store (S3), so it outlives this instance
    audit_key = hashlib.sha256(b"manuvi-audit|" + "|".join(sorted(u.hash for u in auth.users.values())).encode()).digest()
    proxies = _networks(trusted_proxies)
    hosts = {h.lower() for h in allowed_hosts}
    origins = {f"{sch}://{h}" for h in hosts for sch in ("http", "https")}
    max_bytes = max_upload_mb * 1024 * 1024
    app = FastAPI(title="Manuvi", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.auth, app.state.store, app.state.queue, app.state.audit = auth, store, queue, audit
    review_stores: dict[str, SyncedJobStore] = {}
    review_lock = threading.Lock()

    # ---- request helpers
    def from_proxy(request: Request) -> bool:
        try:
            ip = ipaddress.ip_address(request.client.host if request.client else "")
        except ValueError:
            return False
        return any(ip in n for n in proxies)

    def client_ip(request: Request) -> str:
        """Address used for rate limiting and the audit log. Headers count only when the direct peer is a trusted
        proxy; CF-Connecting-IP additionally needs MANUVI_TRUST_CF=1 (the peer is then the tunnel's network)."""
        peer = request.client.host if request.client else "?"
        if from_proxy(request):
            if trust_cf:
                try:
                    return str(ipaddress.ip_address(request.headers.get("cf-connecting-ip", "").strip()))
                except ValueError:
                    pass  # absent or malformed: fall through to the proxy's own view
            xff = request.headers.get("x-forwarded-for", "")
            if xff:
                return xff.split(",")[-1].strip()[:45]  # the address the proxy itself saw
        return peer

    def is_https(request: Request) -> bool:
        if secure_cookies is not None:
            return secure_cookies
        return from_proxy(request) and request.headers.get("x-forwarded-proto", "") == "https"

    def session_of(request: Request) -> Optional[Session]:
        return auth.get(request.cookies.get(SESSION_COOKIE))

    def need(request: Request) -> Session:
        s = request.state.session
        if s is None:
            raise HTTPException(401, "sign in required")
        return s

    def founder(request: Request) -> Session:
        s = need(request)
        if s.role != "founder":
            raise HTTPException(403, "founder only")
        return s

    def job_or_404(job_id: str) -> tuple[JobRecord, JobStatus]:
        try:
            valid_job_id(job_id)
            rec = store.get_job(job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "job not found")
        if not rec.submitted:
            raise HTTPException(404, "job not found")
        return rec, store.get_status(job_id)

    def user_tag(raw: str) -> str:
        """Failed-login audit entries never hold the raw user field (it may be a mistyped password)."""
        return "~" + hmac.new(audit_key, raw.strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()[:12]

    def view(rec: JobRecord, st: JobStatus) -> dict:
        eff = JobState.cancelled if store.is_cancelled(rec.id) and st.state not in TERMINAL else st.state
        return {"id": rec.id, "name": rec.name, "owner": rec.owner, "created_at": rec.created_at,
                "sections": rec.sections, "state": eff.value, "progress": st.progress, "pages": st.pages,
                "chunks": st.chunks, "pending": st.pending, "by_status": st.by_status, "calls": st.calls,
                "cost_usd": st.cost_usd, "error_code": st.error_code, "attempts": st.attempts,
                "updated_at": st.updated_at, "page_states": st.page_states, "section_progress": st.sections,
                "priority_sections": rec.priority_sections,
                "outputs": {k: (v in st.outputs) for k, v in OUTPUTS.items()},
                "reviewer_can_review": rec.reviewer_can_review, "reviewer_can_read": rec.reviewer_can_read}

    # ---- security middleware
    @app.middleware("http")
    async def guard(request: Request, call_next):
        path = request.url.path

        def deny(msg, code=403):
            return JSONResponse({"detail": msg}, status_code=code, headers=HEADERS)

        if path in ("/healthz", "/internal/tls-ask"):  # probes from the container network, no Host / session
            return await call_next(request)
        if request.headers.get("host", "").lower() not in hosts:
            return deny("bad Host header")
        origin = request.headers.get("origin")
        if origin is not None and origin.lower() not in origins:
            return deny("bad Origin")
        sess = session_of(request)
        request.state.session = sess
        is_api = path.startswith("/api/")
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            ct = request.headers.get("content-type", "").split(";")[0].strip().lower()
            is_pdf_put = request.method == "PUT" and path.startswith("/api/jobs/") and path.endswith("/pdf")
            if ct != (PDF_CT if is_pdf_put else "application/json"):
                return deny("Content-Type must be " + (PDF_CT if is_pdf_put else "application/json"))
            if path != "/api/login":
                if sess is None:
                    return deny("sign in required", 401)
                if not secrets.compare_digest(request.headers.get("x-manuvi-token", ""), sess.csrf):
                    return deny("missing or bad token")
        elif is_api:
            if sess is None:
                return deny("sign in required", 401)
            if not secrets.compare_digest(request.headers.get("x-manuvi-token", ""), sess.csrf):
                return deny("missing or bad token")
        resp = await call_next(request)
        for k, v in HEADERS.items():
            if not (k == "Cache-Control" and path.startswith("/static/")):
                resp.headers.setdefault(k, v)
        if is_https(request):
            resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        return resp

    # ---- public: health, static, login page, login/logout
    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/static/{name:path}")
    def static(name: str):
        return static_file(name)

    @app.get("/internal/tls-ask")
    def tls_ask(request: Request, domain: str = ""):
        """Caddy's on-demand TLS `ask`: issue a certificate only for a name this app serves (MANUVI_ALLOWED_HOSTS).
        Requests that came through the public proxy carry X-Forwarded-*; they get 404, as does the Caddyfile route."""
        if request.headers.get("x-forwarded-for") or request.headers.get("x-forwarded-host"):
            raise HTTPException(404, "not found")
        d = domain.strip().lower().rstrip(".")
        if d and d in {h.rsplit(":", 1)[0] if h.count(":") == 1 else h for h in hosts}:
            return {"ok": True}
        raise HTTPException(404, "not found")

    @app.post("/api/login")
    def login(request: Request, body: LoginBody):
        ip = client_ip(request)
        sid, wait = auth.login(ip, body.user, body.password)
        if sid is None:
            audit.log(user_tag(body.user), "login_locked" if wait else "login_failed", ip,
                      user_len=len(body.user), known=body.user in auth.users)
            if wait:
                return JSONResponse({"detail": "Too many attempts. Try again later.", "retry_after": wait},
                                    status_code=429, headers={"Retry-After": str(wait)})
            return JSONResponse({"detail": "Wrong user name or password."}, status_code=401)
        s = auth.get(sid)
        audit.log(s.user, "login", ip)
        resp = JSONResponse({"user": s.user, "role": s.role, "csrf": s.csrf})
        resp.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="strict", secure=is_https(request),
                        path="/", max_age=12 * 3600)
        return resp

    @app.post("/api/logout")
    def logout(request: Request):
        s = need(request)
        auth.logout(request.cookies.get(SESSION_COOKIE))
        audit.log(s.user, "logout", client_ip(request))
        resp = JSONResponse({"ok": True})
        resp.delete_cookie(SESSION_COOKIE, path="/")
        return resp

    @app.get("/api/me")
    def me(request: Request):
        s = need(request)
        return {"user": s.user, "role": s.role, "csrf": s.csrf}

    @app.get("/")
    def home(request: Request):
        s = request.state.session
        if s is None:
            return render("login.html", {})
        return render("portal.html", {"token": s.csrf, "user": s.user, "role": s.role, "maxMb": max_upload_mb})

    # ---- jobs
    @app.get("/api/jobs")
    def list_jobs(request: Request):
        need(request)
        return [view(r, s) for r, s in store.list_jobs() if r.submitted]

    @app.post("/api/jobs")
    def create_job(request: Request, body: CreateBody):
        s = founder(request)
        try:
            secs = parse_sections(body.sections)
        except ValueError as e:
            raise HTTPException(422, str(e))
        if any(not SECTION_RE.match(x.section) for x in secs):
            raise HTTPException(422, "Section names may use letters, digits, - and _ (max 16 characters).")
        names = {x.section for x in secs}
        if any(k not in names or v < 1 or v > 100000 for k, v in body.page_map.items()):
            raise HTTPException(422, "Page map must refer to listed sections and PDF pages from 1.")
        if any(x not in names for x in body.priority_sections):
            raise HTTPException(422, "Priority sections must be among the listed sections.")
        jid = secrets.token_hex(8)
        store.put_job(JobRecord(id=jid, name=" ".join(body.name.split()), owner=s.user, sections=body.sections,
                                page_map=body.page_map, priority_sections=list(dict.fromkeys(body.priority_sections))))
        audit.log(s.user, "job_create", client_ip(request), job=jid)
        return {"id": jid}

    @app.put("/api/jobs/{job_id}/pdf")
    async def upload_pdf(request: Request, job_id: str):
        s = founder(request)
        try:
            valid_job_id(job_id)
            rec = store.get_job(job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "job not found")
        if rec.submitted or rec.owner != s.user:
            raise HTTPException(409, "this job already has its PDF")
        try:
            declared = int(request.headers.get("content-length", "0"))
        except ValueError:
            raise HTTPException(400, "bad Content-Length")
        if declared > max_bytes:
            raise HTTPException(413, f"PDF is larger than {max_upload_mb} MB.")
        size, head = 0, b""
        with tempfile.NamedTemporaryFile(dir=data_dir, prefix=".upload-", delete=True) as tmp:
            async for chunk in request.stream():
                size += len(chunk)
                if size > max_bytes:
                    raise HTTPException(413, f"PDF is larger than {max_upload_mb} MB.")
                if len(head) < 1024:
                    head += chunk[:1024]
                tmp.write(chunk)
            tmp.flush()
            if size == 0 or b"%PDF-" not in head[:1024] or not head.startswith(b"%PDF-"):
                raise HTTPException(415, "This file is not a PDF.")
            store.put_file(job_id, "input.pdf", Path(tmp.name))
        store.put_job(rec.model_copy(update={"pdf_bytes": size}))
        audit.log(s.user, "upload", client_ip(request), job=job_id, bytes=size)
        return {"id": job_id, "bytes": size}

    @app.post("/api/jobs/{job_id}/submit")
    def submit(request: Request, job_id: str):
        s = founder(request)
        try:
            valid_job_id(job_id)
            rec = store.get_job(job_id)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "job not found")
        if rec.submitted:
            raise HTTPException(409, "already submitted")
        if not store.exists(job_id, "input.pdf"):
            raise HTTPException(409, "upload the PDF first")
        store.put_job(rec.model_copy(update={"submitted": True}))
        store.put_status(job_id, JobStatus(state=JobState.queued))
        queue.enqueue(job_id)
        audit.log(s.user, "submit", client_ip(request), job=job_id)
        return {"id": job_id, "state": "queued"}

    @app.get("/api/jobs/{job_id}")
    def job_detail(request: Request, job_id: str):
        need(request)
        rec, st = job_or_404(job_id)
        return view(rec, st)

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(request: Request, job_id: str):
        s = founder(request)
        rec, st = job_or_404(job_id)
        if st.state in TERMINAL:
            raise HTTPException(409, f"job is already {st.state.value}")
        store.request_cancel(job_id, s.user)
        audit.log(s.user, "cancel", client_ip(request), job=job_id)
        return {"id": job_id, "state": "cancelled"}

    @app.post("/api/jobs/{job_id}/resume")
    def resume(request: Request, job_id: str):
        """Continue a job that waits for human approval (safety gates), or rebuild the outputs of a finished
        job from the current review decisions."""
        s = founder(request)
        rec, st = job_or_404(job_id)
        if store.is_cancelled(job_id):
            raise HTTPException(409, "job was cancelled")
        if st.state == JobState.needs_review:
            queue.enqueue(job_id)
            what = "resume"
        elif st.state == JobState.done:
            store.put_bytes(job_id, "rebuild.json", json.dumps({"by": s.user, "at": utcnow()}).encode())
            queue.enqueue(job_id)
            what = "rebuild"
        else:
            raise HTTPException(409, f"nothing to resume while the job is {st.state.value}")
        audit.log(s.user, what, client_ip(request), job=job_id)
        return {"id": job_id, "action": what}

    @app.post("/api/jobs/{job_id}/reviewer-access")
    def reviewer_access(request: Request, job_id: str, body: AccessBody):
        s = founder(request)
        rec, _ = job_or_404(job_id)
        upd = {k: v for k, v in (("reviewer_can_review", body.allow), ("reviewer_can_read", body.read)) if v is not None}
        if body.allow is False and body.read is None:
            upd["reviewer_can_read"] = False  # taking review away also takes the downloads away
        new = rec.model_copy(update=upd)
        store.put_job(new)
        audit.log(s.user, "reviewer_access", client_ip(request), job=job_id, allow=new.reviewer_can_review,
                  read=new.reviewer_can_read)
        return {"reviewer_can_review": new.reviewer_can_review, "reviewer_can_read": new.reviewer_can_read}

    @app.get("/api/events")
    def events(request: Request, limit: int = 200):
        founder(request)
        return audit.tail(min(max(limit, 1), 1000))

    @app.get("/api/events/verify")
    def events_verify(request: Request):
        founder(request)
        return {"chain_ok": audit.verify()}

    # ---- downloads (cookie auth; plain navigation, no custom header possible)
    @app.get("/download/{job_id}/{kind}")
    def download(request: Request, job_id: str, kind: str):
        s = request.state.session
        if s is None:
            raise HTTPException(401, "sign in required")
        rec, st = job_or_404(job_id)
        if s.role != "founder" and not rec.reviewer_can_read:
            raise HTTPException(403, "the founder has not allowed downloads for the reviewer role")
        if kind not in OUTPUTS or OUTPUTS[kind] not in st.outputs:
            raise HTTPException(404, "not available yet")
        audit.log(s.user, "download", client_ip(request), job=job_id, kind=kind)
        fname = f"manuvi-{job_id}-{kind.replace('_pdf', '').replace('_xlsx', '')}.{DOWNLOAD_EXT[kind]}"
        url = store.download_url(job_id, OUTPUTS[kind], fname, expires=300)
        if url:
            return RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
        path = store.local_path(job_id, OUTPUTS[kind])  # LocalStore
        return FileResponse(path, media_type=PDF_CT if DOWNLOAD_EXT[kind] == "pdf" else XLSX_CT, filename=fname,
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    # ---- review of one job
    def review_store(request: Request) -> JobStore:
        job_id = request.path_params["job_id"]
        rec, _ = job_or_404(job_id)
        s = need(request)
        if s.role != "founder" and not rec.reviewer_can_review:  # reading segments needs the same grant as reviewing
            raise HTTPException(403, "the founder has not enabled review for the reviewer role")
        with review_lock:
            rs = review_stores.get(job_id)
            if rs is None:
                if not store.exists(job_id, "segments.json"):
                    raise HTTPException(404, "nothing to review yet")
                rs = review_stores[job_id] = SyncedJobStore(data_dir / "review" / job_id, store, job_id)
        return rs

    def authorize(request: Request, action: str, seg: Optional[dict]) -> None:
        s = need(request)
        if s.role == "founder":
            return
        rec, _ = job_or_404(request.path_params["job_id"])
        if action == "comment":
            if rec.reviewer_can_review:
                return
        elif action in ("approve", "flag", "bulk") and rec.reviewer_can_review \
                and not (seg is not None and seg.get("kind") == "signal"):
            return
        raise HTTPException(403, "your role cannot do this" if rec.reviewer_can_review
                            else "the founder has not enabled review for the reviewer role")

    router = build_review_router(review_store, lambda r: r.state.session.user, authorize)
    app.include_router(router, prefix="/api/jobs/{job_id}")

    @app.get("/review/{job_id}")
    def review_page(request: Request, job_id: str):
        s = request.state.session
        if s is None:
            return RedirectResponse("/", status_code=303)
        rec, _ = job_or_404(job_id)
        if s.role != "founder" and not rec.reviewer_can_review:
            raise HTTPException(403, "the founder has not enabled review for the reviewer role")
        return render_review_page(s.csrf, prefix=f"/api/jobs/{job_id}", role=s.role, user=s.user, job_id=job_id,
                                  can_review=True)

    return app
