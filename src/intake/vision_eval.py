"""How to judge a vision model's reading quality: 10 synthetic image-label cases, scored on exact text match
and box IoU. Run it against any model behind the gateway alias `vision`:

    MANUVI_PROFILE=qa MANUVI_MODEL_VISION=ollama:qwen3-vl:8b python -m src.intake.vision_eval

The cases are generated (no customer data). Each page has a text layer paragraph plus raster labels, so the
region detection is exercised too. Suggested bar before trusting a model on the real manual: text exact match
>= 90%, mean IoU >= 0.6, no unmatched (invented) blocks, zero cases with a failed read. This is a smoke level
check on clean synthetic labels; real scanned diagrams will be worse, which is why uncertain blocks are
flagged for review instead of being trusted.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pymupdf

from .vision_detect import analyze_page
from .vision_read import VisionBlock, VisionConfig, VisionReader, iou, load_vision_config, norm_text

PROSE = "Check the hydraulic pump pressure before starting the engine and inspect the hoses for leaks."
MARGIN = 3.0


@dataclass(frozen=True)
class Case:
    id: str
    labels: tuple[str, ...]
    fontsize: float = 14.0
    rotation: int = 0
    cropbox: Optional[tuple[float, float, float, float]] = None


CASES: tuple[Case, ...] = (
    Case("c01-single-word", ("RELAY",)),
    Case("c02-two-words", ("CAB WORK LIGHT",)),
    Case("c03-harness-code", ("YT12-AB3C",)),
    Case("c04-part-number", ("YN20-01234-A",)),
    Case("c05-value-unit", ("24 V", "5 A")),
    Case("c06-connector", ("CN-19F", "SW-7", "SE-3")),
    Case("c07-small-font", ("WIPER MOTOR", "TO CAB"), fontsize=8.0),
    Case("c08-large-font", ("FUSE BOX",), fontsize=26.0),
    Case("c09-rotated-page", ("SPEAKER (R)", "TAPING"), rotation=90),
    Case("c10-cropbox-rotated", ("MAIN HARNESS", "GND"), rotation=270, cropbox=(30, 40, 560, 780)),
)


def build_case(case: Case, path: Path) -> list[tuple[str, pymupdf.Rect]]:
    """Write the case PDF; return [(label text, tight pdf-space rect)] read from the final page geometry."""
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_textbox(pymupdf.Rect(72, 60, 520, 130), PROSE, fontsize=11)
    placed = []
    y = 220.0
    for text in case.labels:
        w = pymupdf.get_text_length(text, "helv", case.fontsize) + 2 * MARGIN
        h = case.fontsize * 1.25 + 2 * MARGIN
        src = pymupdf.open()
        sp = src.new_page(width=w, height=h)
        sp.insert_text((MARGIN, MARGIN + case.fontsize), text, fontsize=case.fontsize)
        r = pymupdf.Rect(150, y, 150 + w, y + h)
        p.insert_image(r, stream=sp.get_pixmap(dpi=144).tobytes("png"))
        placed.append((text, r))
        y += h + 70
    if case.cropbox:
        p.set_cropbox(pymupdf.Rect(*case.cropbox))
    if case.rotation:
        p.set_rotation(case.rotation)
    final = [pymupdf.Rect(i["bbox"]) for i in p.get_image_info()]     # final pdf-space rects, insertion order
    doc.save(str(path))
    doc.close()
    return [(t, pymupdf.Rect(r.x0 + MARGIN, r.y0 + MARGIN, r.x1 - MARGIN, r.y1 - MARGIN))
            for (t, _), r in zip(placed, final)]


@dataclass
class CaseScore:
    case: str
    expected: int
    exact: int = 0
    ious: list[float] = field(default_factory=list)
    unmatched: int = 0               # blocks that match no expected label (invented text)
    flagged: int = 0
    failed: bool = False

    @property
    def mean_iou(self) -> float:
        return sum(self.ious) / self.expected if self.expected else 0.0


def score_case(case_id: str, truth: list[tuple[str, pymupdf.Rect]], blocks: list[VisionBlock],
               failed: bool = False) -> CaseScore:
    s = CaseScore(case_id, len(truth), flagged=sum(b.needs_review for b in blocks), failed=failed)
    free = list(blocks)
    for text, rect in truth:
        best, bi = None, 0.0
        for b in free:
            v = iou(rect, pymupdf.Rect(b.bbox))
            if v > bi:
                best, bi = b, v
        if best is None or bi < 0.1:
            s.ious.append(0.0)
            continue
        free.remove(best)
        s.ious.append(bi)
        s.exact += int(" ".join(best.text.split()) == " ".join(text.split()))
    s.unmatched = len(free)
    return s


@dataclass
class EvalReport:
    scores: list[CaseScore]

    @property
    def total(self) -> int:
        return sum(s.expected for s in self.scores)

    @property
    def exact_rate(self) -> float:
        return sum(s.exact for s in self.scores) / self.total if self.total else 0.0

    @property
    def mean_iou(self) -> float:
        return sum(sum(s.ious) for s in self.scores) / self.total if self.total else 0.0

    @property
    def unmatched(self) -> int:
        return sum(s.unmatched for s in self.scores)

    @property
    def failed_cases(self) -> int:
        return sum(s.failed for s in self.scores)

    def passed(self, exact: float = 0.9, iou_min: float = 0.6) -> bool:
        return self.exact_rate >= exact and self.mean_iou >= iou_min and self.unmatched == 0 and not self.failed_cases

    def to_markdown(self) -> str:
        L = ["| Case | Labels | Exact | Mean IoU | Invented | Flagged | Failed |", "|---|---|---|---|---|---|---|"]
        for s in self.scores:
            L.append(f"| {s.case} | {s.expected} | {s.exact} | {s.mean_iou:.2f} | {s.unmatched} | {s.flagged} | "
                     f"{'yes' if s.failed else ''} |")
        L.append(f"\nText exact match {self.exact_rate:.0%}, mean IoU {self.mean_iou:.2f}, invented blocks "
                 f"{self.unmatched}, failed cases {self.failed_cases}: {'PASS' if self.passed() else 'FAIL'}")
        return "\n".join(L)


def run_eval(gateway, work: Path, cfg: Optional[VisionConfig] = None, cases=CASES, job_id: str = "vision-eval") -> EvalReport:
    cfg = cfg or load_vision_config()
    reader = VisionReader(gateway, job_id, cfg)
    scores = []
    for case in cases:
        path = Path(work) / f"{case.id}.pdf"
        truth = build_case(case, path)
        with pymupdf.open(str(path)) as doc:
            d, tb = analyze_page(doc[0], cfg)
            res = reader.read_page(doc[0], d.regions if d.mode == "regions" else None, tb)
        scores.append(score_case(case.id, truth, res.blocks, res.failed))
    return EvalReport(scores)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Score a vision model on 10 synthetic image-label cases (local, $0).")
    ap.add_argument("--model", help="ollama:<tag>; default is the profile's vision model")
    ap.add_argument("--profile", default="qa")
    ap.add_argument("--smoke", action="store_true", help="only the first case, print PASS or FAIL")
    a = ap.parse_args(argv)
    from src.llm_gateway import Gateway
    env = {"MANUVI_PROFILE": a.profile}
    if a.model:
        env["MANUVI_MODEL_VISION"] = a.model
    with tempfile.TemporaryDirectory() as td:
        gw = Gateway(cache_path=Path(td) / "cache.sqlite", env=env)
        cases = CASES[1:2] if a.smoke else CASES
        try:
            rep = run_eval(gw, Path(td), cases=cases)
        except Exception as e:  # setup problems: class name only
            print(f"FAIL: the vision model could not be used ({type(e).__name__}). Is Ollama running and the model pulled?")
            return 2
    print(rep.to_markdown())
    ok = rep.passed(exact=1.0, iou_min=0.3) if a.smoke else rep.passed()
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
