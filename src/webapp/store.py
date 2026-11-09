"""Job-directory store: segments.json, terms.json, audit.jsonl (append-only)."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import threading
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.common.filelock import file_lock
from src.guard import QAContext, Severity, mask, qa_check

STATUS_RANK = {"error": 0, "needs_review": 1, "warn": 2, "ok": 3}
STATUSES = set(STATUS_RANK)
KINDS = {"prose", "step", "signal", "table", "label"}


class ConflictError(Exception):
    pass


class StaleError(ConflictError):
    """The caller acted on an older version of the segment (or the file changed under a concurrent write)."""


def _tokens(text: str) -> Counter:
    """Multiset of protected tokens (patterns.yaml via guard.mask)."""
    try:
        return Counter(mask(text or "")[1].values())
    except ValueError:  # placeholder brackets in text: cannot be verified
        return Counter({"<unmaskable>": 1})


def missing_codes(source: str, target: str) -> list[str]:
    return sorted((_tokens(source) - _tokens(target)).elements())


def qa_errors(source: str, target: str) -> list:
    try:
        issues = qa_check(source, target or "", QAContext())
    except ValueError as e:
        return [{"rule_id": "QA000", "severity": "error", "message": str(e)}]
    return [i.model_dump(mode="json") for i in issues]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JobStore:
    """All mutations: lock -> reload from disk -> audit (fsync) -> atomic write."""

    def __init__(self, jobdir: str | Path):
        self.dir = Path(jobdir)
        self.lock = threading.RLock()
        self.seg_path = self.dir / "segments.json"
        self.terms_path = self.dir / "terms.json"
        self.audit_path = self.dir / "audit.jsonl"
        if not self.seg_path.exists():
            raise FileNotFoundError(f"{self.seg_path} not found")
        self.nonces: dict[str, str] = {}  # segment id -> single-use confirm nonce

    # --- disk io
    def _load_segments(self) -> list[dict]:
        segs = json.loads(self.seg_path.read_text("utf-8"))
        for s in segs:
            s.setdefault("decision", "pending")
            s.setdefault("issues", [])
            s.setdefault("risk", 0.0)
        return segs

    def _load_terms(self) -> list[dict]:
        if not self.terms_path.exists():
            return []
        terms = json.loads(self.terms_path.read_text("utf-8"))
        for t in terms:
            t.setdefault("decision", "pending")
        return terms

    @property
    def segments(self) -> list[dict]:
        return self._load_segments()

    @property
    def terms(self) -> list[dict]:
        return self._load_terms()

    @staticmethod
    def _write(path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def _audit(self, action, item_type, item_id, old, new, extra=None, actor=None) -> None:
        """Append a hash-chained record and fsync. Call while holding the lock."""
        prev = "0" * 64
        if self.audit_path.exists():
            lines = [l for l in self.audit_path.read_text("utf-8").splitlines() if l.strip()]
            if lines:
                prev = hashlib.sha256(lines[-1].encode("utf-8")).hexdigest()
        rec = {"ts": now(), "action": action, "type": item_type, "id": item_id, "old": old, "new": new,
               "prev_hash": prev}
        if actor:
            rec["user"] = actor
        if extra:
            rec.update(extra)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def audit(self) -> list[dict]:
        if not self.audit_path.exists():
            return []
        return [json.loads(l) for l in self.audit_path.read_text("utf-8").splitlines() if l.strip()]

    def verify_audit(self) -> bool:
        prev = "0" * 64
        for line in self.audit_path.read_text("utf-8").splitlines():
            if not line.strip():
                continue
            if json.loads(line)["prev_hash"] != prev:
                return False
            prev = hashlib.sha256(line.encode("utf-8")).hexdigest()
        return True

    # --- segments
    def get(self, sid: str) -> dict:
        for s in self._load_segments():
            if s["id"] == sid:
                return s
        raise KeyError(sid)

    def issue_nonce(self, sid: str) -> str:
        n = secrets.token_urlsafe(16)
        self.nonces[sid] = n
        return n

    @staticmethod
    def sort_key(s: dict):
        return (
            0 if s.get("kind") == "signal" else 1,
            STATUS_RANK.get(s.get("status"), 9),
            -float(s.get("risk", 0)),
            s["id"],
        )

    def queue(self, status=None, kind=None, section=None, decision=None, min_risk=None, q=None):
        out = []
        for s in self._load_segments():
            if status and s["status"] != status:
                continue
            if kind and s["kind"] != kind:
                continue
            if section and s.get("section") != section:
                continue
            if decision and s["decision"] != decision:
                continue
            if min_risk is not None and s["risk"] < min_risk:
                continue
            if q and q.lower() not in (s["source"] + " " + (s.get("target") or "")).lower():
                continue
            out.append(s)
        return sorted(out, key=self.sort_key)

    def _find(self, segs, sid):
        for s in segs:
            if s["id"] == sid:
                return s
        raise KeyError(sid)

    @staticmethod
    def _check_rev(s: dict, rev: int | None) -> None:
        """Optimistic concurrency: `rev` is the `_rev` the caller last saw; every change bumps it."""
        if rev is not None and int(rev) != int(s.get("_rev", 0)):
            raise StaleError("Someone else changed this, reload.")

    def edit(self, sid: str, target: str, actor: str | None = None, rev: int | None = None) -> dict:
        with self.lock, file_lock(self.seg_path):
            segs = self._load_segments()
            s = self._find(segs, sid)
            self._check_rev(s, rev)
            old = s.get("target")
            if old == target:
                return s
            old_state = {"decision": s["decision"], "status": s["status"]}
            s["target"] = target
            s["_rev"] = int(s.get("_rev", 0)) + 1
            s["decision"] = "pending"  # an edit invalidates a previous decision
            errs = [i for i in qa_errors(s["source"], target) if i["severity"] == "error"]
            s["qa_issues"] = errs
            if errs:
                s["status"] = "error"
            self._audit("edit", "segment", sid, old, target,
                        {"old_state": old_state, "new_state": {"decision": s["decision"], "status": s["status"]},
                         "qa_errors": [i["rule_id"] for i in errs]}, actor=actor)
            self._write(self.seg_path, segs)
            return s

    def decide(self, sid: str, action: str, nonce: str | None = None, bulk: bool = False,
               actor: str | None = None, rev: int | None = None) -> dict:
        with self.lock, file_lock(self.seg_path):
            segs = self._load_segments()
            s = self._find(segs, sid)
            self._check_rev(s, rev)
            old = {"decision": s["decision"], "status": s["status"]}
            explicit = False
            if action == "approve":
                if s["kind"] == "signal":
                    expected = self.nonces.get(sid)
                    if bulk or not nonce or not expected or not secrets.compare_digest(nonce, expected):
                        raise ConflictError("Safety (signal) segments need an explicit individual human approval.")
                    explicit = True
                target = s.get("target") or ""
                miss = missing_codes(s["source"], target)
                if miss:
                    raise ConflictError("Protected codes missing or changed in target: " + ", ".join(miss))
                errs = [i for i in qa_errors(s["source"], target) if i["severity"] == "error"]
                if errs:
                    raise ConflictError("QA errors: " + "; ".join(f"{i['rule_id']} {i['message']}" for i in errs))
                s["decision"] = "approved"
            elif action == "reject":
                s["decision"] = "rejected"
            elif action == "needs_review":
                s["decision"] = "pending"
                s["status"] = "needs_review"
            else:
                raise ValueError(action)
            s["_rev"] = int(s.get("_rev", 0)) + 1
            self._audit(action, "segment", sid, old, {"decision": s["decision"], "status": s["status"]},
                        {"bulk": bulk, "explicit": explicit}, actor=actor)
            self._write(self.seg_path, segs)
            if explicit:
                self.nonces.pop(sid, None)
            return s

    def comment(self, sid: str, text: str, actor: str | None = None) -> dict:
        """A review note. Goes to the audit log only; it never changes status, target or decision."""
        text = " ".join(text.split())[:500]
        if not text:
            raise ValueError("empty comment")
        with self.lock, file_lock(self.seg_path):
            self._find(self._load_segments(), sid)
            self._audit("comment", "segment", sid, None, text, actor=actor)
        return {"id": sid, "comment": text}

    def bulk_approve(self, ids: list[str], actor: str | None = None) -> dict:
        done, skipped = [], []
        for sid in ids:
            try:
                s = self.get(sid)
            except KeyError:
                skipped.append({"id": sid, "reason": "not found"})
                continue
            if s["kind"] == "signal":
                skipped.append({"id": sid, "reason": "safety segment: approve individually"})
                continue
            if s["status"] != "ok":
                skipped.append({"id": sid, "reason": f"status {s['status']}: approve individually"})
                continue
            try:
                self.decide(sid, "approve", bulk=True, actor=actor)
                done.append(sid)
            except ConflictError as e:
                skipped.append({"id": sid, "reason": str(e)})
        return {"approved": done, "skipped": skipped}

    # --- terms
    def decide_term(self, tid: str, action: str, vi: str | None = None, actor: str | None = None) -> dict:
        with self.lock, file_lock(self.terms_path):
            terms = self._load_terms()
            t = next((x for x in terms if x["id"] == tid), None)
            if t is None:
                raise KeyError(tid)
            old = {"vi": t.get("vi"), "decision": t["decision"]}
            if action == "edit":
                if vi is None:
                    raise ValueError("vi required")
                t["vi"] = vi
                t["decision"] = "approved"
            elif action in ("approve", "reject"):
                t["decision"] = "approved" if action == "approve" else "rejected"
            else:
                raise ValueError(action)
            self._audit("term_" + action, "term", tid, old, {"vi": t.get("vi"), "decision": t["decision"]},
                        actor=actor)
            self._write(self.terms_path, terms)
            return t

    def status(self) -> dict:
        segs = self._load_segments()
        terms = self._load_terms()
        by_status = {k: 0 for k in STATUS_RANK}
        by_decision = {"pending": 0, "approved": 0, "rejected": 0}
        for s in segs:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            by_decision[s["decision"]] += 1
        total = len(segs)
        decided = total - by_decision["pending"]
        safety = [s for s in segs if s["kind"] == "signal"]
        return {
            "total": total,
            "by_status": by_status,
            "by_decision": by_decision,
            "progress": round(decided / total, 3) if total else 0.0,
            "safety_total": len(safety),
            "safety_approved": sum(1 for s in safety if s["decision"] == "approved"),
            "terms_pending": sum(1 for t in terms if t["decision"] == "pending"),
            "cost_usd": 0.0,
            "profile": "dev",
        }
