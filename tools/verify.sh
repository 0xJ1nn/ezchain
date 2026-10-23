#!/usr/bin/env bash
# Full local verification for a release candidate.
#
#   bash tools/verify.sh            fast gate + stress self-test (no running API needed)
#   bash tools/verify.sh --live     also runs every stress mode against a running PAPER API
#
# The stress tester owns this command. It never starts a live-money service and never
# treats a missing API as a pass when --live was requested.
set -euo pipefail
cd "$(dirname "$0")/.."

live=0
for arg in "$@"; do
  case "$arg" in
    --live) live=1 ;;
    --help|-h)
      sed -n '2,12p' "$0"
      exit 0
      ;;
    *)
      echo "verify: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

step "1/3 fast gate"
bash tools/ci.sh

step "2/3 stress self-test"
tools/stress/run.sh --self-test

if [ "$live" -eq 1 ]; then
  step "3/3 live PAPER stress + live UI E2E"
  base="${EZCHAIN_STRESS_BASE_URL:-http://127.0.0.1:8000}"
  if [ ! -f apps/web/e2e/live-paper.spec.ts ] || [ ! -f apps/web/playwright.live.config.ts ]; then
    echo "verify: live UI E2E spec/config is missing; refusing to claim a full live pass" >&2
    exit 3
  fi
  if ! node -e "const p=require('./apps/web/package.json'); process.exit(p.scripts && p.scripts['test:live'] ? 0 : 1)"; then
    echo "verify: apps/web has no test:live script; refusing to claim a full live pass" >&2
    exit 3
  fi
  echo "target: $base"
  tools/stress/run.sh --mode all --base-url "$base"
  EZCHAIN_API_URL="$base" EZCHAIN_WEB_MODE=live \
    pnpm --filter @ezchain/web test:live
  printf '\n\033[32mverify: ok (live)\033[0m\n'
else
  step "3/3 live stress skipped (pass --live to run it)"
  printf '\n\033[32mverify: ok (fast)\033[0m\n'
  echo "The full gate for a merge to main requires: bash tools/verify.sh --live"
fi
