#!/usr/bin/env bash
# Build the two shipped images from a named release candidate. The build
# context is the candidate, never whatever is checked out elsewhere, and the
# script does not tag to a registry, push or publish anything. Health smoke
# checks are opt-in and run locally.
#
#   tools/release/build-release-images.sh --source <checkout> [--tag <version>] [--smoke]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SOURCE=""
TAG=""
SMOKE=0

usage() { sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; }
fail() { printf 'build-release-images: %s\n' "$1" >&2; exit 2; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --source) SOURCE="${2:?--source needs a value}"; shift 2 ;;
    --source=*) SOURCE="${1#*=}"; shift ;;
    --tag) TAG="${2:?--tag needs a value}"; shift 2 ;;
    --tag=*) TAG="${1#*=}"; shift ;;
    --smoke) SMOKE=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) fail "unknown argument: $1" ;;
  esac
done

[ -n "$SOURCE" ] || fail "pass --source <release-candidate checkout>"
[ -d "$SOURCE" ] || fail "source directory does not exist: $SOURCE"
SOURCE="$(cd "$SOURCE" && pwd)"
for file in VERSION apps/api/Dockerfile apps/web/Dockerfile; do
  [ -f "$SOURCE/$file" ] || fail "release candidate is missing $file"
done
[ -z "$TAG" ] && TAG="$(tr -d '[:space:]' < "$SOURCE/VERSION")"
case "$TAG" in
  ""|*[!A-Za-z0-9._-]*) fail "invalid tag: $TAG" ;;
esac

if ! command -v docker >/dev/null 2>&1; then
  fail "docker is not installed on this host (image build not verified)"
fi
if ! docker info >/dev/null 2>&1; then
  fail "no reachable Docker daemon (image build not verified, not a pass)"
fi

printf 'building images from release candidate %s at %s\n' "$TAG" "$SOURCE"
docker build -f "$SOURCE/apps/api/Dockerfile" -t "ezchain/api:$TAG" "$SOURCE"
docker build -f "$SOURCE/apps/web/Dockerfile" -t "ezchain/web:$TAG" "$SOURCE"

if [ "$SMOKE" -eq 1 ]; then
  docker run --rm "ezchain/api:$TAG" python -c \
    'import ezchain_bridge; assert hasattr(ezchain_bridge, "quantize_money")'
  api="$(docker run -d --rm "ezchain/api:$TAG")"
  web="$(docker run -d --rm "ezchain/web:$TAG")"
  healthy_api=0
  healthy_web=0
  for _ in $(seq 1 30); do
    if [ "$healthy_api" -eq 0 ] && docker exec "$api" python -c \
      'import urllib.request; assert urllib.request.urlopen("http://127.0.0.1:8000/healthz", timeout=2).status == 200' >/dev/null 2>&1; then
      healthy_api=1
    fi
    if [ "$healthy_web" -eq 0 ] && docker exec "$web" wget -qO- http://127.0.0.1:3000/ >/dev/null 2>&1; then
      healthy_web=1
    fi
    [ "$healthy_api" -eq 1 ] && [ "$healthy_web" -eq 1 ] && break
    sleep 1
  done
  docker stop "$api" "$web" >/dev/null 2>&1 || true
  [ "$healthy_api" -eq 1 ] || fail "API image did not answer /healthz within 30s"
  [ "$healthy_web" -eq 1 ] || fail "web image did not answer its health probe within 30s"
  printf 'smoke: both images answer their health probes with the native bridge present\n'
else
  printf 'smoke: skipped (pass --smoke to run the local health checks)\n'
fi
printf 'images built: ezchain/api:%s ezchain/web:%s\n' "$TAG" "$TAG"
