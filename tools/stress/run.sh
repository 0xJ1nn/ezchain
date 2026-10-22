#!/usr/bin/env bash
# Run the PAPER stress harness from any worktree using the shared venv.
set -euo pipefail
cd "$(dirname "$0")/../.."
ROOT="$PWD"

find_python() {
  local candidate
  if [ -n "${EZCHAIN_PYTHON:-}" ] && [ -x "${EZCHAIN_PYTHON}" ]; then
    printf '%s' "${EZCHAIN_PYTHON}"
    return 0
  fi
  for candidate in "$ROOT/.venv/bin/python" "$ROOT/../ezchain/.venv/bin/python"; do
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  local common
  common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [ -n "$common" ]; then
    candidate="$(dirname "$common")/.venv/bin/python"
    if [ -x "$candidate" ]; then
      printf '%s' "$candidate"
      return 0
    fi
  fi
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [ -z "$PYTHON_BIN" ]; then
  echo "missing .venv — run: PYTHON=python3.11 bash tools/setup-python.sh" >&2
  exit 1
fi

export PYTHONPATH="$ROOT/packages/core-py/src:$ROOT/packages/adapters/src:$ROOT/apps/api/src:$ROOT/services/engine/src${PYTHONPATH:+:$PYTHONPATH}"
exec "$PYTHON_BIN" -m tools.stress "$@"
