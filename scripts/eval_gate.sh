#!/usr/bin/env bash
# Offline quality gate for CI / worker hooks. Exit 0 = pass, 1 = regression, 2 = eval set modified.
# MODEL=fake (default) is expected to FAIL against the shipped baseline; CI uses MODEL=recorded with a fixture.
# Usage: scripts/eval_gate.sh [fixture.jsonl]    (no fixture: builds the reference fixture, a harness self-test)
set -euo pipefail
cd "$(dirname "$0")/.."
FIXTURE="${1:-}"
if [ -z "$FIXTURE" ]; then
  FIXTURE="$(mktemp -d)/reference.jsonl"
  python -m src.cli eval make-fixture "$FIXTURE"
fi
python -m src.cli eval run --model "${MODEL:-recorded}" --fixture "$FIXTURE" \
  --baseline "${BASELINE:-src/eval/baselines/electrical_v2.recorded-reference.json}" "${@:2}"
