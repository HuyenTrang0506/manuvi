"""Offline demo pieces: a deterministic fake translator and synthetic chunks. $0, no network."""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..guard import load_signal_table
from ..llm_gateway.adapters import FakeAdapter, estimate_tokens
from .schema import CHUNKS_FILE

PH = re.compile(r"⟦P\d{3,}⟧")


class DemoTranslator(FakeAdapter):
    """Pretends to translate: 'VI: ' + text with signal words swapped for the fixed VI forms.

    Fault injection for tests/demo (only on the first attempt, i.e. when no FIX message is present):
      'DROPME'   in the text -> placeholders are dropped (QA error, fixed by the retry)
      'ALWAYSBAD' in the text -> placeholders are always dropped (ends needs_review)
    """

    name = "fake"

    def __init__(self, **kw):
        super().__init__(**kw)
        self.table = load_signal_table()
        self.log: list[tuple[str, bool]] = []  # (model_id, is_fix)

    def complete(self, req, model_id):
        self.calls += 1
        is_fix = any(m["role"] == "user" and m["content"].startswith("FIX") for m in req.messages)
        self.log.append((model_id, is_fix))
        src = next(m for m in req.messages if m["role"] == "user")
        text = src["content"]
        out = text
        for en, vi in self.table.items():
            out = re.sub(rf"(?<!\w){re.escape(en)}(?!\w)", vi, out)
        out = "VI: " + out
        if "ALWAYSBAD" in text or ("DROPME" in text and not is_fix):
            out = PH.sub("", out)
        return {"text": out, "tokens_in": estimate_tokens(req.messages), "tokens_out": estimate_tokens(out)}


SYNTHETIC = [
    ("Remove connector YT12-AB3C from the relay box.", "step", 1),
    ("Tighten bolt to 12 Nm. See Fig. 3-2.", "prose", 1),
    ("WARNING: Disconnect the battery before service. Part YN20-01234-A.", "prose", 1),
    ("Fuse", "label", 2),
    ("Relay | Rating | Circuit", "table", 2),
    ("Check wiring harness continuity (max 0.5 Ω) between pin 3 and pin 7.", "prose", 2),
    ("CAUTION: Hot surface.", "prose", 3),
    ("Install relay box YN20-01234-A and torque to 10 N·m. DROPME", "step", 3),
    ("Reconnect the battery terminal and test at 24 V.", "prose", 4),
]


def synthetic_chunks(n: int = len(SYNTHETIC)) -> list[dict]:
    rows = []
    for i in range(n):
        src, kind, page = SYNTHETIC[i % len(SYNTHETIC)]
        rows.append({"id": f"C{i + 1:03d}", "page": page + (i // len(SYNTHETIC)) * 4, "section": "23",
                     "source": src if i < len(SYNTHETIC) else f"{src} (#{i})", "kind": kind})
    return rows


def write_chunks(job_dir: Path, chunks: list[dict]) -> Path:
    job_dir.mkdir(parents=True, exist_ok=True)
    p = job_dir / CHUNKS_FILE
    p.write_text(json.dumps(chunks, ensure_ascii=False, indent=1), "utf-8")
    return p


def chunks_from_fixture(path: Path) -> list[dict]:
    """JSON list of chunk dicts, or a .txt where blank lines separate paragraphs (page 1)."""
    if path.suffix == ".json":
        return json.loads(path.read_text("utf-8"))
    paras = [p.strip() for p in path.read_text("utf-8").split("\n\n") if p.strip()]
    return [{"id": f"C{i + 1:03d}", "page": 1, "section": "", "source": p} for i, p in enumerate(paras)]
