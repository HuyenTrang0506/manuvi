import importlib.util
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.webapp.app import create_app

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def jobdir(tmp_path):
    spec = importlib.util.spec_from_file_location("mk", ROOT / "scripts" / "make_demo_job.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    mk.main(str(tmp_path / "job"))
    return tmp_path / "job"


TOKEN = "test-token"
BASE = "http://127.0.0.1:8765"
H = {"X-Manuvi-Token": TOKEN}


def mk_client(jobdir):
    return TestClient(create_app(jobdir, port=8765, token=TOKEN), base_url=BASE, headers=H)


@pytest.fixture
def client(jobdir):
    return mk_client(jobdir)


def approve_signal(c, sid):
    nonce = c.get(f"/api/segments/{sid}").json()["nonce"]
    return c.post(f"/api/segments/{sid}/decision", json={"action": "approve", "nonce": nonce})


def test_queue_ordering(client):
    rows = client.get("/api/queue").json()
    assert len(rows) == 40
    kinds = [r["kind"] for r in rows]
    n_sig = kinds.count("signal")
    assert n_sig == 3 and set(kinds[:3]) == {"signal"}
    rest = rows[3:]
    rank = {"error": 0, "needs_review": 1, "warn": 2, "ok": 3}
    keys = [(rank[r["status"]], -r["risk"]) for r in rest]
    assert keys == sorted(keys)
    assert rest[0]["status"] == "error"
    assert [r["kind"] for r in client.get("/api/queue?kind=signal").json()] == ["signal"] * 3


def test_bulk_approve_cannot_approve_safety(client):
    rows = client.get("/api/queue").json()
    sig = [r["id"] for r in rows if r["kind"] == "signal"]
    ok = [r["id"] for r in rows if r["status"] == "ok" and r["kind"] != "signal"][:3]
    res = client.post("/api/bulk-approve", json={"ids": sig + ok}).json()
    assert sorted(res["approved"]) == sorted(ok)
    assert {s["id"] for s in res["skipped"]} == set(sig)
    for sid in sig:
        assert client.get(f"/api/segments/{sid}").json()["decision"] == "pending"
        # a client flag alone, or a guessed nonce, is refused
        for body in ({"action": "approve"}, {"action": "approve", "explicit": True},
                     {"action": "approve", "nonce": "guess"}):
            assert client.post(f"/api/segments/{sid}/decision", json=body).status_code == 409
    # individual fetch -> nonce -> approve works, nonce is single use
    nonce = client.get(f"/api/segments/{sig[0]}").json()["nonce"]
    body = {"action": "approve", "nonce": nonce}
    assert client.post(f"/api/segments/{sig[0]}/decision", json=body).json()["decision"] == "approved"
    assert client.post(f"/api/segments/{sig[1]}/decision", json=body).status_code == 409


def test_bulk_only_ok_segments(client):
    rows = client.get("/api/queue").json()
    non_ok = [r["id"] for r in rows if r["status"] in ("warn", "needs_review", "error") and r["kind"] != "signal"]
    res = client.post("/api/bulk-approve", json={"ids": non_ok}).json()
    assert res["approved"] == [] and len(res["skipped"]) == len(non_ok)


def test_protected_code_blocks_approval(client):
    r = client.post("/api/segments/S014/decision", json={"action": "approve"})
    assert r.status_code == 409 and "YT2210" in r.json()["detail"]
    # duplicate/changed code is also refused (multiset, not substring)
    client.put("/api/segments/S014", json={"target": "Đo giắc YT2210 và YT2210 giữa pin 3 và pin 7."})
    assert client.post("/api/segments/S014/decision", json={"action": "approve"}).status_code == 409
    client.put("/api/segments/S014", json={"target": "Đo điện trở giữa pin 3 và pin 7 của giắc YT2210."})
    assert client.post("/api/segments/S014/decision", json={"action": "approve"}).status_code == 200


def test_edit_reruns_qa(client):
    client.put("/api/segments/S001", json={"target": "Kiểm tra mức dầu."})
    s = client.get("/api/segments/S001").json()
    assert "qa_issues" in s
    client.put("/api/segments/S010", json={"target": "Ngắt giắc khỏi bộ điều khiển."})
    s = client.get("/api/segments/S010").json()
    assert s["status"] == "error" and s["qa_issues"]


def test_edit_persists_with_audit_log(client, jobdir):
    old = client.get("/api/segments/S001").json()["target"]
    client.put("/api/segments/S001", json={"target": "Mới"})
    client.post("/api/segments/S001/decision", json={"action": "reject"})
    data = json.loads((jobdir / "segments.json").read_text("utf-8"))
    assert next(s for s in data if s["id"] == "S001")["target"] == "Mới"
    log = [json.loads(l) for l in (jobdir / "audit.jsonl").read_text("utf-8").splitlines()]
    assert [e["action"] for e in log] == ["edit", "reject"]
    assert log[0]["old"] == old and log[0]["new"] == "Mới" and log[0]["ts"]
    assert client.get("/api/audit").json()[0]["action"] == "reject"


def test_restart_keeps_state(jobdir):
    c1 = mk_client(jobdir)
    c1.put("/api/segments/S002", json={"target": "Sửa"})
    c1.post("/api/segments/S002/decision", json={"action": "needs_review"})
    c1.post("/api/terms/T01", json={"action": "edit", "vi": "van điều khiển chính"})
    c2 = mk_client(jobdir)
    s = c2.get("/api/segments/S002").json()
    assert s["target"] == "Sửa" and s["status"] == "needs_review"
    t = next(t for t in c2.get("/api/terms").json() if t["id"] == "T01")
    assert t["vi"] == "van điều khiển chính" and t["decision"] == "approved"
    assert len(c2.get("/api/audit").json()) == 3


def test_status_and_index(client):
    s = client.get("/api/status").json()
    assert s["total"] == 40 and s["cost_usd"] == 0.0 and s["progress"] == 0
    assert s["safety_total"] == 3
    assert client.get("/").status_code == 403  # no ?t= token
    page = client.get("/?t=" + TOKEN).text
    assert "Manuvi review" in page and TOKEN in page


def test_security(jobdir):
    c = TestClient(create_app(jobdir, port=8765, token=TOKEN), base_url=BASE)
    body = {"target": "x"}
    # missing token
    assert c.put("/api/segments/S001", json=body).status_code == 403
    assert c.get("/api/queue").status_code == 403
    # text/plain form-style post rejected even with token
    r = c.post("/api/bulk-approve", content='{"ids":["S001"]}', headers={**H, "Content-Type": "text/plain"})
    assert r.status_code == 403
    r = c.post("/api/bulk-approve", data={"ids": "S001"}, headers=H)
    assert r.status_code == 403
    # bad Host (DNS rebinding)
    assert c.get("/api/queue", headers={**H, "Host": "evil.example:8765"}).status_code == 403
    assert c.get("/api/queue", headers={**H, "Host": "127.0.0.1:9999"}).status_code == 403
    # bad Origin
    r = c.put("/api/segments/S001", json=body, headers={**H, "Origin": "http://evil.example"})
    assert r.status_code == 403
    ok = c.put("/api/segments/S001", json=body, headers={**H, "Origin": BASE})
    assert ok.status_code == 200


def test_audit_hash_chain_and_external_write(jobdir):
    from src.webapp.store import JobStore
    c = mk_client(jobdir)
    c.put("/api/segments/S001", json={"target": "a"})
    c.post("/api/segments/S001/decision", json={"action": "reject"})
    st = JobStore(jobdir)
    assert st.verify_audit()
    # pipeline-side change on disk is not clobbered by a later UI write
    data = json.loads((jobdir / "segments.json").read_text("utf-8"))
    data[5]["target"] = "from pipeline"
    (jobdir / "segments.json").write_text(json.dumps(data, ensure_ascii=False), "utf-8")
    c.put("/api/segments/S001", json={"target": "b"})
    assert json.loads((jobdir / "segments.json").read_text("utf-8"))[5]["target"] == "from pipeline"
    lines = (jobdir / "audit.jsonl").read_text("utf-8").splitlines()
    rec = json.loads(lines[1]); rec["new"] = "tampered"; lines[1] = json.dumps(rec)
    (jobdir / "audit.jsonl").write_text("\n".join(lines) + "\n", "utf-8")
    assert not st.verify_audit()
