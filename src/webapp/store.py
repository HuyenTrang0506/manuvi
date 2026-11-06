"""Job-directory store: segments.json, terms.json, audit.jsonl (append-only)."""
from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path

STATUS_RANK = {"error": 0, "needs_review": 1, "warn": 2, "ok": 3}
STATUSES = set(STATUS_RANK)
KINDS = {"prose", "step", "signal", "table", "label"}
# Protected codes: part/wire codes (YT1234), values with units, Fig./page refs.
CODE_RE = re.compile(
    r"\b[A-Z]{1,3}\d{2,}[A-Z0-9-]*\b|\b\d+(?:[.,]\d+)?\s?(?:mm|kg|N·m|Nm|V|A|kPa|MPa|rpm|°C|Ω)\b"
    r"|\b(?:Fig\.|Page)\s?\d+(?:-\d+)?"
)


class ConflictError(Exception):
    pass


def protected_codes(text: str) -> list[str]:
    return sorted(set(m.group(0) for m in CODE_RE.finditer(text or "")))


def missing_codes(source: str, target: str) -> list[str]:
    return [c for c in protected_codes(source) if c not in (target or "")]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class JobStore:
    def __init__(self, jobdir: str | Path):
        self.dir = Path(jobdir)
        self.lock = threading.RLock()
        self.seg_path = self.dir / "segments.json"
        self.terms_path = self.dir / "terms.json"
        self.audit_path = self.dir / "audit.jsonl"
        if not self.seg_path.exists():
            raise FileNotFoundError(f"{self.seg_path} not found")
        self.segments: list[dict] = json.loads(self.seg_path.read_text("utf-8"))
        for s in self.segments:
            s.setdefault("decision", "pending")
            s.setdefault("issues", [])
            s.setdefault("risk", 0.0)
        self.terms: list[dict] = (
            json.loads(self.terms_path.read_text("utf-8")) if self.terms_path.exists() else []
        )
        for t in self.terms:
            t.setdefault("decision", "pending")

    # persistence
    def _write(self, path: Path, data) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)

    def _audit(self, action: str, item_type: str, item_id: str, old, new, extra=None) -> None:
        rec = {"ts": now(), "action": action, "type": item_type, "id": item_id, "old": old, "new": new}
        if extra:
            rec.update(extra)
        with self.audit_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    def audit(self) -> list[dict]:
        if not self.audit_path.exists():
            return []
        return [json.loads(l) for l in self.audit_path.read_text("utf-8").splitlines() if l.strip()]

    # segments
    def get(self, sid: str) -> dict:
        for s in self.segments:
            if s["id"] == sid:
                return s
        raise KeyError(sid)

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
        for s in self.segments:
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

    def edit(self, sid: str, target: str) -> dict:
        with self.lock:
            s = self.get(sid)
            old = s.get("target")
            if old == target:
                return s
            s["target"] = target
            # an edit invalidates a previous decision
            if s["decision"] != "pending":
                s["decision"] = "pending"
            self._write(self.seg_path, self.segments)
            self._audit("edit", "segment", sid, old, target)
            return s

    def decide(self, sid: str, action: str, explicit: bool = False, bulk: bool = False) -> dict:
        with self.lock:
            s = self.get(sid)
            old = {"decision": s["decision"], "status": s["status"]}
            if action == "approve":
                if s["kind"] == "signal" and (bulk or not explicit):
                    raise ConflictError("Safety (signal) segments need an explicit individual human approval.")
                miss = missing_codes(s["source"], s.get("target") or "")
                if miss:
                    raise ConflictError("Protected codes missing in target: " + ", ".join(miss))
                s["decision"] = "approved"
            elif action == "reject":
                s["decision"] = "rejected"
            elif action == "needs_review":
                s["decision"] = "pending"
                s["status"] = "needs_review"
            else:
                raise ValueError(action)
            self._write(self.seg_path, self.segments)
            self._audit(action, "segment", sid, old, {"decision": s["decision"], "status": s["status"]},
                        {"bulk": bulk, "explicit": explicit})
            return s

    def bulk_approve(self, ids: list[str]) -> dict:
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
            if s["status"] == "error":
                skipped.append({"id": sid, "reason": "status error: approve individually"})
                continue
            try:
                self.decide(sid, "approve", bulk=True)
                done.append(sid)
            except ConflictError as e:
                skipped.append({"id": sid, "reason": str(e)})
        return {"approved": done, "skipped": skipped}

    # terms
    def decide_term(self, tid: str, action: str, vi: str | None = None) -> dict:
        with self.lock:
            t = next((x for x in self.terms if x["id"] == tid), None)
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
            self._write(self.terms_path, self.terms)
            self._audit("term_" + action, "term", tid, old, {"vi": t.get("vi"), "decision": t["decision"]})
            return t

    def status(self) -> dict:
        by_status = {k: 0 for k in STATUS_RANK}
        by_decision = {"pending": 0, "approved": 0, "rejected": 0}
        for s in self.segments:
            by_status[s["status"]] = by_status.get(s["status"], 0) + 1
            by_decision[s["decision"]] += 1
        total = len(self.segments)
        decided = total - by_decision["pending"]
        safety = [s for s in self.segments if s["kind"] == "signal"]
        return {
            "total": total,
            "by_status": by_status,
            "by_decision": by_decision,
            "progress": round(decided / total, 3) if total else 0.0,
            "safety_total": len(safety),
            "safety_approved": sum(1 for s in safety if s["decision"] == "approved"),
            "terms_pending": sum(1 for t in self.terms if t["decision"] == "pending"),
            "cost_usd": 0.0,
            "profile": "dev",
        }
