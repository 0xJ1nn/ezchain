#!/usr/bin/env bash
# Single local gate. Mirrors the checks that can run on this machine.
# GitHub Actions is not available on this account; this script is the gate.
set -euo pipefail
cd "$(dirname "$0")/.."

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "1/6 agent stubs"
node tools/sync-agent-stubs.mjs --check

step "2/6 design tokens"
node tools/build-tokens.mjs --check

step "3/6 wireframe integrity"
node tools/check-wireframe.mjs

step "4/6 markdown links"
node tools/check-links.mjs

step "5/6 secret scan"
node tools/check-secrets.mjs

step "6/6 runtime gates (when present)"
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
if [ -f pnpm-workspace.yaml ]; then
  command -v pnpm >/dev/null 2>&1 || {
    echo "pnpm is required for the web gate: npm install -g pnpm@9"
    exit 1
  }
  pnpm -r lint
  pnpm -r typecheck
  pnpm --filter @ezchain/web build
  pnpm -r test
  ran=1
fi
if [ "$ran" -eq 0 ]; then
  echo "not present yet: ruff, mypy, pytest, eslint, tsc, playwright"
fi

printf '\n\033[32mci: ok\033[0m\n'
