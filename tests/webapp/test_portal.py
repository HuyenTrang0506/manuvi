import importlib.util
import json
import shutil
from pathlib import Path

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from src.jobs import DirQueue, JobRecord, JobState, JobStatus, LocalStore, S3Store
from src.webapp.auth import (Auth, AuthConfigError, RateLimiter, User, hash_password, load_users, verify_password)
from src.webapp.hosted import create_app as hosted_app
from src.webapp.portal import create_portal_app

ROOT = Path(__file__).resolve().parents[2]
HOST = "testserver"
BASE = f"http://{HOST}"
PW_F, PW_R = "founder-secret-1", "reviewer-secret-1"
JID = "abcdef123456"


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


@pytest.fixture(scope="module")
def users():
    return {"huyen": User("huyen", "founder", hash_password(PW_F, log_n=10)),
            "rev": User("rev", "reviewer", hash_password(PW_R, log_n=10))}


@pytest.fixture
def clock():
    return Clock()


def make_env(tmp_path, users, clock, store=None, **kw):
    store = store or LocalStore(tmp_path / "store")
    queue = DirQueue(tmp_path / "q")
    auth = Auth(users, RateLimiter(clock=clock), clock=clock)
    app = create_portal_app(auth=auth, store=store, queue=queue, data_dir=tmp_path / "data",
                            allowed_hosts={HOST}, **kw)
    (tmp_path / "data").mkdir(exist_ok=True)
    return app, store, queue


@pytest.fixture
def env(tmp_path, users, clock):
    return make_env(tmp_path, users, clock)


def login(app, user="huyen", pw=PW_F, **ck):
    c = TestClient(app, base_url=BASE, **ck)
    r = c.post("/api/login", json={"user": user, "password": pw})
    assert r.status_code == 200, r.text
    c.headers["X-Manuvi-Token"] = r.json()["csrf"]
    return c


def seed_job(store, tmp_path, reviewer_ok=False, submitted=True):
    spec = importlib.util.spec_from_file_location("mk", ROOT / "scripts" / "make_demo_job.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.main(str(tmp_path / "demo"))
    store.put_job(JobRecord(id=JID, name="Demo", owner="huyen", sections="23:1-4", submitted=submitted,
                            reviewer_can_review=reviewer_ok))
    for n in ("segments.json", "terms.json"):
        store.put_bytes(JID, n, (tmp_path / "demo" / n).read_bytes())
    store.put_status(JID, JobStatus(state=JobState.needs_review, chunks=40, pending=10))


# ---- authentication
def test_everything_needs_login(env):
    app, store, _ = env
    c = TestClient(app, base_url=BASE)
    page = c.get("/")
    assert page.status_code == 200 and "Sign in" in page.text and "csrf" not in page.text
    for path in ("/api/jobs", "/api/me", "/api/events"):
        assert c.get(path).status_code == 401, path
    assert c.get("/download/abcdef123456/vi_pdf").status_code == 401
    assert c.get(f"/review/{JID}", follow_redirects=False).status_code == 303
    assert c.post("/api/jobs", json={}).status_code == 401
    h = c.get("/healthz")
    assert h.status_code == 200 and h.json() == {"ok": True}


def test_login_cookie_flags_and_logout(env):
    app, *_ = env
    c = TestClient(app, base_url=BASE)
    r = c.post("/api/login", json={"user": "huyen", "password": PW_F})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=strict" in cookie and "secure" not in cookie
    assert r.json()["role"] == "founder" and r.json()["csrf"]
    tok = {"X-Manuvi-Token": r.json()["csrf"]}
    assert c.get("/api/me", headers=tok).json()["user"] == "huyen"
    assert "Manuvi" in c.get("/").text and "Upload" in c.get("/").text
    assert c.post("/api/logout", json={}, headers=tok).status_code == 200
    assert c.get("/api/me", headers=tok).status_code == 401  # session is gone server side


def test_wrong_credentials_do_not_reveal_which_part_was_wrong(env):
    app, *_ = env
    c = TestClient(app, base_url=BASE)
    a = c.post("/api/login", json={"user": "huyen", "password": "nope-nope-nope"})
    b = c.post("/api/login", json={"user": "nobody", "password": "nope-nope-nope"})
    assert a.status_code == b.status_code == 401 and a.json() == b.json()
    assert "set-cookie" not in a.headers


def test_secure_cookie_flag_and_trusted_proxy(tmp_path, users, clock):
    app, *_ = make_env(tmp_path, users, clock, secure_cookies=True)
    r = TestClient(app, base_url=BASE).post("/api/login", json={"user": "huyen", "password": PW_F})
    assert "secure" in r.headers["set-cookie"].lower()
    assert "strict-transport-security" in r.headers
    # behind a proxy: X-Forwarded-Proto is honoured only from a trusted address
    app2, *_ = make_env(tmp_path / "b", users, clock, trusted_proxies="10.0.0.0/8")
    body = {"user": "huyen", "password": PW_F}
    fwd = {"X-Forwarded-Proto": "https"}
    trusted = TestClient(app2, base_url=BASE, client=("10.1.2.3", 5000)).post("/api/login", json=body, headers=fwd)
    assert "secure" in trusted.headers["set-cookie"].lower()
    other = TestClient(app2, base_url=BASE, client=("203.0.113.9", 5000)).post("/api/login", json=body, headers=fwd)
    assert "secure" not in other.headers["set-cookie"].lower()


def test_rate_limit_locks_out_then_recovers(env, clock):
    app, *_ = env
    c = TestClient(app, base_url=BASE)
    for _ in range(5):
        assert c.post("/api/login", json={"user": "huyen", "password": "wrong-wrong"}).status_code == 401
    r = c.post("/api/login", json={"user": "huyen", "password": PW_F})  # right password, still locked
    assert r.status_code == 429 and int(r.headers["retry-after"]) > 0
    # another user from the same address is not blocked by that single-user lock
    assert c.post("/api/login", json={"user": "rev", "password": PW_R}).status_code == 200
    clock.t += 901
    assert c.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 200


def test_rate_limit_is_per_address(env, clock):
    """The hard lock is per address. The soft per-user delay (below) is the only thing another address sees."""
    app, *_ = env
    a = TestClient(app, base_url=BASE, client=("198.51.100.1", 1))
    b = TestClient(app, base_url=BASE, client=("198.51.100.2", 1))
    for _ in range(5):
        a.post("/api/login", json={"user": "huyen", "password": "wrong-wrong"})
    assert a.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 429
    assert b.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 429  # soft delay, per user
    clock.t += 5                                                      # ... which is seconds, not the 15 min lock
    assert b.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 200
    assert a.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 429  # a stays locked


def test_password_hashing_and_user_config():
    h = hash_password("a-long-password", log_n=10)
    assert h.startswith("scrypt$") and verify_password("a-long-password", h)
    assert not verify_password("another-one", h) and not verify_password("x", "garbage")
    assert not verify_password("x", "scrypt$30$8$1$AAAA$AAAA")  # absurd cost parameters are refused
    with pytest.raises(ValueError):
        hash_password("short")
    good = json.dumps([{"user": "a", "role": "founder", "hash": h}, {"user": "b", "role": "reviewer", "hash": h}])
    assert set(load_users({"MANUVI_WEB_USERS": good})) == {"a", "b"}
    for bad in ("", "not json", "[]", json.dumps([{"user": "a", "role": "admin", "hash": h}]),
                json.dumps([{"user": "a", "role": "reviewer", "hash": h}]),
                json.dumps([{"user": "a", "role": "founder", "hash": "plain"}])):
        with pytest.raises(AuthConfigError):
            load_users({"MANUVI_WEB_USERS": bad})


# ---- request guards
def test_csrf_host_origin_and_content_type_guards(env):
    app, *_ = env
    c = login(app)
    body = {"name": "x", "sections": "23:1-4"}
    assert c.post("/api/jobs", json=body).status_code == 200
    no_token = {k: v for k, v in c.headers.items() if k.lower() != "x-manuvi-token"}
    assert c.post("/api/jobs", json=body, headers={"X-Manuvi-Token": "wrong"}).status_code == 403
    assert c.get("/api/jobs", headers={"X-Manuvi-Token": "wrong"}).status_code == 403
    assert c.post("/api/jobs", content=json.dumps(body), headers={"Content-Type": "text/plain"}).status_code == 403
    assert c.post("/api/jobs", data=body).status_code == 403                                  # form post
    assert c.post("/api/jobs", json=body, headers={"Origin": "https://evil.example"}).status_code == 403
    assert c.post("/api/jobs", json=body, headers={"Origin": BASE}).status_code == 200
    assert c.get("/api/jobs", headers={"Host": "evil.example"}).status_code == 403
    assert c.get("/healthz", headers={"Host": "anything"}).status_code == 200                  # probe only
    del no_token


def test_security_headers_and_static_whitelist(env):
    app, *_ = env
    c = TestClient(app, base_url=BASE)
    h = c.get("/").headers
    assert "script-src 'self'" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"
    assert c.get("/static/brand.css").status_code == 200
    assert c.get("/static/fonts/Inter-Regular.woff2").status_code == 200
    for bad in ("/static/index.html", "/static/portal.html", "/static/../auth.py", "/static/%2e%2e/auth.py",
                "/static/fonts/OFL.txt"):
        assert c.get(bad).status_code == 404, bad


# ---- upload
PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def new_job(c, **kw):
    r = c.post("/api/jobs", json={"name": "Service manual", "sections": "23:1-4,43:1-2", **kw})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def put_pdf(c, jid, data, ct="application/pdf", **kw):
    return c.put(f"/api/jobs/{jid}/pdf", content=data, headers={"Content-Type": ct}, **kw)


def test_upload_happy_path_enqueues_only_the_job_id(env):
    app, store, queue = env
    c = login(app)
    jid = new_job(c, priority_sections=["43"], page_map={"23": 187})
    assert c.get("/api/jobs").json() == []                      # drafts are not listed
    assert put_pdf(c, jid, PDF).json()["bytes"] == len(PDF)
    assert c.post(f"/api/jobs/{jid}/submit", json={}).json()["state"] == "queued"
    assert store.get_bytes(jid, "input.pdf") == PDF
    rec = store.get_job(jid)
    assert rec.priority_sections == ["43"] and rec.page_map == {"23": 187} and rec.owner == "huyen"
    msg = queue.receive()
    assert msg.job_id == jid
    j = c.get(f"/api/jobs/{jid}").json()
    assert j["state"] == "queued" and j["priority_sections"] == ["43"]
    assert [x["id"] for x in c.get("/api/jobs").json()] == [jid]
    assert c.post(f"/api/jobs/{jid}/submit", json={}).status_code == 409
    assert put_pdf(c, jid, PDF).status_code == 409


@pytest.mark.parametrize("data,expected", [
    (b"hello world, not a pdf", 415), (b"", 415), (b"<html>%PDF-</html>", 415),
    (b"\x00\x01%PDF-1.4", 415), (b"MZ\x90\x00 executable", 415)])
def test_upload_rejects_non_pdf_content(env, data, expected):
    app, store, queue = env
    c = login(app)
    jid = new_job(c)
    assert put_pdf(c, jid, data).status_code == expected
    assert not store.exists(jid, "input.pdf")
    assert c.post(f"/api/jobs/{jid}/submit", json={}).status_code == 409
    assert queue.receive() is None


def test_upload_rejects_wrong_content_type_and_missing_token(env):
    app, *_ = env
    c = login(app)
    jid = new_job(c)
    assert put_pdf(c, jid, PDF, ct="text/plain").status_code == 403
    assert put_pdf(c, jid, PDF, ct="multipart/form-data; boundary=x").status_code == 403
    assert c.put(f"/api/jobs/{jid}/pdf", content=PDF,
                 headers={"Content-Type": "application/pdf", "X-Manuvi-Token": "bad"}).status_code == 403


def test_upload_size_limit(tmp_path, users, clock):
    app, store, _ = make_env(tmp_path, users, clock, max_upload_mb=1)
    c = login(app)
    jid = new_job(c)
    big = PDF + b"0" * (1024 * 1024 + 10)
    assert put_pdf(c, jid, big).status_code == 413
    # a client that lies about Content-Length is still cut off while streaming
    r = c.put(f"/api/jobs/{jid}/pdf", content=iter([PDF, b"0" * (600 * 1024), b"0" * (600 * 1024)]),
              headers={"Content-Type": "application/pdf"})
    assert r.status_code == 413 and not store.exists(jid, "input.pdf")
    assert put_pdf(c, jid, PDF).status_code == 200


@pytest.mark.parametrize("jid", ["..", "%2e%2e%2fetc", "..%2f..%2fx", "a" * 80, "not valid id", "short"])
def test_path_tricks_in_job_ids(env, jid):
    app, store, _ = env
    c = login(app)
    for call in (lambda: put_pdf(c, jid, PDF), lambda: c.get(f"/api/jobs/{jid}"),
                 lambda: c.post(f"/api/jobs/{jid}/submit", json={}), lambda: c.get(f"/download/{jid}/vi_pdf"),
                 lambda: c.get(f"/api/jobs/{jid}/queue")):
        assert call().status_code in (403, 404, 405, 307, 308), jid
    assert not list((Path(store.root) / "jobs").glob("**/etc"))
    assert store.list_job_ids() == []


@pytest.mark.parametrize("body", [
    {"name": "x", "sections": "../../etc:1-2"}, {"name": "x", "sections": "23"}, {"name": "x", "sections": "23:5-1"},
    {"name": "x", "sections": "23:1-4", "page_map": {"99": 3}}, {"name": "x", "sections": "23:1-4", "page_map": {"23": 0}},
    {"name": "x", "sections": "23:1-4", "priority_sections": ["99"]}, {"name": "", "sections": "23:1-4"},
    {"name": "x" * 500, "sections": "23:1-4"}, {"name": "x", "sections": "a b:1-2"}])
def test_create_job_validation(env, body):
    app, store, _ = env
    c = login(app)
    assert c.post("/api/jobs", json=body).status_code == 422
    assert store.list_job_ids() == []


def test_job_name_with_markup_is_data_and_the_ui_escapes_it(env):
    app, store, _ = env
    c = login(app)
    r = c.post("/api/jobs", json={"name": "<img src=x onerror=alert(1)>", "sections": "23:1-2"})
    assert r.status_code == 200 and "<img" in store.get_job(r.json()["id"]).name
    js = (ROOT / "src/webapp/static/portal.js").read_text()
    assert "${esc(j.name)}" in js and "${j.name}" not in js


# ---- roles and review
def test_reviewer_is_read_only_until_the_founder_allows(env, tmp_path):
    app, store, _ = env
    seed_job(store, tmp_path)
    f, r = login(app), login(app, "rev", PW_R)
    assert r.post("/api/jobs", json={"name": "x", "sections": "23:1-4"}).status_code == 403
    assert put_pdf(r, JID, PDF).status_code == 403
    assert r.post(f"/api/jobs/{JID}/cancel", json={}).status_code == 403
    assert r.post(f"/api/jobs/{JID}/resume", json={}).status_code == 403
    assert r.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": True}).status_code == 403
    assert r.get("/api/events").status_code == 403
    # reading the segments needs the same founder grant as reviewing them
    assert r.get(f"/api/jobs/{JID}/queue").status_code == 403
    assert r.get(f"/api/jobs/{JID}/audit").status_code == 403 and r.get(f"/api/jobs/{JID}/terms").status_code == 403
    assert f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": True}).json()["reviewer_can_review"] is True
    assert len(r.get(f"/api/jobs/{JID}/queue").json()) == 40
    assert f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": False}).status_code == 200
    assert r.get(f"/api/jobs/{JID}/queue").status_code == 403
    ok_id = next(x["id"] for x in f.get(f"/api/jobs/{JID}/queue?status=ok").json() if x["kind"] != "signal")
    d = {"action": "approve"}
    assert r.post(f"/api/jobs/{JID}/segments/{ok_id}/decision", json=d).status_code == 403
    assert r.post(f"/api/jobs/{JID}/segments/{ok_id}/comment", json={"text": "hm"}).status_code == 403
    assert f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": True}).json() == {
        "reviewer_can_review": True, "reviewer_can_read": False}
    assert r.post(f"/api/jobs/{JID}/segments/{ok_id}/decision", json=d).status_code == 200
    assert r.post(f"/api/jobs/{JID}/segments/{ok_id}/comment", json={"text": "checked against the PDF"}).status_code == 200
    # never: edit, reject, terms, safety
    assert r.put(f"/api/jobs/{JID}/segments/{ok_id}", json={"target": "x"}).status_code == 403
    assert r.post(f"/api/jobs/{JID}/segments/{ok_id}/decision", json={"action": "reject"}).status_code == 403
    term = f.get(f"/api/jobs/{JID}/terms").json()[0]["id"]
    assert r.post(f"/api/jobs/{JID}/terms/{term}", json={"action": "approve"}).status_code == 403
    sig = next(x["id"] for x in r.get(f"/api/jobs/{JID}/queue?kind=signal").json())
    seg = r.get(f"/api/jobs/{JID}/segments/{sig}").json()
    assert "nonce" not in seg                                          # no way to sign a safety segment
    assert r.post(f"/api/jobs/{JID}/segments/{sig}/decision", json={"action": "approve"}).status_code == 403
    assert r.post(f"/api/jobs/{JID}/segments/{sig}/decision",
                  json={"action": "approve", "nonce": "guess"}).status_code == 403
    out = r.post(f"/api/jobs/{JID}/bulk-approve", json={"ids": [sig, ok_id]}).json()
    assert sig not in out["approved"]


def test_safety_approval_only_by_founder_with_explicit_nonce(env, tmp_path):
    app, store, _ = env
    seed_job(store, tmp_path, reviewer_ok=True)
    f = login(app)
    sig = next(x["id"] for x in f.get(f"/api/jobs/{JID}/queue?kind=signal").json())
    assert f.post(f"/api/jobs/{JID}/segments/{sig}/decision", json={"action": "approve"}).status_code == 409
    nonce = f.get(f"/api/jobs/{JID}/segments/{sig}").json()["nonce"]
    r = f.post(f"/api/jobs/{JID}/segments/{sig}/decision", json={"action": "approve", "nonce": nonce})
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    # persisted to the store, where the worker reads it
    stored = json.loads(store.get_bytes(JID, "segments.json"))
    assert next(s for s in stored if s["id"] == sig)["decision"] == "approved"


def test_audit_log_records_user_names(env, tmp_path):
    app, store, _ = env
    seed_job(store, tmp_path, reviewer_ok=True)
    f, r = login(app), login(app, "rev", PW_R)
    ok_id = next(x["id"] for x in f.get(f"/api/jobs/{JID}/queue?status=ok").json() if x["kind"] != "signal")
    r.post(f"/api/jobs/{JID}/segments/{ok_id}/decision", json={"action": "approve"})
    r.post(f"/api/jobs/{JID}/segments/{ok_id}/comment", json={"text": "looks right"})
    f.put(f"/api/jobs/{JID}/segments/S001", json={"target": "Sửa"})
    audit = f.get(f"/api/jobs/{JID}/audit").json()
    users = {(a["action"], a.get("user")) for a in audit}
    assert ("approve", "rev") in users and ("comment", "rev") in users and ("edit", "huyen") in users
    assert store.exists(JID, "audit.jsonl")
    ev = f.get("/api/events").json()
    assert {"login"} <= {e["action"] for e in ev} and {e["user"] for e in ev} >= {"huyen", "rev"}
    assert PW_F not in json.dumps(ev) and PW_R not in json.dumps(ev)


def test_failed_logins_are_audited_without_passwords(env):
    app, *_ = env
    c = TestClient(app, base_url=BASE)
    c.post("/api/login", json={"user": "huyen", "password": "wrong-password-xyz"})
    f = login(app)
    ev = f.get("/api/events").json()
    assert any(e["action"] == "login_failed" for e in ev)
    assert "wrong-password-xyz" not in json.dumps(ev)


def test_review_pages_carry_role_and_csp_safe_config(env, tmp_path):
    app, store, _ = env
    seed_job(store, tmp_path, reviewer_ok=False)
    r = login(app, "rev", PW_R)
    assert r.get(f"/review/{JID}").status_code == 403                   # the review UI is closed without the grant
    f = login(app)
    f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": True})
    page = r.get(f"/review/{JID}")
    assert page.status_code == 200 and '"role": "reviewer"' in page.text and '"canReview": true' in page.text
    assert "<script>" not in page.text.replace('<script type="application/json"', "")
    assert r.get("/review/nosuchjob1").status_code == 404


def test_cancel_resume_and_state_rules(env, tmp_path):
    app, store, queue = env
    seed_job(store, tmp_path)
    f = login(app)
    assert f.post(f"/api/jobs/{JID}/resume", json={}).json()["action"] == "resume"
    assert queue.receive().job_id == JID
    store.put_status(JID, JobStatus(state=JobState.done, outputs=["out/segments.xlsx"]))
    assert f.post(f"/api/jobs/{JID}/resume", json={}).json()["action"] == "rebuild"
    assert store.exists(JID, "rebuild.json")
    assert f.post(f"/api/jobs/{JID}/cancel", json={}).status_code == 409        # terminal
    store.put_status(JID, JobStatus(state=JobState.running))
    assert f.post(f"/api/jobs/{JID}/cancel", json={}).status_code == 200
    assert store.is_cancelled(JID)
    assert f.get(f"/api/jobs/{JID}").json()["state"] == "cancelled"


# ---- downloads
def test_download_local_and_not_ready(env, tmp_path):
    """Downloads are the founder's by default. The old version of this test logged in as the reviewer and treated
    a successful download as intended; that is the leak (the outputs hold the whole manual, source and translation).
    A reviewer now gets 403 until the founder sets `reviewer_can_read`, which is a separate grant from
    `reviewer_can_review`, and taking review away takes the downloads away too."""
    app, store, _ = env
    seed_job(store, tmp_path)
    f, rv = login(app), login(app, "rev", PW_R)
    c = f
    assert c.get(f"/download/{JID}/vi_pdf").status_code == 404
    store.put_bytes(JID, "out/vi.pdf", b"%PDF-vi")
    store.put_status(JID, JobStatus(state=JobState.done, outputs=["out/vi.pdf"]))
    r = c.get(f"/download/{JID}/vi_pdf")
    assert r.status_code == 200 and r.content == b"%PDF-vi" and "attachment" in r.headers["content-disposition"]
    assert c.get(f"/download/{JID}/input").status_code == 404                    # only the four outputs
    assert c.get(f"/download/{JID}/..%2fjob.json").status_code == 404
    assert c.get(f"/api/jobs/{JID}").json()["outputs"]["vi_pdf"] is True
    # reviewer: forbidden by default, also when only review is granted, allowed with reviewer_can_read
    assert rv.get(f"/download/{JID}/vi_pdf").status_code == 403
    f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": True})
    assert rv.get(f"/download/{JID}/vi_pdf").status_code == 403
    assert f.post(f"/api/jobs/{JID}/reviewer-access", json={"read": True}).json()["reviewer_can_read"] is True
    assert rv.get(f"/download/{JID}/vi_pdf").status_code == 200
    f.post(f"/api/jobs/{JID}/reviewer-access", json={"allow": False})
    assert rv.get(f"/download/{JID}/vi_pdf").status_code == 403
    assert rv.get(f"/download/{JID}/nope").status_code == 403           # no probing of kinds without the grant


def test_s3_backed_portal_presigned_downloads_and_review_sync(tmp_path, users, clock):
    with mock_aws():
        from botocore.config import Config
        s3 = boto3.client("s3", region_name="ap-southeast-2", config=Config(signature_version="s3v4"))
        s3.create_bucket(Bucket="manuvi-test", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"})
        app, store, _ = make_env(tmp_path, users, clock, store=S3Store("manuvi-test", client=s3))
        seed_job(store, tmp_path)
        store.put_bytes(JID, "out/segments.xlsx", b"PK")
        store.put_status(JID, JobStatus(state=JobState.done, outputs=["out/segments.xlsx"]))
        f = login(app)
        r = f.get(f"/download/{JID}/segments_xlsx", follow_redirects=False)
        assert r.status_code == 302
        loc = r.headers["location"]
        assert "jobs/" + JID + "/out/segments.xlsx" in loc and "X-Amz-Expires=300" in loc and "attachment" in loc
        ok_id = next(x["id"] for x in f.get(f"/api/jobs/{JID}/queue?status=ok").json() if x["kind"] != "signal")
        assert f.post(f"/api/jobs/{JID}/segments/{ok_id}/decision", json={"action": "approve"}).status_code == 200
        stored = json.loads(s3.get_object(Bucket="manuvi-test", Key=f"jobs/{JID}/segments.json")["Body"].read())
        assert next(s for s in stored if s["id"] == ok_id)["decision"] == "approved"
        assert s3.head_object(Bucket="manuvi-test", Key=f"jobs/{JID}/audit.jsonl")["ServerSideEncryption"] == "AES256"


# ---- hosted factory (container contract)
def test_hosted_factory_contract(tmp_path, users):
    env = {"MANUVI_WEB_USERS": json.dumps([{"user": u.name, "role": u.role, "hash": u.hash} for u in users.values()]),
           "MANUVI_ALLOWED_HOSTS": "manuvi.example.org, other.example.org", "MANUVI_DATA_DIR": str(tmp_path / "d"),
           "MANUVI_QUEUE": "dir", "MANUVI_STORE": "local", "PORT": "8000"}
    app = hosted_app(env)
    c = TestClient(app, base_url="http://manuvi.example.org")
    assert c.get("/healthz").json() == {"ok": True}
    assert c.get("/healthz", headers={"Host": "10.0.0.5:8000"}).status_code == 200      # container probe
    assert c.get("/").status_code == 200
    assert c.get("/", headers={"Host": "evil.example"}).status_code == 403
    assert TestClient(app, base_url="http://localhost:8000").get("/").status_code == 200
    assert c.post("/api/login", json={"user": "huyen", "password": PW_F},
                  headers={"Origin": "https://manuvi.example.org"}).status_code == 200
    with pytest.raises(AuthConfigError):
        hosted_app({**env, "MANUVI_WEB_USERS": ""})


def test_unknown_user_costs_as_much_as_a_known_one():
    """Timing side channel: the dummy hash must use the same scrypt cost as the real users' hashes."""
    h = hash_password("correct horse battery", log_n=12)
    a = Auth({"x": User("x", "founder", h)})
    assert a._dummy.split("$")[1:4] == h.split("$")[1:4]


def test_parallel_pulls_do_not_race_on_a_shared_tmp_file(tmp_path):
    """The review page fires several GETs at once; once the cache expired they raced on one tmp file (500)."""
    from concurrent.futures import ThreadPoolExecutor
    from src.webapp.portal import SyncedJobStore
    store = LocalStore(tmp_path / "store")
    store.put_job(JobRecord(id=JID, name="x", owner="huyen", sections="23:1-4", submitted=True))
    store.put_bytes(JID, "segments.json", b"[]")
    store.put_bytes(JID, "terms.json", b"[]")
    s = SyncedJobStore(tmp_path / "rev", store, JID, ttl=0.0)

    def pull(_):
        for _ in range(30):
            s._pull()
        return True

    with ThreadPoolExecutor(8) as ex:
        assert all(ex.map(pull, range(8)))
