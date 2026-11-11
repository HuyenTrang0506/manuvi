#!/usr/bin/env bash
# One command for the Section 43 pilot on the Mac: local Ollama, $0, safe to re-run (it resumes).
# usage: scripts/mac/run_pilot.sh [manual.pdf]
# env:   MANUVI_PILOT_MODEL (ollama:<tag>), MANUVI_PILOT_VISION_MODEL (ollama:<tag>), MANUVI_PILOT_PAGES (default 10),
#        MANUVI_PILOT_OUT
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PDF="${1:-${MANUVI_MANUAL_PDF:-$HOME/Project/translator-electronic/data/382014584-sk70sr-1es-shop-manual.pdf}}"
OUT="${MANUVI_PILOT_OUT:-$ROOT/workspace/pilot-section43}"
PAGES="${MANUVI_PILOT_PAGES:-10}"
OLLAMA_URL="http://127.0.0.1:11434"

die() { echo "ERROR: $*" >&2; exit 1; }

[ -f "$PDF" ] || die "manual not found: $PDF (pass the path as the first argument)"
command -v ollama >/dev/null 2>&1 || die "Ollama is not installed. Get it from https://ollama.com/download and run this again."

# 1. Python environment (3.13+), created once, updated on every run
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
MANUVI="$ROOT/.venv/bin/manuvi"

# 2. Model: explicit, else the profile default
MODEL="${MANUVI_PILOT_MODEL:-}"
if [ -z "$MODEL" ]; then
  MODEL="$("$MANUVI" pilot --show-model)"
fi
TAG="${MODEL#ollama:}"

# 3. Ollama server up, model present
if ! curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; then
  echo "Starting Ollama in the background..."
  mkdir -p "$OUT"
  nohup ollama serve >"$OUT/ollama-serve.log" 2>&1 &
  i=0
  until curl -sf "$OLLAMA_URL/api/tags" >/dev/null 2>&1; do
    i=$((i + 1))
    [ "$i" -le 30 ] || die "Ollama did not start; see $OUT/ollama-serve.log"
    sleep 1
  done
fi
if ! ollama show "$TAG" >/dev/null 2>&1; then
  echo "Downloading model $TAG (first run only)..."
  ollama pull "$TAG"
fi

# Vision model: only pages without a text layer (scans, pictures inside diagrams) need it. Not downloaded here.
VMODEL="${MANUVI_PILOT_VISION_MODEL:-$("$ROOT/.venv/bin/python" -c \
  "from src.llm_gateway.config import resolve_config as r; print(r({'MANUVI_PROFILE': 'qa'}).models['vision'])")}"
VTAG="${VMODEL#ollama:}"
if ! ollama show "$VTAG" >/dev/null 2>&1; then
  echo "Note: vision model $VTAG is not installed; pages without a text layer will fail. Try it with scripts/mac/vision_smoke.sh"
fi

# 4. Run (resumes automatically when the output folder already has finished pages)
status=0
"$MANUVI" pilot --pdf "$PDF" --section 43 --pages "$PAGES" --model "$MODEL" --vision-model "$VMODEL" --out "$OUT" || status=$?

if command -v open >/dev/null 2>&1; then
  open "$OUT"
else
  echo "Output: $OUT"
fi
exit "$status"
