#!/usr/bin/env bash
# Create the Python 3.11 environment from the committed pins.
#
# The locks are the install source: `requirements.txt` declares ranges and
# `requirements.lock` resolves them to exact versions with sha256 hashes. Installing
# the ranges would let two machines run different code from the same commit.
#
#   bash tools/setup-python.sh                 # dev + test toolchain, from the lock
#   bash tools/setup-python.sh --offline       # from $EZCHAIN_WHEELHOUSE, no network
#   bash tools/setup-python.sh --audit         # also run the advisory check
set -euo pipefail
cd "$(dirname "$0")/.."

PYTHON="${PYTHON:-python3.11}"
offline=0
audit=0
for arg in "$@"; do
  case "$arg" in
    --offline) offline=1 ;;
    --audit) audit=1 ;;
    --help|-h)
      sed -n '2,10p' "$0"
      exit 0
      ;;
    *)
      echo "python: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "python3.11 is required (set PYTHON=/path/to/python3.11)" >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  "$PYTHON" -m venv .venv
fi
.venv/bin/python -m pip install --upgrade pip --quiet --disable-pip-version-check

install=(--require-hashes --disable-pip-version-check -r requirements.lock)
if [ "$offline" -eq 1 ]; then
  install+=(--no-index --find-links "${EZCHAIN_WHEELHOUSE:?--offline needs EZCHAIN_WHEELHOUSE}")
fi
.venv/bin/python -m pip install "${install[@]}"

# The local packages are editable and resolve to this checkout; --no-deps keeps the lock
# above the single source of their transitive dependencies.
.venv/bin/pip install --no-build-isolation --no-deps \
  -e packages/core-py \
  -e packages/adapters \
  -e packages/sdk-py \
  -e apps/api \
  -e apps/mcp \
  -e services/engine \
  -e services/executor \
  -e services/ingest \
  -e services/agent-runtime

.venv/bin/python tools/check_pins.py --fast

if [ "$audit" -eq 1 ]; then
  status=0
  .venv/bin/python tools/audit_pins.py --python || status=$?
  if [ "$status" -eq 2 ]; then
    echo "python: the advisory check could not run (no network?) — this is not a pass" >&2
  fi
  [ "$status" -eq 0 ] || exit "$status"
fi

echo "python: ok — run .venv/bin/pytest"
