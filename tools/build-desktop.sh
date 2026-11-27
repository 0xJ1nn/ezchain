#!/usr/bin/env bash
# Stage the desktop app on this host: traced Next server, frozen FastAPI sidecar,
# Electron unpacked build. The electron-builder platform is derived from the host,
# never hard-coded to Linux; packaged targets are selected by package-desktop.sh.
#
#   bash tools/build-desktop.sh            # unpacked app for this host
#   bash tools/build-desktop.sh --print-platform  # print the host's builder platform
#
# The desktop app uses an embedded SQLite database and binds both sidecars to 127.0.0.1
# on ephemeral ports. It is not a public web deployment.
set -euo pipefail
cd "$(dirname "$0")/.."

print_platform=0
for arg in "$@"; do
  case "$arg" in
    --print-platform) print_platform=1 ;;
    --help|-h) sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "build-desktop: unknown argument: $arg" >&2; exit 2 ;;
  esac
done

# electron-builder names platforms differently from Node. Derive it once, from the
# host, so a macOS staging run never asks for a Linux target.
BUILDER_PLATFORM="$(node -p '({darwin:"mac",linux:"linux",win32:"win"})[process.platform] || ""')"
if [ "$print_platform" -eq 1 ]; then
  [ -n "$BUILDER_PLATFORM" ] || { echo "build-desktop: unsupported host $(uname -s)" >&2; exit 2; }
  echo "$BUILDER_PLATFORM"
  exit 0
fi
[ -n "$BUILDER_PLATFORM" ] || { echo "build-desktop: unsupported host $(uname -s)" >&2; exit 2; }

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
  --hidden-import ezchain_bridge \
  --collect-submodules uvicorn \
  --collect-submodules sqlalchemy \
  --collect-submodules pydantic \
  --collect-submodules fastapi \
  --exclude-module psycopg \
  --exclude-module psycopg_binary \
  --exclude-module tkinter \
  apps/desktop/backend_entry.py

# Prove the frozen sidecar, not the build venv, can load the extension. A missing
# --hidden-import leaves the import dynamic in native.py and fails here, not at a
# customer's first launch.
sidecar="apps/desktop/build/backend/ezchain-api/ezchain-api"
if [ ! -x "$sidecar" ]; then
  echo "build-desktop: frozen sidecar not found at $sidecar" >&2
  exit 1
fi
EZCHAIN_DESKTOP_SMOKE_NATIVE=1 "$sidecar"

step "4/4 electron package (host platform: $BUILDER_PLATFORM)"
pnpm --filter @ezchain/desktop exec electron-builder "--$BUILDER_PLATFORM" dir

printf '\n\033[32mdesktop: staged for %s\033[0m\n' "$BUILDER_PLATFORM"
echo "launch: pnpm --filter @ezchain/desktop exec electron apps/desktop"
