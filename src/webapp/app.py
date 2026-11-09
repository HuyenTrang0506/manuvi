"""FastAPI app: JSON API + one static page."""
from __future__ import annotations

import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .review import build_review_router
from .pages import HEADERS, render_review_page, static_file
from .store import ConflictError, JobStore  # noqa: F401  (re-exported for callers and tests)


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

    app.include_router(build_review_router(lambda r: store), prefix="/api")

    @app.get("/")
    def index(t: str = ""):
        if not secrets.compare_digest(t, token):
            raise HTTPException(403, "open the URL printed at startup (it contains the token)")
        return render_review_page(token)

    @app.get("/static/{name:path}")
    def static(name: str):
        return static_file(name)

    return app
