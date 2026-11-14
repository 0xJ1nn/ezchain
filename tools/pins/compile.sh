#!/usr/bin/env bash
# Regenerate the three Python locks from their declared inputs.
#
# This is the only supported way to move a pin. It needs a network and the same
# Python minor version the deployable uses (3.11), because a lock is resolved for
# one interpreter. The output is committed; CI never regenerates, it verifies.
#
#   bash tools/pins/compile.sh
#   bash tools/pins/compile.sh --check    # fail if a committed lock is stale
#
# pip-tools is pinned here rather than in a lock of its own: a resolver is a build
# tool, not a production dependency, and pinning it would put a second copy of the
# resolution logic under audit.
set -euo pipefail
cd "$(dirname "$0")/../.."

PIP_TOOLS="7.6.2"
PYTHON="${PYTHON:-python3.11}"
check=0
case "${1:-}" in
  --check) check=1 ;;
  "") ;;
  *)
    echo "compile: unknown argument: $1" >&2
    exit 2
    ;;
esac

if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "compile: $PYTHON is required (set PYTHON=/path/to/python3.11)" >&2
  exit 1
fi

workdir="$(mktemp -d)"
trap 'rm -rf "$workdir"' EXIT
"$PYTHON" -m venv "$workdir/venv"
"$workdir/venv/bin/pip" install --quiet --disable-pip-version-check "pip-tools==$PIP_TOOLS"

# --generate-hashes records every sha256 for every wheel and sdist the resolver saw,
# which is what makes `pip install --require-hashes` meaningful. --no-emit-index-url
# keeps a personal index URL out of the committed file.
compile() {
  "$workdir/venv/bin/pip-compile" \
    --quiet \
    --generate-hashes \
    --no-emit-index-url \
    --resolver=backtracking \
    --output-file="$1" \
    "${@:2}"
}

compile requirements.lock requirements.txt
compile requirements-runtime.lock requirements-runtime.txt
compile requirements-keyring.lock --extra keyring apps/api/pyproject.toml

if [ "$check" -eq 1 ]; then
  if ! git diff --quiet -- requirements.lock requirements-runtime.lock requirements-keyring.lock; then
    echo "compile: a committed lock is stale — review the diff and commit it" >&2
    git diff --stat -- requirements.lock requirements-runtime.lock requirements-keyring.lock >&2
    exit 1
  fi
  echo "compile: ok — the committed locks match their inputs"
  exit 0
fi

"$PYTHON" tools/check_pins.py --fast
echo "compile: ok — review the diff, then commit the locks"
