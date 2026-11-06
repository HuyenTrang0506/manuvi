"""FastAPI app: JSON API + one static page."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .store import ConflictError, JobStore

STATIC = Path(__file__).parent / "static"


class EditBody(BaseModel):
    target: str


class DecideBody(BaseModel):
    action: str  # approve | reject | needs_review
    nonce: str | None = None  # single-use, issued when a signal segment is fetched individually


class BulkBody(BaseModel):
    ids: list[str]


class TermBody(BaseModel):
    action: str  # approve | reject | edit
    vi: str | None = None


def create_app(jobdir: str | Path, port: int = 8765, token: str | None = None) -> FastAPI:
    store = JobStore(jobdir)
    token = token or secrets.token_urlsafe(24)
    hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
    origins = {f"http://{h}" for h in hosts}
    app = FastAPI(title="Manuvi review")
    app.state.store = store
    app.state.token = token

    @app.middleware("http")
    async def guard(request: Request, call_next):
        def deny(msg):
            return JSONResponse({"detail": msg}, status_code=403)
        if request.headers.get("host", "") not in hosts:
            return deny("bad Host header")
        origin = request.headers.get("origin")
        if origin is not None and origin not in origins:
            return deny("bad Origin")
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json":
                return deny("Content-Type must be application/json")
            if not secrets.compare_digest(request.headers.get("x-manuvi-token", ""), token):
                return deny("missing or bad token")
        elif request.url.path.startswith("/api/") and not secrets.compare_digest(
                request.headers.get("x-manuvi-token", ""), token):
            return deny("missing or bad token")
        return await call_next(request)

    def seg_or_404(sid):
        try:
            return store.get(sid)
        except KeyError:
            raise HTTPException(404, "segment not found")

    @app.get("/")
    def index(t: str = ""):
        if not secrets.compare_digest(t, token):
            raise HTTPException(403, "open the URL printed at startup (it contains the token)")
        html = (STATIC / "index.html").read_text("utf-8").replace("__MANUVI_TOKEN__", token)
        return HTMLResponse(html, headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"})

    @app.get("/api/queue")
    def queue(status: str | None = None, kind: str | None = None, section: str | None = None,
              decision: str | None = None, min_risk: float | None = None, q: str | None = None):
        rows = store.queue(status, kind, section, decision, min_risk, q)
        return [{k: s.get(k) for k in ("id", "page", "section", "status", "kind", "risk", "decision", "issues")}
                | {"source": s["source"][:140], "target": (s.get("target") or "")[:140]} for s in rows]

    @app.get("/api/segments/{sid}")
    def segment(sid: str):
        s = seg_or_404(sid)
        ids = [x["id"] for x in store.queue()]
        i = ids.index(sid)
        extra = {"nonce": store.issue_nonce(sid)} if s["kind"] == "signal" else {}
        return s | extra | {"prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None}

    @app.put("/api/segments/{sid}")
    def edit(sid: str, body: EditBody):
        seg_or_404(sid)
        return store.edit(sid, body.target)

    @app.post("/api/segments/{sid}/decision")
    def decide(sid: str, body: DecideBody):
        seg_or_404(sid)
        try:
            return store.decide(sid, body.action, nonce=body.nonce)
        except ConflictError as e:
            raise HTTPException(409, str(e))
        except ValueError:
            raise HTTPException(422, "bad action")

    @app.post("/api/bulk-approve")
    def bulk(body: BulkBody):
        return store.bulk_approve(body.ids)

    @app.get("/api/sections")
    def sections():
        return sorted({s.get("section") for s in store.segments if s.get("section")})

    @app.get("/api/terms")
    def terms():
        return store.terms

    @app.post("/api/terms/{tid}")
    def term(tid: str, body: TermBody):
        try:
            return store.decide_term(tid, body.action, body.vi)
        except KeyError:
            raise HTTPException(404, "term not found")
        except ValueError:
            raise HTTPException(422, "bad request")

    @app.get("/api/status")
    def status():
        return store.status()

    @app.get("/api/audit")
    def audit(limit: int = 200):
        return store.audit()[-limit:][::-1]

    return app
