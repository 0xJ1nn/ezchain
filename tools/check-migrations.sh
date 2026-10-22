#!/usr/bin/env bash
# Fresh-DB migration gate. Requires a disposable Postgres database URL whose name
# clearly marks it as a test database. Never point this at production.
set -euo pipefail
cd "$(dirname "$0")/.."

url="${EZCHAIN_TEST_DATABASE_URL:-}"
if [ -z "$url" ]; then
  echo "migrations: skipped (set EZCHAIN_TEST_DATABASE_URL to a disposable database)" >&2
  exit 0
fi
case "$url" in
  *test*|*scratch*|*_ci*) ;;
  *)
    echo "migrations: refusing $url (database name must contain test, scratch or _ci)" >&2
    exit 2
    ;;
esac
if [ ! -x .venv/bin/alembic ]; then
  echo "migrations: missing .venv/bin/alembic; run tools/setup-python.sh" >&2
  exit 1
fi

EZCHAIN_DATABASE_URL="$url" .venv/bin/alembic -c apps/api/alembic.ini upgrade head
EZCHAIN_DATABASE_URL="$url" .venv/bin/alembic -c apps/api/alembic.ini check
echo "migrations: ok (head + no pending autogenerate diff)"
