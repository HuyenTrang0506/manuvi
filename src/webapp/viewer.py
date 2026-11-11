"""Page viewer: the real original page and the real Vietnamese page, rendered to PNG on the server and cached.

Nothing here is demo data. Pages come from the job's own files (`input.pdf`, `out/vi.pdf`, `out/pages.json`).
All messages the viewer shows are Vietnamese."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Callable

import pymupdf
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response

from src.jobs import JobRecord, JobStatus, Store

from .pages import render

ZOOMS = (1.0, 1.5, 2.0, 3.0)
MAX_PIXELS = 36_000_000
WHICH = {"orig": "input.pdf", "vi": "out/vi.pdf"}
PNG_HEADERS = {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}
MSG = {
    "nopages": "Chưa có trang để xem. Bản dịch chưa hoàn tất hoặc chưa có trang nào được xử lý.",
    "badpage": "Không có trang này.",
    "badzoom": "Mức phóng đại không hợp lệ.",
    "novi": "Trang này chưa được dịch.",
    "forbidden": "Người quản lý chưa cho phép vai trò của bạn xem và tải bản dịch.",
    "toolarge": "Trang quá lớn để hiển thị ở mức phóng đại này.",
}


class PageCache:
    """PNG cache on local disk, keyed by job, version of the job files, which PDF, page and zoom."""

    def __init__(self, root: Path, store: Store):
        self.root, self.store = Path(root), store
        self.lock = threading.Lock()

    def _dir(self, job_id: str, version: str) -> Path:
        return self.root / job_id / hashlib.sha1(version.encode()).hexdigest()[:10]

    def pdf(self, job_id: str, version: str, name: str) -> Path:
        d = self._dir(job_id, version)
        dest = d / name.replace("/", "_")
        with self.lock:
            if not dest.exists():
                d.mkdir(parents=True, exist_ok=True)
                tmp = dest.with_suffix(".part")
                self.store.get_file(job_id, name, tmp)
                tmp.replace(dest)
        return dest

    def png(self, job_id: str, version: str, which: str, index: int, zoom: float) -> bytes:
        d = self._dir(job_id, version)
        key = d / f"{which}-{index}-{int(zoom * 10)}.png"
        if key.exists():
            return key.read_bytes()
        src = self.pdf(job_id, version, WHICH[which])
        with pymupdf.open(str(src)) as doc:
            if not 0 <= index < len(doc):
                raise IndexError(index)
            page = doc[index]
            if page.rect.width * page.rect.height * zoom * zoom > MAX_PIXELS:
                raise OverflowError("too large")
            data = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("png")
        d.mkdir(parents=True, exist_ok=True)
        tmp = key.with_suffix(".part")
        tmp.write_bytes(data)
        tmp.replace(key)
        return data


def install(app: FastAPI, *, store: Store, data_dir: Path, job_or_404: Callable[[str], tuple[JobRecord, JobStatus]],
            audit=None, client_ip: Callable[[Request], str] = lambda r: "") -> None:
    cache = PageCache(Path(data_dir) / "viewer", store)

    def allowed(request: Request, job_id: str) -> tuple[JobRecord, JobStatus]:
        s = request.state.session
        if s is None:
            raise HTTPException(401, "sign in required")
        rec, st = job_or_404(job_id)
        if s.role != "founder" and not rec.reviewer_can_read:
            raise HTTPException(403, MSG["forbidden"])
        return rec, st

    def page_list(job_id: str, st: JobStatus) -> dict:
        if "out/pages.json" not in st.outputs or not store.exists(job_id, "out/pages.json"):
            raise HTTPException(404, MSG["nopages"])
        return json.loads(store.get_bytes(job_id, "out/pages.json"))

    @app.get("/view/{job_id}")
    def view_page(request: Request, job_id: str):
        s = request.state.session
        if s is None:
            from fastapi.responses import RedirectResponse
            return RedirectResponse("/", status_code=303)
        rec, st = allowed(request, job_id)
        return render("viewer.html", {"token": s.csrf, "user": s.user, "role": s.role, "jobId": job_id,
                                      "name": rec.name, "pdfReady": "out/vi.pdf" in st.outputs})

    @app.get("/api/jobs/{job_id}/viewer")
    def viewer_info(request: Request, job_id: str):
        rec, st = allowed(request, job_id)
        data = page_list(job_id, st)
        data["version"] = hashlib.sha1(st.updated_at.encode()).hexdigest()[:10]
        data["name"] = rec.name
        data["zooms"] = list(ZOOMS)
        return data

    @app.get("/pages/{job_id}/{which}/{index}")
    def page_png(request: Request, job_id: str, which: str, index: str, z: float = 1.5):
        rec, st = allowed(request, job_id)
        if which not in WHICH or not index.endswith(".png") or not index[:-4].isdigit():
            raise HTTPException(404, MSG["badpage"])
        if z not in ZOOMS:
            raise HTTPException(400, MSG["badzoom"])
        if which == "vi" and "out/vi.pdf" not in st.outputs:
            raise HTTPException(404, MSG["novi"])
        try:
            data = cache.png(job_id, st.updated_at, which, int(index[:-4]), z)
        except IndexError:
            raise HTTPException(404, MSG["badpage"])
        except OverflowError:
            raise HTTPException(413, MSG["toolarge"])
        return Response(data, media_type="image/png", headers=PNG_HEADERS)
