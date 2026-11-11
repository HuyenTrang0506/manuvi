"""Regression: eval fixtures must not be git-ignored (a `data/` rule once hid them from clones)."""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ["electrical_v1.jsonl", "electrical_v1.manifest.json", "electrical_v2.jsonl", "electrical_v2.manifest.json", "electrical_terms_proposed.csv"]


def test_eval_data_not_ignored():
    for f in FILES:
        p = f"src/eval/data/{f}"
        r = subprocess.run(["git", "check-ignore", "-q", p], cwd=ROOT)
        assert r.returncode == 1, f"{p} is git-ignored and would be missing from clones"


def test_baseline_not_ignored():
    r = subprocess.run(["git", "check-ignore", "-q", "src/eval/baselines/electrical_v2.recorded-reference.json"], cwd=ROOT)
    assert r.returncode == 1
