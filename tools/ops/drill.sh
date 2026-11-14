#!/usr/bin/env bash
# Re-drill the Compose stack at this checkout's migration head.
#
#   bash tools/ops/drill.sh                  # the whole drill (needs a Docker daemon)
#   bash tools/ops/drill.sh --static-only    # compose structure + migration chain only
#   bash tools/ops/drill.sh --keep           # leave the project up for inspection
#
# Exit codes: 0 every phase passed, 1 a phase failed, 2 the drill refused to start
# (no Docker CLI, no daemon, unusable infra/.env, a rejected compose config). A refusal is
# never a pass, and a host without a daemon therefore gets 2 from the default invocation.
#
# The container phases build images, so the default `ci` gate never calls this script:
# `EZCHAIN_GATE_DRILL=static` or `EZCHAIN_GATE_DRILL=run` opts in.
set -euo pipefail
cd "$(dirname "$0")/../.."

py="${EZCHAIN_PYTHON:-}"
if [ -z "$py" ] && [ -x .venv/bin/python ]; then
  py=.venv/bin/python
fi
py="${py:-python3}"

if ! "$py" - <<'PY' >/dev/null 2>&1
import alembic, ezchain_api, yaml  # noqa: F401
PY
then
  echo "drill: $py cannot import alembic, ezchain_api and pyyaml; run bash tools/setup-python.sh" >&2
  exit 2
fi

exec "$py" -m tools.ops drill "$@"
