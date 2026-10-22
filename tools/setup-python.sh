#!/usr/bin/env bash
# Create the Python 3.11 environment and install every local package editable.
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.11}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "python3.11 is required (set PYTHON=/path/to/python3.11)" >&2
  exit 1
fi

"$PYTHON" -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install --no-build-isolation --no-deps \
  -e packages/core-py \
  -e packages/adapters \
  -e apps/api \
  -e services/engine

echo "python: ok — run .venv/bin/pytest"
