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

# The one deliberate exception: a gate that runs a clone (rather than this checkout) points
# imports at that clone's own package sources through EZCHAIN_GATE_PYTHONPATH, because the
# venv's editable installs resolve to this checkout and would otherwise test the wrong tree.
# It is a declared channel, not an inherited environment: a stray PYTHONPATH is still unset
# above, so a developer's shell cannot change what the gate imports.
if [ -n "${EZCHAIN_GATE_PYTHONPATH:-}" ]; then
  export PYTHONPATH="$EZCHAIN_GATE_PYTHONPATH"
fi

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "1/6 design tokens"
node tools/build-tokens.mjs --check

step "2/6 wireframe integrity"
node tools/check-wireframe.mjs

step "3/6 markdown links"
node tools/check-links.mjs

step "4/6 secret scan"
node tools/check-secrets.mjs

step "5/6 dependency pins and advisory audit"
# Static and hermetic: the locks must be exact, hashed, and still satisfy the ranges
# they were compiled from; every package pyproject must declare only pinned dependencies
# (or a VCS gap named with a reason in tools/pins/unpinned.txt); and every Dockerfile
# base image must be a sha256 digest with the tag it came from recorded above it.
# Nothing here needs a network, so a bumped constraint fails here before anything
# installs. The full install proof is `bash tools/pins/install.sh --full` (add --offline
# on a machine with a wheelhouse) and needs a network; it is deliberately not in this
# gate. The checker needs `packaging`, which is a pinned test requirement: the venv is
# the gate, exactly as it is for ruff, mypy and pytest below.
if [ -x .venv/bin/python ]; then
  .venv/bin/python tools/check_pins.py --fast
elif command -v python3.11 >/dev/null 2>&1 && \
  python3.11 -c "import packaging" >/dev/null 2>&1; then
  python3.11 tools/check_pins.py --fast
else
  echo "not present yet: dependency pins (no venv and no python3.11 with packaging)"
  echo "              run: PYTHON=python3.11 bash tools/setup-python.sh"
fi
# The Node lock's integrity hashes are covered by the check above; its advisories come
# from a network service and are checked by the audit below.

# Advisory audit of the pinned sets. Unlike the static pin check this needs a network:
# pip-audit consults OSV/PyPI and pnpm audit consults the npm registry. A saved
# `pip-audit --format json` report makes the verdict hermetic — point EZCHAIN_AUDIT_REPORT
# at one and the same verdict code runs offline. Each live audit runs when its own tool is
# present. Exit 1 (a blocking advisory) fails the gate; exit 2 ("could not run") is printed
# as an explicit skip and never called a pass, matching the other tool-absent skips in this
# gate. The dated result is recorded in docs/DEPENDENCIES.md.
audit_py=""
if [ -x .venv/bin/python ]; then
  audit_py=.venv/bin/python
elif command -v python3 >/dev/null 2>&1; then
  audit_py=python3
fi
audit_status=0
if [ -n "${EZCHAIN_AUDIT_REPORT:-}" ]; then
  # A supplied report must be readable and clean; there is no skip in this path.
  [ -n "$audit_py" ] || { echo "dependency audit: no python to judge the report with" >&2; exit 1; }
  "$audit_py" tools/audit_pins.py --report "$EZCHAIN_AUDIT_REPORT" || audit_status=$?
  if [ "$audit_status" -ne 0 ]; then
    echo "dependency audit: the supplied report failed (exit $audit_status)" >&2
    exit 1
  fi
else
  audit_ran=0
  if [ -n "$audit_py" ] && "$audit_py" -c "import pip_audit" >/dev/null 2>&1; then
    "$audit_py" tools/audit_pins.py --python || audit_status=$?
    audit_ran=1
  fi
  if [ -n "$audit_py" ] && [ "$audit_status" -eq 0 ] && command -v pnpm >/dev/null 2>&1; then
    "$audit_py" tools/audit_pins.py --node || audit_status=$?
    audit_ran=1
  fi
  if [ "$audit_status" -eq 1 ]; then
    echo "dependency audit: a blocking advisory was reported" >&2
    exit 1
  fi
  if [ "$audit_ran" -eq 0 ]; then
    echo "dependency audit: skipped — no audit tools available (not a pass)"
  elif [ "$audit_status" -eq 2 ]; then
    echo "dependency audit: the advisory service was unreachable (exit 2) — not a pass; see docs/DEPENDENCIES.md" >&2
  fi
fi

step "6/6 runtime gates (when present)"
ran=0
# The bridge clients fail closed when the compiled extension is absent, so the
# native module must be installed before any Python test runs. Host-native build
# only; a toolchain-less machine cannot run the full gate (ADR-0052).
if [ -f rust/Cargo.toml ] && command -v cargo >/dev/null 2>&1; then
  bash tools/build-native.sh
  ran=1
fi
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
# The Compose drill is explicitly opt-in and is never part of the default gate: `run`
# builds images and needs a Docker daemon, so a host without one would turn the gate red
# for a reason that has nothing to do with the diff. `static` validates the compose file
# and the migration chain without a daemon; `run` is the full drill. Anything else is a
# mistake in the caller's environment, not a skip.
case "${EZCHAIN_GATE_DRILL:-off}" in
  off)
    echo "drill: skipped (set EZCHAIN_GATE_DRILL=static for the daemon-free checks, or =run for the full stack)"
    ;;
  static|run)
    if [ ! -x .venv/bin/python ]; then
      echo "not present yet: drill (no .venv — run bash tools/setup-python.sh)"
    elif [ "$EZCHAIN_GATE_DRILL" = "static" ]; then
      EZCHAIN_PYTHON=.venv/bin/python bash tools/ops/drill.sh --static-only
      ran=1
    else
      EZCHAIN_PYTHON=.venv/bin/python bash tools/ops/drill.sh
      ran=1
    fi
    ;;
  *)
    echo "EZCHAIN_GATE_DRILL must be off, static or run (got ${EZCHAIN_GATE_DRILL})" >&2
    exit 2
    ;;
esac
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
# The Rust half of the platform (ADR-0050) is gated wherever a toolchain exists.
# Its absence is reported as a skip, not a red gate, so a contributor without Rust
# can still run the local gate; when cargo is present all three checks are mandatory.
if [ -f rust/Cargo.toml ]; then
  if command -v cargo >/dev/null 2>&1; then
    step "rust gate"
    (
      cd rust
      cargo fmt --all -- --check
      cargo clippy --workspace --all-targets -- -D warnings
      cargo test --workspace
    )
    ran=1
  else
    echo "rust gates skipped: cargo not present (install rustup to run them)"
  fi
fi
if [ "$ran" -eq 0 ]; then
  echo "not present yet: ruff, mypy, pytest, eslint, tsc, playwright, cargo"
fi

printf '\n\033[32mci: ok\033[0m\n'
