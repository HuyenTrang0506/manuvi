#!/usr/bin/env bash
# Try the local vision model: renders one synthetic page with a picture label, lets the model read it and prints
# PASS or FAIL. Local only ($0), no customer data. Safe to re-run. Works with the macOS bash 3.2.
# usage: scripts/mac/vision_smoke.sh [--full] [ollama:<tag>]
#   --full   score all 10 synthetic cases (text exact match and box overlap) instead of one
# env:   MANUVI_VISION_MODEL (ollama:<tag>), default is the qa vision model from config/profiles.yaml
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
OLLAMA_URL="http://127.0.0.1:11434"
FULL=""
MODEL="${MANUVI_VISION_MODEL:-}"

die() { echo "FAIL: $*" >&2; exit 1; }

for arg in "$@"; do
  case "$arg" in
    --full) FULL="yes" ;;
    ollama:*) MODEL="$arg" ;;
    *) die "unknown argument: $arg (use --full and/or ollama:<tag>)" ;;
  esac
done

command -v ollama >/dev/null 2>&1 || die "Ollama is not installed. Get it from https://ollama.com/download and run this again."

# Python environment (3.13+), same one as the pilot
PY=""
for c in python3.13 python3.14 python3; do
  if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 13) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
[ -n "$PY" ] || die "Python 3.13 or newer is required (brew install python@3.13)."
[ -x "$ROOT/.venv/bin/python" ] || "$PY" -m venv "$ROOT/.venv"
"$ROOT/.venv/bin/python" -m pip install --quiet --upgrade pip
"$ROOT/.venv/bin/python" -m pip install --quiet -e "$ROOT"
VPY="$ROOT/.venv/bin/python"

if [ -z "$MODEL" ]; then
  MODEL="$(cd "$ROOT" && "$VPY" -c "from src.llm_gateway.config import resolve_config as r; print(r({'MANUVI_PROFILE': 'qa'}).models['vision'])")"
fi
TAG="${MODEL#ollama:}"
echo "Vision model: $TAG"

# Ollama server up, model present
if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  echo "Starting Ollama in the background..."
  LOG="${TMPDIR:-/tmp}/manuvi-ollama-serve.log"
  nohup ollama serve >"$LOG" 2>&1 &
  i=0
  until curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -le 30 ] || die "Ollama did not start; see $LOG"
    sleep 1
  done
fi
if ! ollama show "$TAG" >/dev/null 2>&1; then
  echo "Downloading $TAG (first run only; the 8b model is about 6 GB)..."
  ollama pull "$TAG" || die "could not download $TAG (needs Ollama 0.12.7 or newer for Qwen3-VL; update Ollama)"
fi

# Render the synthetic page(s), run the model, score. The python module prints a table and PASS or FAIL.
cd "$ROOT"
set +e
if [ -n "$FULL" ]; then
  OUT="$("$VPY" -m src.intake.vision_eval --model "$MODEL")"
else
  OUT="$("$VPY" -m src.intake.vision_eval --smoke --model "$MODEL")"
fi
CODE=$?
set -e
echo "$OUT"
if [ "$CODE" -eq 0 ]; then
  echo "PASS: $TAG read the synthetic page correctly."
  exit 0
fi
echo "FAIL: $TAG did not read the synthetic page correctly (exit $CODE)."
echo "If the text is right but the boxes are not, set bbox_scale: 1000 in config/vision.yaml and run again."
exit 1
