#!/usr/bin/env bash
# Stage a real native binary into one npm platform package and pack the meta
# and platform pair. The binary is either built here from the shipped Rust CLI
# or accepted from the host that produced it; there is no install script, no
# download at install time and no binary committed to the repository.
#
#   tools/release/stage-npm-platform.sh --target darwin-arm64 --build-cli --out <dir>
#   tools/release/stage-npm-platform.sh --target <target> --binary <path> --out <dir>
#   [--stage-root <dir>]  # optional copy of the npm packages for local checks
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET=""
BINARY=""
BUILD=0
OUT=""
STAGE=""

fail() { printf 'stage-npm-platform: %s\n' "$1" >&2; exit 2; }
usage() { sed -n '2,7p' "$0" | sed 's/^# \{0,1\}//'; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target needs a value}"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    --binary) BINARY="${2:?--binary needs a value}"; shift 2 ;;
    --binary=*) BINARY="${1#*=}"; shift ;;
    --build-cli) BUILD=1; shift ;;
    --out) OUT="${2:?--out needs a value}"; shift 2 ;;
    --out=*) OUT="${1#*=}"; shift ;;
    --stage-root) STAGE="${2:?--stage-root needs a value}"; shift 2 ;;
    --stage-root=*) STAGE="${1#*=}"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$TARGET" ] || fail "pass --target"
case "$TARGET" in
  darwin-arm64|darwin-x64|linux-x64|linux-arm64|win32-x64) ;;
  *) fail "unknown target: $TARGET" ;;
esac
HOST="$(node -p 'process.platform + "-" + process.arch')"

if [ "$BUILD" -eq 1 ]; then
  [ "$TARGET" = "$HOST" ] || fail "cannot build $TARGET on $HOST; pass --binary produced on that host"
  export PATH="$HOME/.cargo/bin:$PATH"
  command -v cargo >/dev/null 2>&1 || fail "cargo not found; install the Rust toolchain"
  echo "building ezchain-cli with the committed Cargo.lock"
  (cd "$ROOT/rust" && unset RUSTFLAGS && cargo build --locked --release -p ezchain-cli)
  BINARY="$ROOT/rust/target/release/ezchain-cli"
fi
[ -n "$BINARY" ] || fail "pass --binary or --build-cli"
[ -f "$BINARY" ] && [ -x "$BINARY" ] || fail "binary is missing or not executable: $BINARY"

family="$(file -b "$BINARY")"
case "$TARGET" in
  darwin-*) [[ "$family" == *Mach-O* ]] || fail "$BINARY is not a macOS Mach-O binary ($family)" ;;
  linux-*) [[ "$family" == *ELF* ]] || fail "$BINARY is not a Linux ELF binary ($family)" ;;
  win32-*) [[ "$family" == *PE32* || "$family" == *"MS-DOS"* ]] || fail "$BINARY is not a Windows PE binary ($family)" ;;
esac

smoke=""
if [ "$TARGET" = "$HOST" ]; then
  smoke="$(cd "$ROOT" && "$BINARY" version 2>&1 | head -n 1)"
  if [ "$BUILD" -eq 1 ]; then
    [[ "$smoke" == "ezchain-cli "* ]] || fail "the built CLI did not report its version: $smoke"
  fi
else
  smoke="not executed (cross-target artifact)"
fi

STAGE="${STAGE:-$ROOT/distribution/npm}"
PKG="$STAGE/cli-$TARGET"
[ -d "$PKG" ] || fail "no platform package for $TARGET at $PKG"
native="ezchain-native"
case "$TARGET" in win32-*) native="ezchain-native.exe" ;; esac
cp "$BINARY" "$PKG/bin/$native"
chmod 755 "$PKG/bin/$native"
printf 'staged: %s\n' "$PKG/bin/$native"

OUT="${OUT:-$ROOT/dist/npm}"
mkdir -p "$OUT"
meta="$(npm pack --pack-destination "$OUT" "$STAGE/cli" 2>/dev/null | tail -n 1)"
platform="$(npm pack --pack-destination "$OUT" "$PKG" 2>/dev/null | tail -n 1)"
meta_sha="$(shasum -a 256 "$OUT/$meta" | awk '{print $1}')"
platform_sha="$(shasum -a 256 "$OUT/$platform" | awk '{print $1}')"

printf 'binary: %s\n' "$BINARY"
printf 'artifact: %s\nsha256: %s\n' "$meta" "$meta_sha"
printf 'artifact: %s\nsha256: %s\n' "$platform" "$platform_sha"
printf 'smoke: %s\n' "$smoke"
