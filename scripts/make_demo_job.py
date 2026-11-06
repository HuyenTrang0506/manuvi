"""Generate a synthetic demo job (no customer data) into workspace/demo_job/."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path

PROSE = [
    ("Check the oil level before starting the engine.", "Kiểm tra mức dầu trước khi khởi động động cơ."),
    ("Clean the air filter element with compressed air.", "Làm sạch lõi lọc gió bằng khí nén."),
    ("The hydraulic pump supplies pressure to the control valve.", "Bơm thủy lực cấp áp suất cho van điều khiển."),
    ("Inspect the hoses for cracks or leaks.", "Kiểm tra các ống mềm xem có nứt hoặc rò rỉ không."),
    ("Replace the fuel filter every 500 hours.", "Thay lọc nhiên liệu sau mỗi 500 giờ."),
]
STEPS = [
    ("Disconnect connector YT1234 from the controller.", "Ngắt giắc YT1234 khỏi bộ điều khiển."),
    ("Measure the resistance between pin 3 and pin 7 of connector YT2210.", "Đo điện trở giữa pin 3 và pin 7 của giắc YT2210."),
    ("Tighten the bolt to 45 N·m (see Fig. 12).", "Siết bu lông đến 45 N·m (xem Fig. 12)."),
    ("Remove the cover and check wire YT3305 for damage.", "Tháo nắp và kiểm tra dây YT3305 xem có hư hỏng không."),
]
SIGNAL = [
    ("WARNING: Lower the attachment to the ground before servicing.", "CẢNH BÁO: Hạ cụm công tác xuống đất trước khi bảo dưỡng."),
    ("DANGER: Disconnect the battery before touching terminal YT9001.", "NGUY HIỂM: Ngắt ắc quy trước khi chạm vào cực YT9001."),
    ("CAUTION: Hot surfaces. Wait 30 minutes after shutdown.", "THẬN TRỌNG: Bề mặt nóng. Chờ 30 phút sau khi tắt máy."),
]
LABELS = [("Fuse box", "Hộp cầu chì"), ("Relay", "Rơ le"), ("Ground", "Mát")]
TABLE = [("Voltage: 24 V | Current: 5 A", "Điện áp: 24 V | Dòng điện: 5 A")]


def main(out: str = "workspace/demo_job") -> None:
    rnd = random.Random(7)
    segs: list[dict] = []

    def add(kind, pair, section, status="ok", issues=None, risk=None):
        i = len(segs) + 1
        segs.append({"id": f"S{i:03d}", "page": 1 + i // 4, "section": section, "source": pair[0], "target": pair[1],
                     "status": status, "issues": issues or [], "risk": round(risk if risk is not None else rnd.uniform(0.02, 0.3), 2),
                     "kind": kind})

    for n in range(12):
        add("prose", PROSE[n % len(PROSE)], "Maintenance")
    for n in range(10):
        add("step", STEPS[n % len(STEPS)], "Electrical")
    for p in SIGNAL:
        add("signal", p, "Safety", "warn", ["Safety block: human approval required"], 0.9)
    for p in LABELS * 2:
        add("label", p, "Diagrams")
    add("table", TABLE[0], "Specifications")
    # defects
    segs[1].update(status="warn", issues=["Terminology: 'air filter' vs glossary"], risk=0.55)
    segs[4].update(status="warn", issues=["Long sentence, check fluency"], risk=0.45)
    segs[7].update(status="error", issues=["Possible mistranslation of 'control valve'"], risk=0.8)
    segs[13].update(status="error", issues=["Protected code YT2210 missing in target"], target="Đo điện trở giữa pin 3 và pin 7 của giắc.", risk=0.95)
    segs[15].update(status="needs_review", issues=["Value/unit uncertain"], risk=0.6)
    segs[19].update(status="warn", issues=["Fig. reference altered?"], risk=0.5)
    for i, p in enumerate(PROSE * 2):
        add("prose", (p[0].replace("oil", "coolant") if i % 2 else p[0], p[1]), "Operation", "ok" if i % 3 else "warn",
            None, None)
    segs[:] = segs[:40]
    terms = [{"id": f"T{i+1:02d}", "en": en, "vi": vi, "decision": "pending"} for i, (en, vi) in enumerate([
        ("control valve", "van điều khiển"), ("hydraulic pump", "bơm thủy lực"), ("air filter", "lọc gió"),
        ("attachment", "cụm công tác"), ("terminal", "cực"), ("relay", "rơ le")])]
    d = Path(out)
    d.mkdir(parents=True, exist_ok=True)
    (d / "segments.json").write_text(json.dumps(segs, ensure_ascii=False, indent=1), "utf-8")
    (d / "terms.json").write_text(json.dumps(terms, ensure_ascii=False, indent=1), "utf-8")
    (d / "audit.jsonl").write_text("", "utf-8")
    print(f"wrote {len(segs)} segments, {len(terms)} terms to {d}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
