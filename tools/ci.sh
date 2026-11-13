#!/usr/bin/env bash
# Single local gate. Mirrors the checks that can run on this machine.
# GitHub Actions is not available on this account; this script is the gate.
set -euo pipefail
cd "$(dirname "$0")/.."

# The gate must be hermetic. A developer running `verify.sh --live` exports the deployment
# token for the live steps; unit tests assert the unset development default, so remove it
# (and any signing secret) from the test environment. PYTHONPATH can point imports at a
# second checkout, and NODE_ENV=production changes what Next builds and what pnpm installs.
# The caller's environment is not modified: this script runs in a child shell.
unset EZCHAIN_API_TOKEN EZCHAIN_STRESS_API_TOKEN EZCHAIN_EXECUTOR_SECRET PYTHONPATH NODE_ENV

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
# The project venv is the gate. Global tools run only when no venv exists, so the same
# commit cannot pass on one machine and fail on another because of a stray global ruff.
if [ ! -x .venv/bin/python ]; then
  if command -v ruff >/dev/null 2>&1 && [ -f pyproject.toml ]; then
    ruff check packages apps services
    ruff format --check packages apps services
    ran=1
  fi
  if command -v mypy >/dev/null 2>&1 && [ -f pyproject.toml ]; then
    mypy --strict packages services apps
    ran=1
  fi
  if command -v pytest >/dev/null 2>&1 && [ -d tests ]; then
    pytest -q
    ran=1
  fi
fi
if [ -x .venv/bin/ruff ]; then
  .venv/bin/ruff check packages apps services
  .venv/bin/ruff format --check packages apps services
  ran=1
fi
if [ -x .venv/bin/mypy ]; then
  .venv/bin/mypy --strict packages apps/api services
  ran=1
fi
if [ -x .venv/bin/pytest ]; then
  .venv/bin/pytest -q
  ran=1
fi
if [ -n "${EZCHAIN_TEST_DATABASE_URL:-}" ]; then
  bash tools/check-migrations.sh
  ran=1
fi
# The browser suite is the slow part of the gate. Run it when the diff can
# affect the web app, or when explicitly requested; a backend-only or docs
# diff still runs every Python, lint and migration check.
web_gate=1
if [ "${EZCHAIN_GATE_WEB:-auto}" = "always" ]; then
  web_gate=1
elif git rev-parse --verify --quiet origin/main >/dev/null 2>&1; then
  changed=$(git diff --name-only origin/main...HEAD 2>/dev/null || true)
  if printf '%s\n' "$changed" | grep -qE '^(apps/web/|packages/ui/|pnpm-lock\.yaml|pnpm-workspace\.yaml|tools/build-tokens\.mjs|tools/check-wireframe\.mjs|design/)'; then
    web_gate=1
  else
    web_gate=0
  fi
fi
if [ -f pnpm-workspace.yaml ]; then
  if [ "$web_gate" -eq 0 ]; then
    echo "web gates skipped: the diff touches no web paths (EZCHAIN_GATE_WEB=always forces them)"
  elif [ ! -d node_modules ]; then
    echo "not present yet: web gates (node_modules missing — run bash tools/setup-web.sh)"
  else
    command -v pnpm >/dev/null 2>&1 || {
      echo "pnpm is required for the web gate: npm install -g pnpm@9"
      exit 1
    }
    [ -x node_modules/.bin/eslint ] || {
      echo "web dependencies are incomplete (eslint missing): run bash tools/setup-web.sh"
      exit 1
    }
    pnpm -r lint
    pnpm -r typecheck
    pnpm --filter @ezchain/web build
    pnpm -r test
    ran=1
  fi
fi
if [ "$ran" -eq 0 ]; then
  echo "not present yet: ruff, mypy, pytest, eslint, tsc, playwright"
fi

printf '\n\033[32mci: ok\033[0m\n'
