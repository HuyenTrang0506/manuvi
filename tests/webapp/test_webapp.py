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


@pytest.fixture
def client(jobdir):
    return TestClient(create_app(jobdir))


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
        # single approve without explicit click is also refused
        r = client.post(f"/api/segments/{sid}/decision", json={"action": "approve"})
        assert r.status_code == 409
    # explicit human click works
    r = client.post(f"/api/segments/{sig[0]}/decision", json={"action": "approve", "explicit": True})
    assert r.status_code == 200 and r.json()["decision"] == "approved"


def test_protected_code_blocks_approval(client):
    r = client.post("/api/segments/S014/decision", json={"action": "approve", "explicit": True})
    assert r.status_code == 409 and "YT2210" in r.json()["detail"]
    client.put("/api/segments/S014", json={"target": "Đo điện trở giữa chân 3 và chân 7 của giắc YT2210."})
    assert client.post("/api/segments/S014/decision", json={"action": "approve", "explicit": True}).status_code == 200


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
    c1 = TestClient(create_app(jobdir))
    c1.put("/api/segments/S002", json={"target": "Sửa"})
    c1.post("/api/segments/S002/decision", json={"action": "needs_review"})
    c1.post("/api/terms/T01", json={"action": "edit", "vi": "van điều khiển chính"})
    c2 = TestClient(create_app(jobdir))
    s = c2.get("/api/segments/S002").json()
    assert s["target"] == "Sửa" and s["status"] == "needs_review"
    t = next(t for t in c2.get("/api/terms").json() if t["id"] == "T01")
    assert t["vi"] == "van điều khiển chính" and t["decision"] == "approved"
    assert len(c2.get("/api/audit").json()) == 3


def test_status_and_index(client):
    s = client.get("/api/status").json()
    assert s["total"] == 40 and s["cost_usd"] == 0.0 and s["progress"] == 0
    assert s["safety_total"] == 3
    assert "Manuvi review" in client.get("/").text
