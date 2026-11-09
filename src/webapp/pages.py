"""Static pages and assets. Pages carry their config as an inert JSON block (no inline script), so a strict
Content-Security-Policy (`script-src 'self'`) holds on every page."""
from __future__ import annotations

import html
import json
from pathlib import Path

from fastapi import HTTPException
from fastapi.responses import FileResponse, HTMLResponse

STATIC = Path(__file__).parent / "static"
ASSET_TYPES = {".css": "text/css", ".js": "text/javascript", ".woff2": "font/woff2", ".svg": "image/svg+xml"}
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; font-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'")
HEADERS = {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer", "Content-Security-Policy": CSP}


def static_file(name: str) -> FileResponse:
    p = (STATIC / name).resolve()
    if p.suffix not in ASSET_TYPES or not p.is_file() or not p.is_relative_to(STATIC.resolve()) \
            or p.name in ("index.html", "login.html", "portal.html"):
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type=ASSET_TYPES[p.suffix],
                        headers={"Cache-Control": "public, max-age=3600", "X-Content-Type-Options": "nosniff"})


def _cfg(d: dict) -> str:
    return json.dumps(d).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render(template: str, cfg: dict, title: str = "") -> HTMLResponse:
    text = (STATIC / template).read_text("utf-8").replace("__MANUVI_CONFIG__", _cfg(cfg))
    return HTMLResponse(text, headers=HEADERS)


def render_review_page(token: str, prefix: str = "", role: str = "founder", user: str = "",
                       job_id: str = "", can_review: bool = True) -> HTMLResponse:
    """The review UI. prefix = API prefix of the job ('' for the local single-job app)."""
    return render("index.html", {"token": token, "prefix": prefix, "role": role, "user": user, "jobId": job_id,
                                 "canReview": can_review, "portal": bool(job_id)})


def esc(s: str) -> str:
    return html.escape(s, quote=True)
