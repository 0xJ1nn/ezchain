#!/usr/bin/env bash
# Install the JavaScript workspace from the lockfile.
#
# pnpm omits devDependencies when NODE_ENV=production is inherited from the
# caller, which leaves the gate without eslint, tsc or Playwright. The
# environment is normalised here so the install does not depend on the shell
# that started it.
set -euo pipefail
cd "$(dirname "$0")/.."

unset NODE_ENV
pnpm install --frozen-lockfile

for tool in node_modules/.bin/eslint node_modules/.bin/tsc node_modules/.bin/playwright; do
  [ -x "$tool" ] || { echo "web: missing $tool — rerun this script with NODE_ENV unset" >&2; exit 1; }
done
echo "web: ok — eslint, tsc and Playwright installed"
