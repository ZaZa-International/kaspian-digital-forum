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
if [ -x ../.venv/bin/python ]; then
  SCT_VENV=../.venv
else
  SCT_VENV=.venv
fi
if [ ! -x "$SCT_VENV/bin/python" ]; then
  "$PY" -m venv "$SCT_VENV"
fi
# Keep an existing checkout usable after dependency changes.
"$SCT_VENV/bin/python" -m pip install -q -r requirements.txt
exec "$SCT_VENV/bin/uvicorn" app.main:app --host "${SCT_HOST:-127.0.0.1}" --port "${PORT:-8000}"
