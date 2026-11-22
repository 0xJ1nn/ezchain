#!/usr/bin/env bash
# Build the desktop app: traced Next server, frozen FastAPI sidecar, Electron shell.
#
#   bash tools/build-desktop.sh                 # unpacked app for this host
#   bash tools/build-desktop.sh --package       # installers for this host
#   bash tools/build-desktop.sh --check         # prove an existing frozen sidecar bundles the bridge
#   bash tools/build-desktop.sh --target win    # refuse unless this host can build it
#
# ONE TARGET IS BUILT WHERE IT RUNS, and that is not a limitation of this script.
# PyInstaller freezes the API sidecar into a host-native executable and cannot
# cross-compile, so a Windows package needs a Windows runner, a macOS package a macOS
# runner, and each needs its own CPU architecture. `--target` therefore exists to say so
# clearly and to name what is missing, not to pretend a Linux host can produce a `.exe`.
# `docs/DESKTOP.md` records which packages exist and which are unproven.
#
# The desktop app uses an embedded SQLite database and binds both sidecars to 127.0.0.1
# on ephemeral ports. It is not a public web deployment.
set -euo pipefail
cd "$(dirname "$0")/.."

package=0
check_only=0
target="${EZCHAIN_DESKTOP_TARGET:-}"
while [ "$#" -gt 0 ]; do
  case "$1" in
    --package) package=1 ;;
    --check) check_only=1 ;;
    --target)
      shift
      target="${1-}"
      [ -n "$target" ] || { echo "build-desktop: --target needs a value: linux, mac or win" >&2; exit 2; }
      ;;
    --target=*)
      target="${1#--target=}"
      [ -n "$target" ] || { echo "build-desktop: --target needs a value: linux, mac or win" >&2; exit 2; }
      ;;
    --help|-h) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "build-desktop: unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

# Map a build target onto the host that can produce it. The sidecar is a native
# executable, so there is no cross-build; the check is honest rather than aspirational.
host_platform() {
  case "$(uname -s)" in
    Linux*) echo linux ;;
    Darwin*) echo mac ;;
    MINGW*|MSYS*|CYGWIN*) echo win ;;
    *) echo unknown ;;
  esac
}
if [ -n "$target" ]; then
  case "$target" in linux|mac|win) ;; *) echo "build-desktop: unknown target '$target' (linux, mac, win)" >&2; exit 2 ;; esac
  host="$(host_platform)"
  if [ "$host" != "$target" ]; then
    cat >&2 <<EOF
build-desktop: cannot build the $target package on a $host host.

  The API sidecar is frozen by PyInstaller into a native executable. PyInstaller does not
  cross-compile, so a $target package has to be built on $target (with that platform's
  Python, the Rust MSVC/Xcode toolchain, and, for an installed build, a signing identity).

  What this host CAN build: the $host package.
  What a $target host must run:  bash tools/build-desktop.sh --package
  The steps are in docs/DESKTOP.md.
EOF
    exit 2
  fi
fi
target="${target:-$(host_platform)}"

step() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# The sidecar and the packaged executable carry a platform suffix. Getting this wrong is a
# packaged-app failure, not a build failure, so it is decided once here.
case "$target" in
  win) sidecar="ezchain-api.exe"; electron_unpacked="win-unpacked"; electron_exe="ezchain.exe" ;;
  mac) sidecar="ezchain-api"; electron_unpacked="mac"; electron_exe="ezchain" ;;
  *)   sidecar="ezchain-api"; electron_unpacked="linux-unpacked"; electron_exe="ezchain" ;;
esac

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

# The venv that will freeze the sidecar. It must be the same one the bridge is installed
# into, or the frozen binary would import nothing.
VENV_DIR="$(dirname "$(dirname "$PYINSTALLER")")"
PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "build-desktop: no Python at $PYTHON" >&2
  echo "  set EZCHAIN_PYINSTALLER to the pyinstaller of the venv to freeze from." >&2
  exit 2
fi

FROZEN_DIR="apps/desktop/build/backend/ezchain-api"

# The frozen sidecar imports ezchain_bridge dynamically (apps/api/src/ezchain_api/native.py),
# so PyInstaller's static analysis cannot see it and every money call fails closed at
# runtime. This is the post-freeze proof: the extension is inside the bundle, it loads and
# quantize_money returns the ledger-quantized value. A missing bridge fails the build
# instead of shipping a bundle that starts and then refuses to trade.
check_bridge_bundle() {
  local frozen="$1"
  if [ ! -d "$frozen" ]; then
    echo "build-desktop: FAIL no frozen sidecar at $frozen (run the build first)" >&2
    return 1
  fi
  local found
  found="$(find "$frozen" -type f \( -name 'ezchain_bridge*.so' -o -name 'ezchain_bridge*.pyd' \) -print -quit)"
  if [ -z "$found" ]; then
    echo "build-desktop: FAIL ezchain_bridge is not inside $frozen" >&2
    echo "  the frozen API would start and then refuse every money call (fail closed)." >&2
    echo "  keep --hidden-import ezchain_bridge on the PyInstaller line, or add" >&2
    echo "  --add-binary \"\$SITE_PACKAGES/ezchain_bridge.so:.\" to it." >&2
    return 1
  fi
  echo "build-desktop: bridge present at $found"
  "$PYTHON" - "$found" <<'PY'
"""Load the extension from inside the frozen bundle and call the money kernel."""
import importlib.util
import sys
from pathlib import Path

path = Path(sys.argv[1])
spec = importlib.util.spec_from_file_location("ezchain_bridge", path)
if spec is None or spec.loader is None:
    raise SystemExit(f"build-desktop: FAIL cannot load {path}")
module = importlib.util.module_from_spec(spec)
sys.modules["ezchain_bridge"] = module
spec.loader.exec_module(module)
if not hasattr(module, "quantize_money"):
    raise SystemExit(f"build-desktop: FAIL {path.name} has no quantize_money")
value = module.quantize_money("1.5", "amount")
if value != "1.50000000":
    raise SystemExit(f"build-desktop: FAIL quantize_money('1.5') gave {value!r}")
print(f"build-desktop: bridge ok — {path.name} loads, quantize_money('1.5') -> {value}")
PY
}

if [ "$check_only" -eq 1 ]; then
  step "check frozen bridge"
  check_bridge_bundle "$FROZEN_DIR"
  printf '\n\033[32mdesktop: frozen bridge ok\033[0m\n'
  exit 0
fi

if [ ! -x "$PYINSTALLER" ]; then
  echo "build-desktop: PyInstaller not found; install it in the project venv" >&2
  exit 2
fi

step "1/7 build and install the native bridge"
EZCHAIN_VENV="$VENV_DIR" bash tools/build-native.sh

step "2/7 web standalone build"
pnpm --filter @ezchain/web build

step "3/7 stage web bundle"
rm -rf apps/desktop/build/web
mkdir -p apps/desktop/build/web
cp -a apps/web/.next/standalone/. apps/desktop/build/web/
mkdir -p apps/desktop/build/web/apps/web/.next
cp -a apps/web/.next/static apps/desktop/build/web/apps/web/.next/static
if [ -d apps/web/public ]; then
  cp -a apps/web/public apps/desktop/build/web/apps/web/public
fi
# A tarball survives electron-builder's extraResources filtering. pnpm's standalone tree
# contains symlinks (27 of them in the current build), and creating a symlink on Windows
# needs either Developer Mode or SeCreateSymbolicLinkPrivilege — an unprivileged install
# fails to extract the runtime. `-h` writes the link targets as real files instead, which
# costs almost nothing (25.39 MB -> 25.43 MB measured) and makes extraction a plain copy on
# every platform. `-h` is spelled the same by GNU tar and by the bsdtar Windows ships, which
# matters because the tarball is also created on a Windows host for a Windows build.
tar -czhf apps/desktop/build/web.tar.gz -C apps/desktop/build/web .
if tar -tzvf apps/desktop/build/web.tar.gz | grep -q '^l'; then
  echo "build-desktop: FAIL web.tar.gz still contains symlinks" >&2
  echo "  an unprivileged Windows extractor cannot create them; check that tar honoured -h" >&2
  exit 1
fi

step "4/7 freeze the API sidecar"
rm -rf apps/desktop/build/backend apps/desktop/build/pyi
# --hidden-import ezchain_bridge is what makes the dynamically imported PyO3 extension part
# of the bundle; the next step proves it actually landed.
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

step "5/7 prove the frozen sidecar carries the bridge"
check_bridge_bundle "$FROZEN_DIR"

step "6/7 electron package"
# The fuses live in apps/desktop/package.json (build.electronFuses); electron-builder flips
# them with its own pinned @electron/fuses right before signing.
case "$target" in
  linux)
    pnpm --filter @ezchain/desktop exec electron-builder --linux dir
    if [ "$package" -eq 1 ]; then
      pnpm --filter @ezchain/desktop exec electron-builder --linux AppImage deb
    fi
    ;;
  mac)
    pnpm --filter @ezchain/desktop exec electron-builder --mac dir
    if [ "$package" -eq 1 ]; then
      pnpm --filter @ezchain/desktop exec electron-builder --mac dmg zip
    fi
    ;;
  win)
    pnpm --filter @ezchain/desktop exec electron-builder --win dir
    if [ "$package" -eq 1 ]; then
      pnpm --filter @ezchain/desktop exec electron-builder --win nsis portable
    fi
    ;;
esac

# Read the packaged binary back. A package whose fuses did not apply must never be
# reported as built.
step "7/7 verify electron fuses"
node - "apps/desktop/dist/$electron_unpacked" "$electron_exe" <<'JS'
const fs = require("node:fs");
const path = require("node:path");
const { createRequire } = require("node:module");

// electron-builder names the executable after build.executableName (apps/desktop/
// package.json) and the unpacked directory after the target. Both arrive as arguments so
// this check reads the build it actually produced, on every platform, rather than a Linux
// path on a Windows build.
const unpacked = path.resolve(process.argv[2]);
if (!fs.existsSync(unpacked)) {
	console.error(`build-desktop: FAIL no unpacked build at ${unpacked}`);
	process.exit(1);
}
const exe = path.join(unpacked, process.argv[3]);
if (!fs.existsSync(exe)) {
	console.error(
		`build-desktop: FAIL ${exe} does not exist; the directory has: ` +
			fs.readdirSync(unpacked).slice(0, 12).join(", "),
	);
	process.exit(1);
}

let fuses;
try {
	const fromDesktop = createRequire(path.join(process.cwd(), "apps/desktop/package.json"));
	const builderPkg = fromDesktop.resolve("electron-builder/package.json");
	const libPkg = createRequire(builderPkg).resolve("app-builder-lib/package.json");
	fuses = createRequire(libPkg)("@electron/fuses");
} catch (error) {
	console.error(
		`build-desktop: FAIL cannot load @electron/fuses (${error.message}); ` +
			"re-run bash tools/setup-web.sh so pnpm installs electron-builder's dependency tree",
	);
	process.exit(1);
}

const { FuseV1Options, getCurrentFuseWire } = fuses;

// @electron/fuses 1.8 exports the option indexes and the reader, but not FuseState: the
// enum lives in an internal module. These are the fuse-wire bytes Electron documents
// (constants.js: DISABLE = 48, ENABLE = 49), and the reader returns those bytes raw.
const DISABLE = 48;
const ENABLE = 49;
const expected = [
	["RunAsNode", FuseV1Options.RunAsNode, DISABLE],
	["EnableNodeOptionsEnvironmentVariable", FuseV1Options.EnableNodeOptionsEnvironmentVariable, DISABLE],
	["EnableNodeCliInspectArguments", FuseV1Options.EnableNodeCliInspectArguments, DISABLE],
	["OnlyLoadAppFromAsar", FuseV1Options.OnlyLoadAppFromAsar, ENABLE],
];

getCurrentFuseWire(exe)
	.then((wire) => {
		const wrong = expected.filter(([, option, want]) => wire[option] !== want);
		if (wrong.length > 0) {
			for (const [name, option, want] of wrong) {
				console.error(`build-desktop: FAIL fuse ${name} is ${wire[option]}, expected ${want}`);
			}
			process.exit(1);
		}
		console.log(`build-desktop: fuses ok — ${expected.map(([name]) => name).join(", ")}`);
	})
	.catch((error) => {
		console.error(`build-desktop: FAIL cannot read fuses from ${exe} (${error.message})`);
		process.exit(1);
	});
JS

printf '\n\033[32mdesktop: built\033[0m\n'
echo "launch: pnpm --filter @ezchain/desktop exec electron apps/desktop"
