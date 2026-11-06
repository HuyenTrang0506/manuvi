import json
from pathlib import Path

import pytest

from src.pipeline.demo import DemoTranslator, synthetic_chunks, write_chunks
from src.pipeline.runner import make_gateway, run_job

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture
def job(tmp_path):
    d = tmp_path / "job1"
    write_chunks(d, synthetic_chunks(9))
    return d


def run(job_dir, adapter=None, **kw):
    adapter = adapter or DemoTranslator()
    gw = make_gateway(job_dir, "dev", adapters={"fake": adapter})
    kw.setdefault("batch_size", 4)
    return run_job(job_dir, gateway=gw, job_id="job1", **kw), adapter


def full(job_dir, **kw):
    return run(job_dir, approve_batches=True, approve_safety=True, **kw)


def segs(job_dir):
    return {s["id"]: s for s in json.loads((Path(job_dir) / "segments.json").read_text("utf-8"))}
