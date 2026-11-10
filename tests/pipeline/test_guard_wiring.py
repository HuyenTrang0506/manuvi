"""The batch graph uses the shared guard path: signal split, script guard, never ship failed output."""
import json
from pathlib import Path

from src.llm_gateway.adapters import FakeAdapter
from src.pipeline.demo import write_chunks

from .conftest import full, segs

FX = json.loads((Path(__file__).resolve().parents[1] / "fixtures" / "q1_real_failures.json").read_text("utf-8"))
EL004 = "DANGER: The battery produces explosive gas. Keep sparks and flames away. Terminal voltage is 24 V."


class Scripted(FakeAdapter):
    def __init__(self, key):
        super().__init__()
        self.outs = iter(FX[key]["calls"])
        self.sent = []

    def complete(self, req, model_id):
        self.calls += 1
        self.sent.append(json.dumps(req.messages, ensure_ascii=False))
        return {"text": next(self.outs), "tokens_in": 5, "tokens_out": 5}


def one(tmp_path, source):
    d = tmp_path / "job1"
    write_chunks(d, [{"id": "A", "page": 1, "section": "", "source": source}])
    return d


def test_repaired_in_graph_and_signal_never_sent(tmp_path):
    d = one(tmp_path, EL004)
    ad = Scripted("EL-004")
    full(d, adapter=ad)
    s = segs(d)["A"]
    assert "DANGER" not in ad.sent[0] and ad.calls == 2
    assert s["target"] == ("NGUY HIỂM: Ắc quy sinh ra khí nổ. Giữ tia lửa và ngọn lửa tránh xa. "
                           "Điện áp tại đầu cực là 24 V.")
    assert s["status"] == "needs_review"   # safety text is still human-approved
    assert not any("SCR" in i for i in s["issues"])


def test_script_failure_is_never_shipped(tmp_path):
    d = one(tmp_path, EL004)
    ad = Scripted("EL-004-never-repaired")
    full(d, adapter=ad)
    s = segs(d)["A"]
    assert ad.calls == 3 and s["target"] == "" and s["status"] == "needs_review"
    assert any(i.startswith("SCR001") for i in s["issues"])


def test_signal_only_chunk_makes_no_call(tmp_path):
    d = one(tmp_path, "CAUTION:")
    ad = Scripted("EL-004")
    full(d, adapter=ad)
    assert ad.calls == 0 and segs(d)["A"]["target"] == "THẬN TRỌNG"
