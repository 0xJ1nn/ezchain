#!/usr/bin/env bash
# Point git at the tracked hooks. One-time per clone.
set -euo pipefail
cd "$(dirname "$0")/.."
git config core.hooksPath .githooks
echo "hooks: core.hooksPath=.githooks"
