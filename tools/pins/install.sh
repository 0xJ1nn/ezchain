#!/usr/bin/env bash
# Reproduce the Python install, online or offline, and fail on any drift.
#
#   bash tools/pins/install.sh --fast              static verdict, no network, no venv
#   bash tools/pins/install.sh --full              fresh venv, install from the pins
#   bash tools/pins/install.sh --full --runtime    the runtime set the API image gets
#   bash tools/pins/install.sh --full --offline    install from $EZCHAIN_WHEELHOUSE
#   bash tools/pins/install.sh --wheelhouse DIR    fill DIR from the runtime lock
#   bash tools/pins/install.sh --audit             advisory check (network, or --report)
#
# The fast path is what a commit gate runs on every change. The full path is what
# proves the pins install on a machine that has never built the project; --offline
# proves it without a network. `--wheelhouse` writes the directory that --offline
# then installs from, and its digest is recorded by the caller.
#
# Exit codes: 0 pass, 1 a pin or drift failure, 2 the check could not run.
set -euo pipefail
cd "$(dirname "$0")/../.."

PYTHON="${PYTHON:-}"
if [ -z "$PYTHON" ]; then
  if [ -x .venv/bin/python ]; then PYTHON=.venv/bin/python; else PYTHON=python3.11; fi
fi
mode=fast
runtime=0
offline=0
wheelhouse=""
audit=0
report=""

usage() { sed -n '2,18p' "$0"; }

for arg in "$@"; do
  case "$arg" in
    --fast) mode=fast ;;
    --full) mode=full ;;
    --runtime) runtime=1 ;;
    --offline) offline=1 ;;
    --audit) audit=1 ;;
    --wheelhouse) wheelhouse=next ;;
    --report) report=next ;;
    --help|-h) usage; exit 0 ;;
    *)
      if [ "$wheelhouse" = "next" ]; then wheelhouse="$arg"
      elif [ "$report" = "next" ]; then report="$arg"
      else echo "install: unknown argument: $arg" >&2; exit 2
      fi
      ;;
  esac
done
[ "$wheelhouse" = "next" ] && { echo "install: --wheelhouse needs a directory" >&2; exit 2; }
[ "$report" = "next" ] && { echo "install: --report needs a file" >&2; exit 2; }

if [ ! -x "$PYTHON" ] && ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "install: $PYTHON is not executable; run tools/setup-python.sh or set PYTHON" >&2
  exit 2
fi

if [ "$audit" -eq 1 ]; then
  if [ -n "$report" ]; then
    exec "$PYTHON" tools/audit_pins.py --report "$report"
  fi
  exec "$PYTHON" tools/audit_pins.py --python
fi

if [ -n "$wheelhouse" ]; then
  mkdir -p "$wheelhouse"
  lock=requirements.lock
  [ "$runtime" -eq 1 ] && lock=requirements-runtime.lock
  "$PYTHON" -m pip download --require-hashes --disable-pip-version-check \
    --quiet -r "$lock" -d "$wheelhouse"
  echo "install: wheelhouse $wheelhouse filled from $lock"
  exit 0
fi

args=(--full)
[ "$runtime" -eq 1 ] && args+=(--runtime)
[ "$offline" -eq 1 ] && args+=(--offline)
[ "$mode" = "fast" ] && args=(--fast)

exec "$PYTHON" tools/check_pins.py "${args[@]}"
