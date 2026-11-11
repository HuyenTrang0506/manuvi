"""Page viewer: real rendered pages (PNG, cached), page list with review marks, access rules, Vietnamese strings only."""
import json
import re
from pathlib import Path

import pymupdf
import pytest

from src.jobs import JobRecord, JobState, JobStatus
from src.render.fonts import FontSet
from src.render.page import Segment, render_document
from src.render.style import style_for_box
from src.worker.outputs import write_pages_json

from .test_portal import JID, login, make_env, users, clock  # noqa: F401

STATIC = Path(__file__).resolve().parents[2] / "src" / "webapp" / "static"


@pytest.fixture
def env(tmp_path, users, clock):  # noqa: F811
    return make_env(tmp_path, users, clock)


def make_job(store, tmp_path, with_vi=True, reviewer_read=False):
    src = tmp_path / "input.pdf"
    d = pymupdf.open()
    for i in range(3):
        p = d.new_page(width=300, height=200)
        p.insert_text((30, 60), f"Check the fuse on page {i}", fontsize=10, fontname="helv")
        p.draw_line((20, 80), (280, 80), width=1)
    d.save(str(src))
    d.close()
    segs = []
    with pymupdf.open(str(src)) as doc:
        for i in (0, 2):
            bb = (30.0, 49.0, 170.0, 63.0)
            st = style_for_box(doc[i], bb)
            s = Segment(i, bb, "label", "Check the fuse", "Kiểm tra cầu chì trên trang", rotate=st[1], max_size=st[0].size,
                        style=st[0])
            segs.append(s)
    vi = tmp_path / "vi.pdf"
    results = render_document(src, segs, vi, fontfile=FontSet(), pages=[0, 2])
    layout = {"C1": {"page0": 0, "bbox": [30, 49, 170, 63]}, "C2": {"page0": 2, "bbox": [30, 49, 170, 63]}}
    seg_rows = [{"id": "C1", "status": "needs_review", "kind": "prose"}, {"id": "C2", "status": "ok", "kind": "prose"}]
    spec = {"pages": [{"pdf_page": 1, "label": "43-1"}, {"pdf_page": 2, "label": "43-2", "scanned": True},
                      {"pdf_page": 3, "label": "43-3"}]}
    work = tmp_path / "work"
    (work / "out").mkdir(parents=True)
    write_pages_json(work, src, vi, spec, [0, 2], seg_rows, layout, segs, results)
    store.put_job(JobRecord(id=JID, name="Tài liệu thử", owner="huyen", sections="43:1-3", submitted=True,
                            reviewer_can_read=reviewer_read))
    store.put_file(JID, "input.pdf", src)
    outs = ["out/pages.json"]
    store.put_file(JID, "out/pages.json", work / "out" / "pages.json")
    if with_vi:
        store.put_file(JID, "out/vi.pdf", vi)
        outs.append("out/vi.pdf")
    store.put_status(JID, JobStatus(state=JobState.done, outputs=outs))


def test_pages_json_has_labels_marks_and_no_text(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path)
    f = login(app)
    data = f.get(f"/api/jobs/{JID}/viewer").json()
    pg = {p["label"]: p for p in data["pages"]}
    assert list(pg) == ["43-1", "43-2", "43-3"]
    assert pg["43-1"]["vi_index"] == 0 and pg["43-3"]["vi_index"] == 1 and pg["43-2"]["vi_index"] is None
    assert pg["43-1"]["review"] and pg["43-1"]["marks"][0]["t"] == "text" and not pg["43-3"]["review"]
    assert pg["43-2"]["scanned"]
    assert "Kiểm tra" not in json.dumps(data) and "fuse" not in json.dumps(data)   # geometry only, never page text


def test_png_is_the_real_page_and_cached(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path)
    f = login(app)
    o = f.get(f"/pages/{JID}/orig/0.png?z=1.5")
    v = f.get(f"/pages/{JID}/vi/0.png?z=1.5")
    assert o.status_code == v.status_code == 200 and o.headers["content-type"] == "image/png"
    assert o.content[:8] == b"\x89PNG\r\n\x1a\n" and o.content != v.content
    pix = pymupdf.Pixmap(o.content)
    assert (pix.width, pix.height) == (450, 300)                    # 300x200 pt at zoom 1.5
    cached = list((tmp_path / "data" / "viewer" / JID).rglob("*.png"))
    assert len(cached) == 2
    assert f.get(f"/pages/{JID}/orig/0.png?z=1.5").content == o.content
    assert f.get(f"/pages/{JID}/vi/1.png?z=2").status_code == 200


def test_viewer_input_validation(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path)
    f = login(app)
    for url, code in ((f"/pages/{JID}/orig/9.png?z=1.5", 404), (f"/pages/{JID}/orig/0.png?z=7", 400),
                      (f"/pages/{JID}/secret/0.png", 404), (f"/pages/{JID}/orig/-1.png", 404),
                      (f"/pages/{JID}/orig/0.jpg", 404), (f"/pages/../orig/0.png", 404)):
        assert f.get(url).status_code in (code, 404), url
    assert f.get(f"/pages/{JID}/orig/0.png?z=7").status_code == 400
    assert f.get(f"/pages/{JID}/orig/9.png?z=1.5").status_code == 404


def test_access_rules(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path)
    from fastapi.testclient import TestClient
    anon = TestClient(app, base_url="http://testserver")
    assert anon.get(f"/pages/{JID}/orig/0.png").status_code in (401, 403)
    assert anon.get(f"/api/jobs/{JID}/viewer").status_code in (401, 403)
    assert anon.get(f"/view/{JID}", follow_redirects=False).status_code == 303
    r = login(app, "rev", "reviewer-secret-1")
    assert r.get(f"/api/jobs/{JID}/viewer").status_code == 403         # needs the founder's read grant
    assert r.get(f"/pages/{JID}/orig/0.png").status_code == 403
    f = login(app)
    assert f.post(f"/api/jobs/{JID}/reviewer-access", json={"read": True}).status_code == 200
    assert r.get(f"/api/jobs/{JID}/viewer").status_code == 200


def test_not_ready_and_missing_vi(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path, with_vi=False)
    f = login(app)
    assert f.get(f"/pages/{JID}/vi/0.png").status_code == 404
    assert f.get(f"/pages/{JID}/orig/0.png").status_code == 200
    store.put_status(JID, JobStatus(state=JobState.running, outputs=[]))
    r = f.get(f"/api/jobs/{JID}/viewer")
    assert r.status_code == 404 and "Chưa có trang" in r.json()["detail"]


def test_view_page_and_vietnamese_only(env, tmp_path):
    app, store, _ = env
    make_job(store, tmp_path)
    f = login(app)
    r = f.get(f"/view/{JID}")
    assert r.status_code == 200 and 'lang="vi"' in r.text and "Content-Security-Policy" in r.headers
    assert f.get("/static/viewer.html").status_code == 404           # templates are served only through their route
    assert f.get("/static/viewer.js").status_code == 200
    html = (STATIC / "viewer.html").read_text("utf-8")
    js = (STATIC / "viewer.js").read_text("utf-8")
    visible = re.sub(r"<(script|style|svg)[\s\S]*?</\1>", "", html)
    text = " ".join(re.findall(r">([^<>]+)<", visible)) + " " + " ".join(re.findall(r'(?:aria-label|placeholder|title)="([^"]+)"', html))
    strings = [(m.group(0)[1:-1], "") for m in re.finditer(r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\"|`(?:[^`\\]|\\.)*`", js)]
    words = re.findall(r"[A-Za-z]{4,}", text)
    english = {"the", "page", "image", "with", "from", "this", "view", "diff"}
    assert not [w for w in words if w.lower() in english], words
    for a, b in strings:
        s = a or b
        if re.search(r"\b(the|your|please|sign in|loading|cannot|failed|request)\b", s, re.I):
            pytest.fail("English UI string in viewer.js: " + s)
    for banned in ("lorem", "placeholder data", "demo", "sample data", "mock"):
        assert banned not in (html + js).lower()
