"""scripts/mac/compare_models.sh with a fake `ollama` and a stub eval command (no model, no network)."""
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from src.eval import compare

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/mac/compare_models.sh"
BASELINE = ROOT / "src/eval/baselines/electrical_v2.recorded-reference.json"


def exe(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def env(tmp_path):
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    exe(bin_ / "ollama", """#!/bin/bash
case "$1" in
  pull) case "$2" in *nopull*) echo "pull model manifest: file does not exist" >&2; exit 1;; esac; exit 0;;
  list) echo "NAME ID SIZE MODIFIED"
        echo "small:7b abc 4.7 GB now"; echo "tiny:1b abc 700 MB now"; echo "huge:70b abc 40 GB now"
        echo "oom:12b abc 8.1 GB now"; echo "good:12b abc 8.1 GB now";;
  stop) exit 0;;
esac
""")
    exe(tmp_path / "evalstub", f"""#!/bin/bash
# args: --model ollama:<m> --out <dir> --run-id <id>
while [ "$#" -gt 0 ]; do case "$1" in --model) M="$2";; --out) O="$2";; --run-id) R="$2";; esac; shift; done
case "$M" in *oom*) echo "Error: model requires more system memory than is available" >&2; exit 1;; esac
mkdir -p "$O/$R"; cp "{BASELINE}" "$O/$R/report.json"
""")
    e = dict(os.environ, PATH=f"{bin_}:{os.environ['PATH']}", PYTHON=sys.executable, OUT_ROOT=str(tmp_path / "out"),
             EVAL_CMD=str(tmp_path / "evalstub"), NO_PULL="0")
    return e, tmp_path / "out"


def run(env, *models):
    return subprocess.run(["bash", str(SCRIPT), *models], env=env, cwd=ROOT, capture_output=True, text=True, timeout=120)


def test_pull_failure_memory_and_size_are_skipped_and_the_rest_continues(env):
    e, out = env
    r = run(e, "small:7b", "x-nopull:1b", "huge:70b", "oom:12b", "good:12b")
    assert r.returncode == 0, r.stderr
    md = next(out.glob("compare-*.md")).read_text()
    assert md.count("\n| `") == 5
    assert "`small:7b` | 100.0%" in md and "`good:12b` | 100.0%" in md
    assert "`x-nopull:1b` | skipped: pull failed" in md
    assert "`huge:70b` | skipped: needs 40 GB" in md
    assert "`oom:12b` | skipped: ran out of memory" in md
    assert "| model | protected |" in md


def test_small_model_in_mb_is_accepted(env):
    e, out = env
    run(e, "tiny:1b")
    assert "`tiny:1b` | 100.0%" in next(out.glob("compare-*.md")).read_text()


def test_default_list_is_the_verified_one():
    text = SCRIPT.read_text()
    line = next(x for x in text.splitlines() if x.startswith("DEFAULT_MODELS="))
    for tag in ("qwen2.5:7b", "translategemma:12b-it-q4_K_M", "gemma3:12b-it-q4_K_M", "qwen2.5:14b-instruct-q4_K_M"):
        assert tag in line


def test_script_is_bash32_safe():
    code = "\n".join(x for x in SCRIPT.read_text().splitlines() if not x.lstrip().startswith("#"))
    for banned in ("declare -A", "mapfile", "readarray", ",,}", "^^}", "&>>", "|&"):
        assert banned not in code, banned


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_clean():
    r = subprocess.run(["shellcheck", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_row_from_report():
    import json
    rep = json.loads(BASELINE.read_text())
    rep["timing"] = {"total_seconds": 150.0, "seconds_per_segment": 2.0}
    row = compare.row(rep, "m:1")
    assert row.startswith("| `m:1` | 100.0% | 100.0% | 100.0% | 100.0% | 0/75 (0.0%) | 0 |") and row.endswith("| 2.0 |")
    assert compare.header().splitlines()[0].count("|") == compare.skipped_row("m", "x").count("|")
