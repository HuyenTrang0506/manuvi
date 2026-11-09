"""Portal audit in the Store, client address handling behind a tunnel, per-user soft lockout ($0, offline)."""
import json

import boto3
import pytest
from fastapi.testclient import TestClient
from moto import mock_aws

from src.jobs import LocalStore, S3Store
from src.webapp.auth import RateLimiter
from src.webapp.portal import PortalAudit

from .test_portal import BASE, JID, PW_F, clock, login, make_env, seed_job, users  # noqa: F401  (fixtures)


@pytest.fixture(params=["local", "s3"])
def any_store(request, tmp_path):
    if request.param == "local":
        yield LocalStore(tmp_path / "s")
    else:
        with mock_aws():
            c = boto3.client("s3", region_name="ap-southeast-2")
            c.create_bucket(Bucket="manuvi-test", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"})
            yield S3Store("manuvi-test", client=c)


# ---- portal audit lives in the Store and the chain is verifiable
def test_audit_survives_instance_replacement(any_store, tmp_path, users, clock):
    app1, store, _ = make_env(tmp_path / "one", users, clock, store=any_store)
    seed_job(store, tmp_path)
    f = login(app1)
    sid = next(x["id"] for x in f.get(f"/api/jobs/{JID}/queue?status=ok").json() if x["kind"] != "signal")
    assert f.post(f"/api/jobs/{JID}/segments/{sid}/decision", json={"action": "approve"}).status_code == 200
    before = f.get("/api/events").json()
    assert [e["action"] for e in before][-1] == "login" and f.get("/api/events/verify").json() == {"chain_ok": True}

    # the instance is replaced: new process, empty data dir, same bucket / store
    app2, _, _ = make_env(tmp_path / "two", users, clock, store=any_store)
    g = login(app2)
    after = g.get("/api/events").json()
    assert after[1:] == before                                     # every earlier event is still there, in order
    assert after[0]["action"] == "login" and after[0]["seq"] == before[0]["seq"] + 1   # the chain continues
    assert g.get("/api/events/verify").json() == {"chain_ok": True}
    # review state (decisions + the job's own hash-chained audit) comes back from the Store too
    assert g.get(f"/api/jobs/{JID}/segments/{sid}").json()["decision"] == "approved"
    assert any(a["action"] == "approve" for a in g.get(f"/api/jobs/{JID}/audit").json())
    assert not list((tmp_path / "two" / "data").glob("**/portal-audit*"))     # nothing on the instance disk


def test_audit_objects_are_new_and_encrypted_in_s3(tmp_path, users, clock):
    with mock_aws():
        s3 = boto3.client("s3", region_name="ap-southeast-2")
        s3.create_bucket(Bucket="manuvi-test", CreateBucketConfiguration={"LocationConstraint": "ap-southeast-2"})
        app, store, _ = make_env(tmp_path, users, clock, store=S3Store("manuvi-test", client=s3))
        login(app)
        keys = [o["Key"] for o in s3.list_objects_v2(Bucket="manuvi-test", Prefix="portal/audit/")["Contents"]]
        assert keys == ["portal/audit/000000000001.json"]
        assert s3.head_object(Bucket="manuvi-test", Key=keys[0])["ServerSideEncryption"] == "AES256"
        assert store.global_put_new("audit/000000000001.json", b"{}") is False       # never overwritten


def test_chain_detects_tampering_gaps_and_two_writers(any_store):
    a, b = PortalAudit(any_store), PortalAudit(any_store)           # two writers that both start at the same head
    for i in range(3):
        a.log("u", "x", "1.1.1.1", n=i)
        b.log("u", "y", "2.2.2.2", n=i)                             # b's number is taken: it must reload and go on
    assert [e["seq"] for e in PortalAudit(any_store).tail(10)][::-1] == [1, 2, 3, 4, 5, 6]
    assert PortalAudit(any_store).verify()
    # edit one record in place (what a rogue writer without Object Lock could do)
    key = "audit/000000000003.json"
    rec = json.loads(any_store.global_get(key))
    rec["user"] = "someone-else"
    any_store.client.put_object(Bucket="manuvi-test", Key="portal/" + key, Body=json.dumps(rec).encode()) \
        if hasattr(any_store, "client") else (any_store.root / "portal" / key).write_text(json.dumps(rec))
    assert not PortalAudit(any_store).verify()


def test_chain_detects_a_deleted_record(tmp_path):
    st = LocalStore(tmp_path)
    au = PortalAudit(st)
    for i in range(4):
        au.log("u", "x")
    (tmp_path / "portal" / "audit" / "000000000002.json").unlink()
    assert not PortalAudit(st).verify()


# ---- client address behind the tunnel
def ips(app, who="huyen"):
    f = login(app, who, PW_F) if who == "huyen" else None
    return [e["ip"] for e in f.get("/api/events").json() if e["action"] == "login_failed"]


def fail_from(app, peer, headers=None, user="huyen"):
    TestClient(app, base_url=BASE, client=(peer, 5000)).post(
        "/api/login", json={"user": user, "password": "wrong-wrong"}, headers=headers or {})


def test_cf_connecting_ip_needs_the_flag_and_a_trusted_peer(tmp_path, users, clock):
    hdr = {"CF-Connecting-IP": "198.51.100.7"}
    on, *_ = make_env(tmp_path / "on", users, clock, trusted_proxies="172.29.0.0/24", trust_cf=True)
    off, *_ = make_env(tmp_path / "off", users, clock, trusted_proxies="172.29.0.0/24", trust_cf=False)
    fail_from(on, "172.29.0.5", hdr)            # peer is the tunnel network, flag on -> the visitor's address
    fail_from(on, "203.0.113.9", hdr)           # not the tunnel: the header is attacker input, ignored
    fail_from(on, "172.29.0.5", {"CF-Connecting-IP": "not-an-ip"})   # malformed -> the peer
    fail_from(off, "172.29.0.5", hdr)           # flag off -> the header is ignored
    assert sorted(ips(on)) == ["172.29.0.5", "198.51.100.7", "203.0.113.9"]
    assert ips(off) == ["172.29.0.5"]


def test_spoofed_cf_header_cannot_dodge_the_lockout(tmp_path, users, clock):
    app, *_ = make_env(tmp_path, users, clock, trusted_proxies="172.29.0.0/24", trust_cf=False)
    for i in range(5):
        fail_from(app, "203.0.113.9", {"CF-Connecting-IP": f"198.51.100.{i}"})   # a new "address" every time
    r = TestClient(app, base_url=BASE, client=("203.0.113.9", 5000)).post(
        "/api/login", json={"user": "huyen", "password": PW_F}, headers={"CF-Connecting-IP": "198.51.100.99"})
    assert r.status_code == 429


# ---- failed-login audit never holds the raw user field
def test_failed_login_audit_stores_a_tag_not_the_raw_input(env_app):
    app = env_app
    secret_typed_as_user = "Hunter2-my-real-password"
    c = TestClient(app, base_url=BASE)
    for u in (secret_typed_as_user, secret_typed_as_user.upper(), "nobody", "huyen"):
        assert c.post("/api/login", json={"user": u, "password": "wrong-wrong"}).status_code == 401
    ev = [e for e in login(app).get("/api/events").json() if e["action"] == "login_failed"][::-1]
    dump = json.dumps(ev)
    assert secret_typed_as_user.lower() not in dump.lower() and "nobody" not in dump and '"huyen"' not in dump
    assert all(e["user"].startswith("~") and len(e["user"]) == 13 for e in ev)
    assert ev[0]["user"] == ev[1]["user"]                           # same input (case-insensitive) -> same tag
    assert ev[0]["user_len"] == len(secret_typed_as_user) and [e["known"] for e in ev] == [False, False, False, True]


@pytest.fixture
def env_app(tmp_path, users, clock):
    return make_env(tmp_path, users, clock)[0]


# ---- per-user soft lockout with exponential delay
def test_soft_lockout_doubles_then_caps_and_never_becomes_permanent():
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0], max_fails=1000)             # isolate the soft layer from the hard lock
    waits = []
    for i in range(14):
        rl.fail(f"198.51.100.{i}", "Huyen")                         # a different address every time
        waits.append(rl.retry_after("203.0.113.1", "huyen"))
    assert waits[:4] == [0] * 4                                      # a few typos are free
    assert waits[4:8] == [3, 5, 9, 17]                               # 2s, 4s, 8s, 16s (+1 rounding)
    assert max(waits) == 301                                         # capped at 5 minutes
    t[0] += 301
    assert rl.retry_after("203.0.113.1", "huyen") == 0               # the delay simply ends
    rl.success("203.0.113.1", "huyen")
    t[0] += 1
    rl.fail("1.1.1.1", "huyen")
    assert rl.retry_after("1.1.1.1", "huyen") == 0
    assert rl.retry_after("1.1.1.1", "someone-else") == 0            # other users are untouched


def test_soft_lockout_over_http_blocks_the_right_password_for_a_moment(tmp_path, users, clock):
    app, *_ = make_env(tmp_path, users, clock)
    for i in range(5):
        fail_from(app, f"198.51.100.{i}")
    c = TestClient(app, base_url=BASE, client=("198.51.100.99", 1))   # a sixth address, correct password
    r = c.post("/api/login", json={"user": "huyen", "password": PW_F})
    assert r.status_code == 429 and 1 <= int(r.headers["retry-after"]) <= 5
    clock.t += int(r.headers["retry-after"])
    assert c.post("/api/login", json={"user": "huyen", "password": PW_F}).status_code == 200


def test_rate_limiter_memory_is_bounded():
    rl = RateLimiter(clock=lambda: 0.0)
    rl.MAX_KEYS = 100
    for i in range(1000):
        rl.fail("1.1.1.1", f"random-user-{i}")
    assert len(rl.fails) <= 100 + 3


# ---- on-demand TLS ask endpoint (Caddyfile.internal)
def test_tls_ask_answers_only_for_the_apps_own_names(tmp_path, users, clock):
    app, *_ = make_env(tmp_path, users, clock)                       # allowed host: "testserver"
    c = TestClient(app, base_url="http://app:8000")                  # Caddy calls it as http://app:8000, no session
    assert c.get("/internal/tls-ask", params={"domain": "testserver"}).status_code == 200
    assert c.get("/internal/tls-ask", params={"domain": "TestServer."}).status_code == 200
    for bad in ("evil.example", "", "testserver.evil.example", "10.0.0.1"):
        assert c.get("/internal/tls-ask", params={"domain": bad}).status_code == 404, bad
    # the same path reached through the public proxy (X-Forwarded-For set by Caddy) is not an oracle
    assert c.get("/internal/tls-ask", params={"domain": "testserver"},
                 headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 404
