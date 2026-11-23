#!/usr/bin/env bash
#
# Verify one or more release artifacts against the published public key.
#
# Each artifact must have a signature file beside it named <file>.sig, produced by
# tools/release/sign.sh. Verification prints only the artifact name and the verdict; it
# never prints key material or a signature. A missing key, a missing signature and a bad
# signature each fail closed with a non-zero exit code.
#
# Usage:
#   tools/release/verify.sh --public-key <path> <file> [<file> ...]
#   tools/release/verify.sh --public-key <path> --signature <path> <file>

set -Eeuo pipefail

PUBLIC_KEY=""
SIGNATURE=""
FILES=()

usage() {
  sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  printf 'verify: %s\n' "$*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --public-key) PUBLIC_KEY="${2:?--public-key needs a value}"; shift 2 ;;
    --public-key=*) PUBLIC_KEY="${1#*=}"; shift ;;
    --signature) SIGNATURE="${2:?--signature needs a value}"; shift 2 ;;
    --signature=*) SIGNATURE="${1#*=}"; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; while [ "$#" -gt 0 ]; do FILES+=("$1"); shift; done ;;
    -*) die "unknown argument: $1" ;;
    *) FILES+=("$1"); shift ;;
  esac
done

[ -n "$PUBLIC_KEY" ] || die "pass --public-key"
[ -f "$PUBLIC_KEY" ] || die "the release public key is not a readable file"
[ "${#FILES[@]}" -gt 0 ] || die "pass at least one artifact to verify"
command -v openssl >/dev/null 2>&1 || die "openssl is required"

status=0
for artifact in "${FILES[@]}"; do
  [ -f "$artifact" ] || { printf 'verify: %s is missing\n' "$(basename "$artifact")" >&2; status=1; continue; }
  sig="$SIGNATURE"
  [ -n "$sig" ] || sig="${artifact}.sig"
  if [ ! -f "$sig" ]; then
    printf 'verify: %s has no signature\n' "$(basename "$artifact")" >&2
    status=1
    continue
  fi
  if openssl dgst -sha256 -verify "$PUBLIC_KEY" -signature "$sig" "$artifact" >/dev/null 2>&1; then
    printf 'verified %s\n' "$(basename "$artifact")"
  else
    printf 'verify: %s did not verify\n' "$(basename "$artifact")" >&2
    status=1
  fi
done
exit "$status"
