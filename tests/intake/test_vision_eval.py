import json

import pymupdf

from src.intake.vision_eval import CASES, CaseScore, EvalReport, build_case, run_eval, score_case
from src.intake.vision_read import VisionBlock, VisionConfig, to_visible

from .vision_helpers import ScriptedVLM, make_gateway


def run(tmp_path, mutate=None, shift=0.0):
    """Run every case with an oracle that knows where the labels are; optionally with mistakes."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    scores = []
    for c in CASES:
        t = build_case(c, tmp_path / f"{c.id}.pdf")
        with pymupdf.open(str(tmp_path / f"{c.id}.pdf")) as doc:
            vis = [(mutate(x) if mutate else x, to_visible(doc[0], r)) for x, r in t]
        adapter = ScriptedVLM(vis)
        if shift:
            orig = adapter.complete

            def shifted(req, model_id, orig=orig):
                out = orig(req, model_id)
                d = json.loads(out["text"])
                for b in d["blocks"]:
                    b["bbox"] = [v + shift for v in b["bbox"]]
                out["text"] = json.dumps(d)
                return out
            adapter.complete = shifted
        rep = run_eval(make_gateway(tmp_path / f"gw-{c.id}", adapter), tmp_path, VisionConfig(), cases=(c,))
        scores += rep.scores
    return EvalReport(scores)


def test_ten_cases_and_geometry():
    assert len(CASES) == 10 and len({c.id for c in CASES}) == 10


def test_perfect_reader_scores_full_marks(tmp_path):
    rep = run(tmp_path / "ok")
    assert len(rep.scores) == 10 and rep.total == sum(len(c.labels) for c in CASES)
    assert rep.exact_rate == 1.0 and rep.mean_iou > 0.95 and rep.unmatched == 0 and rep.passed()
    assert "PASS" in rep.to_markdown()


def test_wrong_text_and_shifted_boxes_lower_the_scores(tmp_path):
    rep = run(tmp_path / "a", mutate=lambda t: t[:-1])
    assert rep.exact_rate == 0.0 and not rep.passed()
    rep2 = run(tmp_path / "b", shift=0.2)
    assert rep2.mean_iou < rep.mean_iou and not rep2.passed()


def test_score_case_counts_invented_and_missing():
    truth = [("RELAY", pymupdf.Rect(0, 0, 100, 20)), ("FUSE", pymupdf.Rect(0, 50, 100, 70))]
    blocks = [VisionBlock("RELAY", (0, 0, 100, 20), confidence=0.9),
              VisionBlock("INVENTED", (300, 300, 400, 320), confidence=0.9)]
    s = score_case("t", truth, blocks)
    assert s.exact == 1 and s.unmatched == 1 and s.ious == [1.0, 0.0] and s.mean_iou == 0.5
    assert isinstance(s, CaseScore)
