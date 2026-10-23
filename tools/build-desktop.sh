#!/usr/bin/env bash
# Build the Linux desktop app: traced Next server, frozen FastAPI sidecar, Electron shell.
#
#   bash tools/build-desktop.sh          # unpacked app (fast, for local launch)
#   bash tools/build-desktop.sh --package # AppImage + .deb
#
# The desktop app uses an embedded SQLite database and binds both sidecars to 127.0.0.1
# on ephemeral ports. It is not a public web deployment.
set -euo pipefail
cd "$(dirname "$0")/.."

package=0
for arg in "$@"; do
  case "$arg" in
    --package) package=1 ;;
    --help|-h) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "build-desktop: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# PyInstaller is a build tool in the project venv. A linked worktree may not have its own
# .venv, so fall back to the main checkout's venv via the git common dir.
MAIN_ROOT="$(cd "$(git rev-parse --git-common-dir)/.." && pwd)"
PYINSTALLER="${EZCHAIN_PYINSTALLER:-}"
if [ -z "$PYINSTALLER" ]; then
  if [ -x .venv/bin/pyinstaller ]; then
    PYINSTALLER="$PWD/.venv/bin/pyinstaller"
  else
    PYINSTALLER="$MAIN_ROOT/.venv/bin/pyinstaller"
  fi
fi
if [ ! -x "$PYINSTALLER" ]; then
  echo "build-desktop: PyInstaller not found; install it in the project venv" >&2
  exit 2
fi

step "1/4 web standalone build"
pnpm --filter @ezchain/web build

step "2/4 stage web bundle"
rm -rf apps/desktop/build/web
mkdir -p apps/desktop/build/web
cp -a apps/web/.next/standalone/. apps/desktop/build/web/
mkdir -p apps/desktop/build/web/apps/web/.next
cp -a apps/web/.next/static apps/desktop/build/web/apps/web/.next/static
if [ -d apps/web/public ]; then
  cp -a apps/web/public apps/desktop/build/web/apps/web/public
fi
# A tarball survives electron-builder's extraResources filtering and preserves pnpm's
# symlinked standalone dependency tree. The app extracts it once per version into its data
# directory at startup.
tar -czf apps/desktop/build/web.tar.gz -C apps/desktop/build/web .

step "3/4 freeze the API sidecar"
rm -rf apps/desktop/build/backend apps/desktop/build/pyi
"$PYINSTALLER" \
  --noconfirm --clean --onedir --name ezchain-api \
  --distpath apps/desktop/build/backend \
  --workpath apps/desktop/build/pyi \
  --specpath apps/desktop/build/pyi \
  --paths packages/core-py/src \
  --paths packages/adapters/src \
  --paths apps/api/src \
  --paths services/engine/src \
  --collect-submodules uvicorn \
  --collect-submodules sqlalchemy \
  --collect-submodules pydantic \
  --collect-submodules fastapi \
  --exclude-module psycopg \
  --exclude-module psycopg_binary \
  --exclude-module tkinter \
  apps/desktop/backend_entry.py

step "4/4 electron package"
pnpm --filter @ezchain/desktop exec electron-builder --linux dir
if [ "$package" -eq 1 ]; then
  pnpm --filter @ezchain/desktop exec electron-builder --linux AppImage deb
fi

printf '\n\033[32mdesktop: built\033[0m\n'
echo "launch: pnpm --filter @ezchain/desktop exec electron apps/desktop"
