#!/usr/bin/env bash
# Run the API and the web app together for local development.
# The API must have Postgres running; the web app proxies /api to it.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ ! -x .venv/bin/uvicorn ]; then
  echo "missing .venv — run: PYTHON=python3.11 bash tools/setup-python.sh" >&2
  exit 1
fi
if ! pg_isready >/dev/null 2>&1; then
  echo "Postgres is not accepting connections on the default socket." >&2
  exit 1
fi

.venv/bin/uvicorn ezchain_api.main:app --host 127.0.0.1 --port 8000 --reload &
API_PID=$!
trap 'kill "$API_PID" 2>/dev/null || true' EXIT

echo "api:  http://127.0.0.1:8000/docs"
echo "web:  http://localhost:3000"
pnpm --filter @ezchain/web dev
