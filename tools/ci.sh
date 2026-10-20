#!/usr/bin/env bash
# Single local gate. Mirrors .github/workflows/ci.yml for the artifacts that exist.
# Runtime gates (ruff/mypy/pytest/tsc/vitest) activate at M1 when code lands.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "1/5 agent stubs"
node tools/sync-agent-stubs.mjs --check

step "2/5 wireframe integrity"
node tools/check-wireframe.mjs

step "3/5 markdown links"
node tools/check-links.mjs

step "4/5 secret scan"
node tools/check-secrets.mjs

step "5/5 runtime gates (when present)"
ran=0
if command -v ruff >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  ruff check .
  ruff format --check .
  ran=1
fi
if command -v mypy >/dev/null 2>&1 && [ -f pyproject.toml ]; then
  mypy --strict packages services apps/api
  ran=1
fi
if command -v pytest >/dev/null 2>&1 && [ -d tests ]; then
  pytest -q
  ran=1
fi
if command -v pnpm >/dev/null 2>&1 && [ -f pnpm-workspace.yaml ]; then
  pnpm -r lint
  pnpm -r typecheck
  pnpm -r test
  ran=1
fi
if [ "$ran" -eq 0 ]; then
  echo "not present yet (M1): ruff, mypy, pytest, eslint, tsc, vitest"
fi

printf '\n\033[32mci: ok\033[0m\n'
