#!/usr/bin/env bash
# Build and install the in-process PyO3 bridge (ADR-0052) into the chosen venv.
#
# This is a host-native build: no Docker, no container toolchain. The extension is
# built with the repository's committed Rust lock and installed into the venv's
# site-packages as an importable ``ezchain_bridge`` module. The app and the gate fail
# closed without it (NativeBridgeUnavailable), so this script is a prerequisite for
# running either.
#
#   bash tools/build-native.sh                # install into .venv (or $VIRTUAL_ENV)
#   EZCHAIN_VENV=/path/to/venv bash tools/build-native.sh
#
# The script is idempotent: a second run rebuilds only what changed and overwrites the
# installed module with the same file. RUSTFLAGS is deliberately unset so the linker
# flags in rust/.cargo/config.toml (needed by PyO3 on macOS) are the ones used; callers
# do not need to export anything.
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

# The venv is chosen explicitly, or inherited from an activated environment, or the
# repository default. A missing/partial venv is a hard error with a clear remedy.
VENV_DIR="${EZCHAIN_VENV:-${VIRTUAL_ENV:-$ROOT/.venv}}"
PYTHON="$VENV_DIR/bin/python"
if [ ! -x "$PYTHON" ]; then
  echo "native: no Python at $PYTHON — create the venv first:" >&2
  echo "         PYTHON=python3.11 bash tools/setup-python.sh" >&2
  echo "       or point EZCHAIN_VENV at an existing venv." >&2
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "native: cargo not found — a Rust toolchain is required to build ezchain-bridge." >&2
  echo "        Install it with rustup (https://rustup.rs), then re-run this script." >&2
  echo "        No Docker is used or required." >&2
  exit 1
fi

echo "native: Python  = $PYTHON"
echo "native: venv    = $VENV_DIR"

# Build from the Rust workspace root so cargo picks up rust/.cargo/config.toml.
# --locked keeps the committed Cargo.lock authoritative; --release is the installed
# build. CARGO_TARGET_DIR is pinned so the artifact location below is deterministic.
echo "native: building ezchain-bridge (abi3-py39) with cargo ..."
(
  cd "$ROOT/rust"
  unset RUSTFLAGS
  export CARGO_TARGET_DIR="$ROOT/rust/target"
  cargo build --locked --release -p ezchain-bridge
)

SITE_PACKAGES="$("$PYTHON" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"

# The cdylib's file name is platform-specific; Python imports it as ezchain_bridge.so
# on both macOS and Linux. Other platforms are not supported or verified.
case "$(uname -s)" in
  Darwin)
    src="$ROOT/rust/target/release/libezchain_bridge.dylib"
    ;;
  Linux)
    src="$ROOT/rust/target/release/libezchain_bridge.so"
    ;;
  *)
    echo "native: unsupported platform $(uname -s); only macOS and Linux host builds are supported" >&2
    echo "        Windows is not supported or verified." >&2
    exit 1
    ;;
esac

if [ ! -f "$src" ]; then
  echo "native: expected build artifact not found at $src" >&2
  exit 1
fi

dst="$SITE_PACKAGES/ezchain_bridge.so"
# Replace the module atomically. Copying over a loaded extension rewrites the file a running
# interpreter has mapped and crashes it; a rename gives the new build a new file while every
# running process keeps the copy it loaded.
tmp="$dst.tmp.$$"
cp "$src" "$tmp"
mv -f "$tmp" "$dst"
echo "native: installed $src -> $dst"

# Prove the installed file is importable by the venv that will run the app/gate.
if ! "$PYTHON" -c 'import ezchain_bridge; assert hasattr(ezchain_bridge, "quantize_money")'; then
  echo "native: the installed ezchain_bridge module failed to import in $PYTHON" >&2
  exit 1
fi

echo "native: ok — ezchain_bridge is importable; the app and gate can run"
