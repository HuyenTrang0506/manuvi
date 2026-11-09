"""Lost updates on segments.json: web edit vs web edit, and web decision vs worker merge ($0, offline).

Each test uses two actors on the same Store. A hook runs the second actor's write in the gap between the first
actor's read and write, which is the window that used to lose an update.
"""
import json
from pathlib import Path

import pytest
from src.jobs import JobRecord, LocalStore, VersionConflict
from src.webapp.portal import SyncedJobStore
from src.worker import Worker

from .test_portal import JID, clock, login, make_env, seed_job, users  # noqa: F401  (fixtures)


class HookStore:
    """Delegates to a store; runs `hook` once, right before the first conditional write."""

    def __init__(self, inner, hook=None):
        self._inner, self.hook = inner, hook

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def put_if_match(self, *a, **k):
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        return self._inner.put_if_match(*a, **k)


@pytest.fixture
def two(tmp_path, users, clock):
    app, store, _ = make_env(tmp_path, users, clock)
    seed_job(store, tmp_path)
    return app, store, tmp_path


def seg_ids(store, n=3):
    return [s["id"] for s in json.loads(store.get_bytes(JID, "segments.json")) if s["kind"] != "signal"][:n]


def test_two_web_actors_both_edits_survive_a_race(two):
    _, store, tmp = two
    a = SyncedJobStore(tmp / "ra", store, JID, ttl=0.0)
    b = SyncedJobStore(tmp / "rb", HookStore(store), JID, ttl=0.0)
    s1, s2 = seg_ids(store, 2)
    b.remote.hook = lambda: a.edit(s1, "Bản A", actor="alice")      # A lands between B's read and B's write
    b.edit(s2, "Bản B", actor="bob")
    final = {s["id"]: s for s in json.loads(store.get_bytes(JID, "segments.json"))}
    assert final[s1]["target"] == "Bản A" and final[s2]["target"] == "Bản B"       # nothing lost
    audit = [json.loads(l) for l in store.get_bytes(JID, "audit.jsonl").decode().splitlines()]
    assert {(r["user"], r["id"]) for r in audit} == {("alice", s1), ("bob", s2)}
    assert b.verify_audit() and SyncedJobStore(tmp / "rc", store, JID).verify_audit()


def test_stale_edit_of_the_same_segment_is_a_conflict(two):
    _, store, tmp = two
    a = SyncedJobStore(tmp / "ra", store, JID, ttl=0.0)
    b = SyncedJobStore(tmp / "rb", store, JID, ttl=0.0)
    sid = seg_ids(store, 1)[0]
    seen_a, seen_b = a.get(sid)["_rev"] if "_rev" in a.get(sid) else 0, b.get(sid).get("_rev", 0)
    a.edit(sid, "Bản A", actor="alice", rev=seen_a)
    from src.webapp.store import StaleError
    with pytest.raises(StaleError):
        b.edit(sid, "Bản B", actor="bob", rev=seen_b)                # B still holds the old version
    assert b.get(sid)["target"] == "Bản A"
    b.edit(sid, "Bản B", actor="bob", rev=b.get(sid)["_rev"])        # after a reload it goes through


def test_conflict_over_http_has_the_reload_message_and_the_page_shows_it_accessibly(two, users, clock):
    app, store, tmp = two
    f1, f2 = login(app), login(app)
    sid = seg_ids(store, 1)[0]
    seg = f1.get(f"/api/jobs/{JID}/segments/{sid}").json()
    rev = seg.get("_rev", 0)
    assert f1.put(f"/api/jobs/{JID}/segments/{sid}", json={"target": "một", "rev": rev}).status_code == 200
    r = f2.put(f"/api/jobs/{JID}/segments/{sid}", json={"target": "hai", "rev": rev})
    assert r.status_code == 409 and r.json()["detail"] == "Someone else changed this, reload."
    assert r.headers["x-manuvi-conflict"] == "stale"
    r = f2.post(f"/api/jobs/{JID}/segments/{sid}/decision", json={"action": "reject", "rev": rev})
    assert r.status_code == 409 and r.headers["x-manuvi-conflict"] == "stale"
    # a rule conflict (protected codes) is a 409 too, but not a "stale" one
    ok = f2.put(f"/api/jobs/{JID}/segments/{sid}", json={"target": "ba"})          # no rev: last writer wins
    assert ok.status_code == 200
    root = Path(__file__).resolve().parents[2] / "src/webapp/static"
    review, common = (root / "review.js").read_text(), (root / "common.js").read_text()
    assert "x-manuvi-conflict" in common.lower() and "Someone else changed this, reload." in review
    assert "setAttribute('role', 'alert')" in review and '<p class="err-text">Someone else' in review              # text + the warning glyph of .err-text, announced
    assert "rev: cur._rev" in review and "e.stale" in review


def test_worker_merge_keeps_a_decision_saved_during_its_upload(two):
    """Old behaviour: the worker read segments.json, a web edit landed, the worker's put erased that edit."""
    _, store, tmp = two
    remote = json.loads(store.get_bytes(JID, "segments.json"))
    pipeline_sid, human_sid = [s["id"] for s in remote if s["kind"] != "signal"][:2]
    web = SyncedJobStore(tmp / "ra", store, JID, ttl=0.0)
    hs = HookStore(store)
    w = Worker(None, hs, tmp / "work", profile="dev")
    work = tmp / "work" / JID
    work.mkdir(parents=True)
    local = [dict(s) for s in remote]
    next(s for s in local if s["id"] == pipeline_sid).update({"_pv": "v2", "target": "bản dịch mới"})  # re-run result
    (work / "segments.json").write_text(json.dumps(local))
    hs.hook = lambda: web.edit(human_sid, "Bản của người", actor="alice")   # a human saves while the worker uploads
    w._merge_segments(JID, work)
    final = {s["id"]: s for s in json.loads(store.get_bytes(JID, "segments.json"))}
    assert final[human_sid]["target"] == "Bản của người"                     # the human edit survived
    assert final[pipeline_sid]["target"] == "bản dịch mới"                   # and the worker's change landed
    assert {s["id"]: s["target"] for s in json.loads((work / "segments.json").read_text())}[human_sid] == "Bản của người"


def test_local_store_conflict_is_detected_across_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    st = LocalStore(tmp_path / "s")
    st.put_job(JobRecord(id=JID, name="x", owner="o", sections="23:1-2"))
    st.put_bytes(JID, "segments.json", b"start")
    _, tag = st.get_versioned(JID, "segments.json")

    def write(i):
        try:
            st.put_if_match(JID, "segments.json", str(i).encode(), tag)
            return True
        except VersionConflict:
            return False

    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(write, range(8)))
    assert results.count(True) == 1                                   # exactly one writer wins
