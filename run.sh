#!/usr/bin/env bash
# One-command run: ./run.sh  ->  http://localhost:8000
# Requires Python 3.12 (same as Docker image). scikit-learn/numpy pins
# are validated on 3.12; newer interpreters may lack wheels.
set -euo pipefail
cd "$(dirname "$0")/backend"
PY=""
for cand in python3.12 python3; do
  if command -v "$cand" >/dev/null 2>&1; then PY="$cand"; break; fi
done
if [ -z "$PY" ]; then echo "Python not found. Install Python 3.12." >&2; exit 1; fi
if [ ! -d .venv ]; then
  "$PY" -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"