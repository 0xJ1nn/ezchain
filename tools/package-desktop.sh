#!/usr/bin/env bash
# Select a desktop package target. Cross-compilation is not attempted: a target
# the host cannot build is refused with a reason before any work starts, and is
# recorded as unverified rather than silently skipped.
#
#   tools/package-desktop.sh --target linux|mac|win|all [--package] [--check-only]
set -euo pipefail
cd "$(dirname "$0")/.."

TARGET=""
CHECK_ONLY=0
PACKAGE=0

usage() { sed -n '2,6p' "$0" | sed 's/^# \{0,1\}//'; }
fail() { printf 'package-desktop: %s\n' "$1" >&2; exit 2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:?--target needs a value}"; shift 2 ;;
    --target=*) TARGET="${1#*=}"; shift ;;
    --package) PACKAGE=1; shift ;;
    --check-only) CHECK_ONLY=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$TARGET" ] || fail "pass --target linux|mac|win|all"
case "$TARGET" in
  linux|mac|win|all) ;;
  *) fail "unknown target: $TARGET" ;;
esac

HOST_OS="$(node -p process.platform)"
supports() {
  case "$1:$HOST_OS" in
    linux:linux|mac:darwin|win:win32) return 0 ;;
    *) return 1 ;;
  esac
}
if [ "$TARGET" = "all" ]; then
  targets=(linux mac win)
else
  targets=("$TARGET")
fi

refused=0
for target in "${targets[@]}"; do
  if ! supports "$target"; then
    printf 'package-desktop: target %s cannot be built on this host (%s); not built, not verified\n' "$target" "$HOST_OS" >&2
    refused=1
  fi
done
[ "$refused" -eq 0 ] || exit 2

if [ "$CHECK_ONLY" -eq 1 ]; then
  node - "$TARGET" <<'NODE'
const config = require("./apps/desktop/package.json");
const names = (target) => (Array.isArray(target) ? target : [target]).map((item) => (typeof item === "string" ? item : item.target));
console.log(`target ${process.argv[2]} configured from apps/desktop/package.json (unsigned)`);
for (const platform of ["linux", "mac", "win"]) {
  console.log(`${platform}: ${names(config.build[platform].target).join(", ")}`);
}
NODE
  exit 0
fi

# One heavy build at a time: the caller serializes this with /usr/bin/lockf.
bash tools/build-desktop.sh $([ "$PACKAGE" -eq 1 ] && printf '%s' "--package")
case "$TARGET" in
  linux)
    [ "$PACKAGE" -eq 1 ] || pnpm --filter @ezchain/desktop exec electron-builder --linux AppImage deb
    ;;
  mac) pnpm --filter @ezchain/desktop exec electron-builder --mac dmg zip ;;
  win) pnpm --filter @ezchain/desktop exec electron-builder --win nsis zip ;;
  all) pnpm --filter @ezchain/desktop exec electron-builder --linux AppImage deb --mac dmg zip --win nsis zip ;;
esac
printf 'package-desktop: %s built unsigned from this checkout\n' "$TARGET"
