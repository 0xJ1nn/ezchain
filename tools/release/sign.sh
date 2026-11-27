#!/usr/bin/env bash
#
# Sign one release artifact with the maintainer's private key.
#
# The private key is never read from this repository, printed or copied into an artifact.
# It is supplied at run time through EZCHAIN_SIGNING_KEY, which names a file the operator
# keeps outside the checkout. The signature is written beside the artifact as <file>.sig.
# Creating or rotating the key is a maintainer action; this script only uses it.
#
# Because signing publishes a trusted artifact, the script states what it will do and
# requires an explicit --yes before writing anything.
#
# Usage:
#   EZCHAIN_SIGNING_KEY=/path/to/private.pem tools/release/sign.sh --yes <file>
#   tools/release/sign.sh --help

set -Eeuo pipefail

YES=0
FILES=()

usage() {
  sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'
}

die() {
  printf 'sign: %s\n' "$*" >&2
  exit 2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --yes) YES=1; shift ;;
    --help|-h) usage; exit 0 ;;
    --) shift; while [ "$#" -gt 0 ]; do FILES+=("$1"); shift; done ;;
    -*) die "unknown argument: $1" ;;
    *) FILES+=("$1"); shift ;;
  esac
done

[ "${#FILES[@]}" -eq 1 ] || die "pass exactly one artifact to sign"
ARTIFACT="${FILES[0]}"
[ -f "$ARTIFACT" ] || die "artifact does not exist: $(basename "$ARTIFACT")"
[ "$YES" -eq 1 ] || die "refusing to sign without --yes"
KEY="${EZCHAIN_SIGNING_KEY:-}"
[ -n "$KEY" ] || die "set EZCHAIN_SIGNING_KEY to the private key path"
[ -f "$KEY" ] || die "the configured private key is not a readable file"
command -v openssl >/dev/null 2>&1 || die "openssl is required"

printf 'sign: artifact %s -> %s.sig\n' "$(basename "$ARTIFACT")" "$(basename "$ARTIFACT")"
printf 'sign: using the release private key supplied outside the repository\n'
openssl dgst -sha256 -sign "$KEY" -out "${ARTIFACT}.sig" "$ARTIFACT"
printf 'signed %s\n' "$(basename "$ARTIFACT")"
