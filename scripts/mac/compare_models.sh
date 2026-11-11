#!/usr/bin/env bash
# Compare local Ollama models on the frozen eval set and write workspace/eval/compare-<date>.md.
# Usage: scripts/mac/compare_models.sh [model ...]        (no args: the default list below)
# Env:   MAX_MODEL_GB=10.0  skip a model whose download is larger (M1 with ~11.8 GB usable: keep ~1.8 GB for context)
#        NO_PULL=1          do not pull, run only models that are already installed
#        PYTHON=python      interpreter of the project virtualenv
#        EVAL_CMD=...       override the eval command (tests); default: "$PYTHON -m src.cli eval run"
# bash 3.2 compatible (macOS): no associative arrays, no mapfile.
set -u

cd "$(dirname "$0")/../.." || exit 1
ROOT="$(pwd)"
PYTHON="${PYTHON:-python}"
MAX_MODEL_GB="${MAX_MODEL_GB:-10.0}"
NO_PULL="${NO_PULL:-0}"
EVAL_CMD="${EVAL_CMD:-$PYTHON -m src.cli eval run}"
OUT_ROOT="${OUT_ROOT:-$ROOT/workspace/eval}"
DATE="$(date +%Y-%m-%d)"
REPORT="$OUT_ROOT/compare-$DATE.md"

# Default list, verified on ollama.com/library on 2026-11-11 (all 4-bit, sizes are the download size):
#   qwen2.5:7b                       baseline, ~4.7 GB
#   translategemma:12b-it-q4_K_M     8.1 GB, Google translation model; English->Vietnamese is in its WMT24++ evaluation
#   gemma3:12b-it-q4_K_M             8.2 GB, general 12B
#   qwen2.5:14b-instruct-q4_K_M      9.0 GB, at the memory limit
DEFAULT_MODELS="qwen2.5:7b translategemma:12b-it-q4_K_M gemma3:12b-it-q4_K_M qwen2.5:14b-instruct-q4_K_M"

if [ "$#" -gt 0 ]; then
  MODELS="$*"
else
  MODELS="$DEFAULT_MODELS"
fi

mkdir -p "$OUT_ROOT"

slug() { printf '%s' "$1" | tr -c 'A-Za-z0-9\n' '-' | sed 's/-*$//'; }

# Installed size of a model in GB (empty when unknown). `ollama list` prints "NAME ID 8.1 GB ...".
model_gb() {
  ollama list 2>/dev/null | awk -v m="$1" '$1 == m { v = $3; u = $4; if (u == "MB") v = v / 1024; else if (u == "KB") v = v / 1048576; print v; exit }'
}

too_big() {  # $1 size, returns 0 when larger than the limit
  awk -v s="$1" -v m="$MAX_MODEL_GB" 'BEGIN { exit !(s + 0 > m + 0) }'
}

add_skipped() { "$PYTHON" -m src.eval.compare skipped "$1" "$2" >> "$REPORT"; echo "skip $1: $2"; }

{
  echo "# Model comparison $DATE"
  echo
  echo "Frozen eval set, same guard and prompt for every model, cost \$0 (local Ollama). Hard metrics are computed on"
  echo "accepted output only; \`routed\` is the share of segments the guard sent to human review. Limit used to skip"
  echo "large models: ${MAX_MODEL_GB} GB download."
  echo
  "$PYTHON" -m src.eval.compare header
} > "$REPORT" || exit 1

if ! command -v ollama >/dev/null 2>&1; then
  echo "ollama not found in PATH" >&2
  for m in $MODELS; do add_skipped "$m" "ollama not installed"; done
  exit 1
fi

for m in $MODELS; do
  echo "== $m"
  if [ "$NO_PULL" != "1" ]; then
    if ! ollama pull "$m"; then
      add_skipped "$m" "pull failed (tag missing or no network)"
      continue
    fi
  fi
  gb="$(model_gb "$m")"
  if [ -z "$gb" ]; then
    add_skipped "$m" "not installed"
    continue
  fi
  if too_big "$gb"; then
    add_skipped "$m" "needs ${gb} GB, above the ${MAX_MODEL_GB} GB limit"
    continue
  fi
  run_id="cmp-$DATE-$(slug "$m")"
  log="$OUT_ROOT/$run_id.log"
  # shellcheck disable=SC2086  # EVAL_CMD is a command line on purpose
  $EVAL_CMD --model "ollama:$m" --out "$OUT_ROOT" --run-id "$run_id" > "$log" 2>&1
  status=$?
  if [ -f "$OUT_ROOT/$run_id/report.json" ] && [ "$status" -eq 0 ]; then
    "$PYTHON" -m src.eval.compare row "$OUT_ROOT/$run_id/report.json" "$m" >> "$REPORT"
    echo "done $m"
  elif grep -qiE 'memory|out of memory|OOM|killed|resource limit' "$log"; then
    add_skipped "$m" "ran out of memory (see $log)"
  else
    add_skipped "$m" "run failed, exit $status (see $log)"
  fi
  ollama stop "$m" >/dev/null 2>&1 || true   # free the memory before the next model
done

{
  echo
  echo "Run ids: \`workspace/eval/cmp-$DATE-*\` (report.md per model). Decision rule: hard metrics 100%, routed <= 5%,"
  echo "glossary hit >= 97%, leakage <= 0.5%, and a speed that finishes 30 pages overnight."
} >> "$REPORT"
echo "wrote $REPORT"
