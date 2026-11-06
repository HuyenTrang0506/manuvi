"""FastAPI app: JSON API + one static page."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from .store import ConflictError, JobStore

STATIC = Path(__file__).parent / "static"


class EditBody(BaseModel):
    target: str


class DecideBody(BaseModel):
    action: str  # approve | reject | needs_review
    explicit: bool = False  # set only by an individual human click


class BulkBody(BaseModel):
    ids: list[str]


class TermBody(BaseModel):
    action: str  # approve | reject | edit
    vi: str | None = None


def create_app(jobdir: str | Path) -> FastAPI:
    store = JobStore(jobdir)
    app = FastAPI(title="Manuvi review")
    app.state.store = store

    def seg_or_404(sid):
        try:
            return store.get(sid)
        except KeyError:
            raise HTTPException(404, "segment not found")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")

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
        return s | {"prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None}

    @app.put("/api/segments/{sid}")
    def edit(sid: str, body: EditBody):
        seg_or_404(sid)
        return store.edit(sid, body.target)

    @app.post("/api/segments/{sid}/decision")
    def decide(sid: str, body: DecideBody):
        seg_or_404(sid)
        try:
            return store.decide(sid, body.action, explicit=body.explicit)
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
