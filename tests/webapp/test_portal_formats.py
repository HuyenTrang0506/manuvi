import zipfile

import pytest

from src.jobs import JobState, JobStatus
from tests.formats.fixtures import CT_DOCX, make_docx, make_pptx

from .test_portal import env, login, users, clock, PDF  # noqa: F401  (fixtures)


def new_file_job(c, fmt, **kw):
    r = c.post("/api/jobs", json={"name": "Tài liệu", "format": fmt, **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def put_file(c, jid, data, ct="application/octet-stream"):
    return c.put(f"/api/jobs/{jid}/file", content=data, headers={"Content-Type": ct})


def test_docx_upload_submit_and_download(env, tmp_path):
    app, store, queue = env
    c = login(app)
    data = make_docx(tmp_path / "a.docx").read_bytes()
    jid = new_file_job(c, "docx")                                    # no sections needed
    assert put_file(c, jid, data).json()["bytes"] == len(data)
    assert c.post(f"/api/jobs/{jid}/submit", json={}).json()["state"] == "queued"
    assert store.get_bytes(jid, "input.docx") == data and store.get_job(jid).format == "docx"
    assert queue.receive().job_id == jid
    assert c.get(f"/api/jobs/{jid}").json()["format"] == "docx"
    assert c.get(f"/api/jobs/{jid}").json()["outputs"]["vi_file"] is False
    assert c.get(f"/download/{jid}/vi_file").status_code == 404
    store.put_bytes(jid, "out/Tai_lieu.vi.docx", b"PK-translated")
    store.put_status(jid, JobStatus(state=JobState.done, outputs=["out/Tai_lieu.vi.docx", "out/segments.xlsx"]))
    r = c.get(f"/download/{jid}/vi_file")
    assert r.status_code == 200 and r.content == b"PK-translated"
    assert "Tai_lieu.vi.docx" in r.headers["content-disposition"] and "wordprocessingml" in r.headers["content-type"]
    assert c.get(f"/api/jobs/{jid}").json()["outputs"]["vi_file"] is True


def test_md_and_pptx_accepted(env, tmp_path):
    app, store, _ = env
    c = login(app)
    jid = new_file_job(c, "md")
    assert put_file(c, jid, "# Tiêu đề\n\nText.\n".encode()).status_code == 200
    jid2 = new_file_job(c, "pptx")
    assert put_file(c, jid2, make_pptx(tmp_path / "a.pptx").read_bytes()).status_code == 200
    assert store.exists(jid2, "input.pptx")


@pytest.mark.parametrize("fmt,data,code", [
    ("docx", b"not a zip", 415), ("docx", b"", 415), ("md", b"abc\x00def", 415), ("md", "caf\xe9".encode("latin-1"), 415),
    ("docx", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"0" * 64, 415),
])
def test_bad_files_rejected(env, fmt, data, code):
    app, store, _ = env
    c = login(app)
    jid = new_file_job(c, fmt)
    assert put_file(c, jid, data).status_code == code
    assert not store.exists(jid, f"input.{fmt}")
    assert c.post(f"/api/jobs/{jid}/submit", json={}).status_code == 409


def test_macro_and_bomb_and_wrong_format_rejected(env, tmp_path):
    app, store, _ = env
    c = login(app)
    jid = new_file_job(c, "docx")
    vba = make_docx(tmp_path / "v.docx", extra_entries={"word/vbaProject.bin": b"x"}).read_bytes()
    r = put_file(c, jid, vba)
    assert r.status_code == 415 and "macro" in r.json()["detail"]
    bomb = make_docx(tmp_path / "b.docx", extra_entries={"word/media/z.bin": b"\0" * 20_000_000}).read_bytes()
    assert put_file(c, jid, bomb).status_code == 415
    assert put_file(c, jid, make_pptx(tmp_path / "p.pptx").read_bytes()).status_code == 415     # pptx sent to a docx job
    assert not store.exists(jid, "input.docx")
    assert put_file(c, jid, make_docx(tmp_path / "ok.docx").read_bytes()).status_code == 200


def test_size_limit_and_content_type_and_state_rules(users, clock, tmp_path):
    from .test_portal import make_env
    app, store, _ = make_env(tmp_path, users, clock, max_upload_mb=1)
    c = login(app)
    jid = new_file_job(c, "md")
    assert put_file(c, jid, b"x" * (1024 * 1024 + 10)).status_code == 413
    assert put_file(c, jid, b"# hi", ct="text/plain").status_code == 403
    assert put_file(c, jid, b"# hi", ct="application/pdf").status_code == 403
    pdf_job = c.post("/api/jobs", json={"name": "x", "sections": "23:1-4"}).json()["id"]
    assert put_file(c, pdf_job, b"# hi").status_code == 409            # PDF jobs use the PDF endpoint
    assert c.put(f"/api/jobs/{jid}/pdf", content=PDF, headers={"Content-Type": "application/pdf"}).status_code in (200, 409)
    assert put_file(c, jid, b"# one").status_code == 200
    c.post(f"/api/jobs/{jid}/submit", json={})
    assert put_file(c, jid, b"# two").status_code == 409
    assert c.post("/api/jobs", json={"name": "x", "format": "exe"}).status_code == 422
    assert c.post("/api/jobs", json={"name": "x"}).status_code == 422   # PDF still needs sections


def test_reviewer_cannot_upload_or_download_without_grant(env, tmp_path):
    from .test_portal import PW_R
    app, store, _ = env
    f, rv = login(app), login(app, "rev", PW_R)
    jid = new_file_job(f, "md")
    assert put_file(rv, jid, b"# x").status_code in (401, 403)
    put_file(f, jid, b"# x")
    f.post(f"/api/jobs/{jid}/submit", json={})
    store.put_bytes(jid, "out/a.vi.md", b"# vi")
    store.put_status(jid, JobStatus(state=JobState.done, outputs=["out/a.vi.md"]))
    assert rv.get(f"/download/{jid}/vi_file").status_code == 403
    assert f.get(f"/download/{jid}/vi_file").headers["content-type"].startswith("text/markdown")


def test_portal_js_offers_new_formats():
    from pathlib import Path
    js = (Path(__file__).resolve().parents[2] / "src/webapp/static/portal.js").read_text("utf-8")
    assert ".docx,.pptx,.md" in js and "'file'" in js and "vi_file" in js and "cùng định dạng" in js
