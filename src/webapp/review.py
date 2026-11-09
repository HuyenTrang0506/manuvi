"""Review API (queue, segments, decisions, terms, audit) as a router.

The same routes serve the single-job local app (`create_app`) and every job in the hosted portal.
`resolve(request)` returns the JobStore, `actor(request)` the user name for the audit log and
`authorize(request, action, segment)` raises HTTPException(403) when the role may not do `action`.
"""
from __future__ import annotations

from typing import Callable, Optional

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .store import ConflictError, JobStore


class EditBody(BaseModel):
    target: str


class DecideBody(BaseModel):
    action: str  # approve | reject | needs_review
    nonce: Optional[str] = None  # single-use, issued when a signal segment is fetched individually


class BulkBody(BaseModel):
    ids: list[str]


class TermBody(BaseModel):
    action: str  # approve | reject | edit
    vi: Optional[str] = None


class CommentBody(BaseModel):
    text: str = Field(max_length=500)


def build_review_router(resolve: Callable[[Request], JobStore],
                        actor: Callable[[Request], Optional[str]] = lambda r: None,
                        authorize: Callable[[Request, str, Optional[dict]], None] = lambda r, a, s: None
                        ) -> APIRouter:
    router = APIRouter()

    def seg_or_404(request, sid):
        try:
            return resolve(request).get(sid)
        except KeyError:
            raise HTTPException(404, "segment not found")

    @router.get("/queue")
    def queue(request: Request, status: str | None = None, kind: str | None = None, section: str | None = None,
              decision: str | None = None, min_risk: float | None = None, q: str | None = None):
        rows = resolve(request).queue(status, kind, section, decision, min_risk, q)
        return [{k: s.get(k) for k in ("id", "page", "section", "status", "kind", "risk", "decision", "issues")}
                | {"source": s["source"][:140], "target": (s.get("target") or "")[:140]} for s in rows]

    @router.get("/segments/{sid}")
    def segment(request: Request, sid: str):
        store = resolve(request)
        s = seg_or_404(request, sid)
        ids = [x["id"] for x in store.queue()]
        i = ids.index(sid)
        # only a role that may approve safety segments gets a confirm nonce
        can_sign = True
        try:
            authorize(request, "approve", s)
        except HTTPException:
            can_sign = False
        extra = {"nonce": store.issue_nonce(sid)} if s["kind"] == "signal" and can_sign else {}
        return s | extra | {"prev": ids[i - 1] if i > 0 else None, "next": ids[i + 1] if i + 1 < len(ids) else None}

    @router.put("/segments/{sid}")
    def edit(request: Request, sid: str, body: EditBody):
        s = seg_or_404(request, sid)
        authorize(request, "edit", s)
        return resolve(request).edit(sid, body.target, actor=actor(request))

    @router.post("/segments/{sid}/decision")
    def decide(request: Request, sid: str, body: DecideBody):
        s = seg_or_404(request, sid)
        authorize(request, {"approve": "approve", "needs_review": "flag"}.get(body.action, "reject"), s)
        try:
            return resolve(request).decide(sid, body.action, nonce=body.nonce, actor=actor(request))
        except ConflictError as e:
            raise HTTPException(409, str(e))
        except ValueError:
            raise HTTPException(422, "bad action")

    @router.post("/segments/{sid}/comment")
    def comment(request: Request, sid: str, body: CommentBody):
        s = seg_or_404(request, sid)
        authorize(request, "comment", s)
        try:
            return resolve(request).comment(sid, body.text, actor=actor(request))
        except ValueError:
            raise HTTPException(422, "empty comment")

    @router.post("/bulk-approve")
    def bulk(request: Request, body: BulkBody):
        authorize(request, "bulk", None)
        return resolve(request).bulk_approve(body.ids, actor=actor(request))

    @router.get("/sections")
    def sections(request: Request):
        return sorted({s.get("section") for s in resolve(request).segments if s.get("section")})

    @router.get("/terms")
    def terms(request: Request):
        return resolve(request).terms

    @router.post("/terms/{tid}")
    def term(request: Request, tid: str, body: TermBody):
        authorize(request, "term", None)
        try:
            return resolve(request).decide_term(tid, body.action, body.vi, actor=actor(request))
        except KeyError:
            raise HTTPException(404, "term not found")
        except ValueError:
            raise HTTPException(422, "bad request")

    @router.get("/status")
    def status(request: Request):
        return resolve(request).status()

    @router.get("/audit")
    def audit(request: Request, limit: int = 200):
        return resolve(request).audit()[-limit:][::-1]

    return router
