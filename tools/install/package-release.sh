#!/usr/bin/env bash
#
# Build the release archive the installer verifies.
#
# A release is two GitHub release assets:
#   ezchain-<tag>.tar.gz
#   ezchain-<tag>.tar.gz.sha256
#
# The installer downloads both and refuses to run unless the archive's SHA-256 matches.
# Publish the pair from a clean tag, never from a working tree.
#
# Usage:
#   tools/install/package-release.sh --tag v0.2.0 [--out dist]
#   tools/install/package-release.sh --tag v0.2.0 --worktree   # local testing only
#
# --worktree archives the current checkout (uncommitted files included) and is intended
# for tests. A published release must be built from a tag.

set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG=""
OUT_DIR="$ROOT/dist"
WORKTREE=0
SOURCE=""

usage() {
  sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --tag=*) TAG="${1#*=}"; shift ;;
    --out) OUT_DIR="${2:?--out needs a value}"; shift 2 ;;
    --out=*) OUT_DIR="${1#*=}"; shift ;;
    --worktree) WORKTREE=1; shift ;;
    --source) SOURCE="${2:?--source needs a value}"; shift 2 ;;
    --source=*) SOURCE="${1#*=}"; shift ;;
    --help|-h) usage; exit 0 ;;
    *) printf 'package-release: unknown argument: %s\n' "$1" >&2; exit 2 ;;
  esac
done

[ -n "$SOURCE" ] && ROOT="$(cd "$SOURCE" && pwd)"
if [ -z "$TAG" ]; then
  TAG="$(git -C "$ROOT" describe --tags --exact-match 2>/dev/null || true)"
fi
[ -n "$TAG" ] || {
  printf 'package-release: pass --tag (or run on an exact tag)\n' >&2
  exit 2
}
case "$TAG" in
  ""|*[!A-Za-z0-9._-]*) printf 'package-release: invalid tag: %s\n' "$TAG" >&2; exit 2 ;;
  ..*|.*) printf 'package-release: invalid tag: %s\n' "$TAG" >&2; exit 2 ;;
esac

mkdir -p "$OUT_DIR"
OUT_DIR="$(cd "$OUT_DIR" && pwd)"
ARCHIVE="$OUT_DIR/ezchain-${TAG}.tar.gz"
rm -f "$ARCHIVE" "${ARCHIVE}.sha256"

if [ "$WORKTREE" -eq 1 ]; then
  # Test path: archive the checkout. Exclude everything that is not source, and every
  # local secret shape, so a test archive can never carry an operator's environment.
  tar -czf "$ARCHIVE" -C "$ROOT" \
    --exclude='./.git' \
    --exclude='./.git/*' \
    --exclude='./.env' \
    --exclude='*/.env' \
    --exclude='./ezchain.env' \
    --exclude='*.pem' \
    --exclude='*.key' \
    --exclude='*.p12' \
    --exclude='*.keystore' \
    --exclude='*.jks' \
    --exclude='./secrets' \
    --exclude='*/secrets' \
    --exclude='./vault' \
    --exclude='*/vault' \
    --exclude='./node_modules' \
    --exclude='*/node_modules' \
    --exclude='./.venv' \
    --exclude='*/.venv' \
    --exclude='*/.next' \
    --exclude='*/__pycache__' \
    --exclude='./dist' \
    --exclude='./data' \
    --exclude='./backups' \
    --exclude='./.worktrees' \
    --exclude='./.shots' \
    --exclude='./tools/stress/reports' \
    --exclude='*.log' \
    .
else
  git -C "$ROOT" archive --format=tar.gz --prefix="ezchain-${TAG}/" -o "$ARCHIVE" "$TAG"
fi

if command -v sha256sum >/dev/null 2>&1; then
  digest="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
else
  digest="$(shasum -a 256 "$ARCHIVE" | awk '{print $1}')"
fi
printf '%s  %s\n' "$digest" "$(basename "$ARCHIVE")" > "${ARCHIVE}.sha256"

printf 'archive  %s\n' "$ARCHIVE"
printf 'sha256   %s\n' "$digest"
printf 'checksum %s\n' "${ARCHIVE}.sha256"
