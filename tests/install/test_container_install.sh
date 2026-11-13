#!/usr/bin/env bash
#
# Clean-container acceptance test for tools/install/install.sh.
#
# It starts a throwaway Docker-in-Docker container, installs ezCHAIN from a locally
# packaged release with the real installer, and checks that the API answers its health
# probes through the edge. It then re-runs the installer at the same tag, and finally
# installs a second tag to exercise the upgrade path (symlink swap, backup, secrets
# kept), proving the data survived both.
#
# Docker is required (privileged containers included). When this host has no usable
# Docker daemon the test prints SKIP and exits 0; the run is then NOT verified.
#
# Usage:
#   tests/install/test_container_install.sh
#   EZCHAIN_INSTALL_TEST_KEEP=1 tests/install/test_container_install.sh  # keep container
#
# Environment:
#   EZCHAIN_INSTALL_TEST_IMAGE  dind image (default docker:28-dind)
#   EZCHAIN_INSTALL_TEST_WAIT   seconds to wait for health (default 600)
#   EZCHAIN_INSTALL_TEST_TAG    first release tag (default v0.0.0-installtest)
#   EZCHAIN_INSTALL_TEST_NEXT_TAG  upgrade tag (default v0.0.1-installtest)

set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TAG="${EZCHAIN_INSTALL_TEST_TAG:-v0.0.0-installtest}"
# A second tag exercises the upgrade path: release symlink swap, backup attempt, and
# secrets kept across a version change. Both are packaged from this worktree, so the
# archives differ only in the tag recorded in the tree.
NEXT_TAG="${EZCHAIN_INSTALL_TEST_NEXT_TAG:-v0.0.1-installtest}"
IMAGE="${EZCHAIN_INSTALL_TEST_IMAGE:-docker:28-dind}"
WAIT_SECONDS="${EZCHAIN_INSTALL_TEST_WAIT:-600}"
NAME="ezchain-install-test-$$"
TMP=""

log() { printf '%s\n' "$*"; }
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  docker logs "$NAME" 2>&1 | tail -40 >&2 || true
  exit 1
}

cleanup() {
  if [ "${EZCHAIN_INSTALL_TEST_KEEP:-0}" = "1" ]; then
    log "keeping container ${NAME}"
  else
    docker rm -f "$NAME" >/dev/null 2>&1 || true
  fi
  if [ -n "$TMP" ]; then
    rm -rf "$TMP"
  fi
  return 0
}
trap cleanup EXIT

if ! command -v docker >/dev/null 2>&1; then
  log "SKIP: docker is unavailable; the clean-container install was NOT verified."
  exit 0
fi
if ! docker info >/dev/null 2>&1; then
  log "SKIP: the Docker daemon is not reachable; the clean-container install was NOT verified."
  exit 0
fi

TMP="$(mktemp -d)"
log "packaging releases ${TAG} and ${NEXT_TAG}"
for tag in "$TAG" "$NEXT_TAG"; do
  bash "$ROOT/tools/install/package-release.sh" --worktree --tag "$tag" --out "$TMP"
done
ARCHIVE="$TMP/ezchain-${TAG}.tar.gz"
SHA="$(awk '{print $1}' "$TMP/ezchain-${TAG}.tar.gz.sha256")"
NEXT_ARCHIVE="$TMP/ezchain-${NEXT_TAG}.tar.gz"
NEXT_SHA="$(awk '{print $1}' "$TMP/ezchain-${NEXT_TAG}.tar.gz.sha256")"
[ -f "$ARCHIVE" ] || fail "release archive was not created"
[ -f "$NEXT_ARCHIVE" ] || fail "the second release archive was not created"

log "starting ${IMAGE}"
docker run -d --privileged --name "$NAME" -e DOCKER_TLS_CERTDIR= "$IMAGE" >/dev/null

log "waiting for the inner Docker daemon"
ready=0
for _ in $(seq 1 60); do
  if docker exec "$NAME" docker info >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 2
done
[ "$ready" -eq 1 ] || fail "the inner Docker daemon did not start"

log "installing test tools in the container"
docker exec "$NAME" sh -c \
  'apk add --no-cache bash curl ca-certificates tar gzip openssl coreutils >/dev/null'
# Some dind images ship the CLI without the Compose plugin.
docker exec "$NAME" sh -c 'apk add --no-cache docker-cli-compose >/dev/null 2>&1 || true'
docker exec "$NAME" docker compose version >/dev/null 2>&1 ||
  fail "the test container has no Docker Compose v2 plugin"

docker cp "$ARCHIVE" "$NAME:/tmp/release.tar.gz"
docker cp "$NEXT_ARCHIVE" "$NAME:/tmp/release-next.tar.gz"
docker cp "$ROOT/tools/install/install.sh" "$NAME:/tmp/install.sh"

# run_installer <tag> <archive> <sha> [extra args...]
run_installer() {
  local tag="$1" archive="$2" sha="$3"
  shift 3
  docker exec "$NAME" bash /tmp/install.sh \
    --version "$tag" \
    --archive "$archive" \
    --sha256 "$sha" \
    --dir /opt/ezchain \
    --bind 127.0.0.1 \
    --port 8080 \
    --skip-docker-install \
    "$@"
}

log "running the installer in the clean container"
run_installer "$TAG" /tmp/release.tar.gz "$SHA" || fail "the installer failed"

log "checking the API through the edge"
ok=0
for _ in $(seq 1 $((WAIT_SECONDS / 3))); do
  if docker exec "$NAME" curl -fsS --max-time 5 http://127.0.0.1:8080/readyz >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 3
done
[ "$ok" -eq 1 ] || fail "the API did not become ready"

docker exec "$NAME" curl -fsS http://127.0.0.1:8080/healthz |
  grep -q '"execution":"paper"' || fail "the API does not report paper mode"
docker exec "$NAME" curl -fsS http://127.0.0.1:8080/readyz |
  grep -q '"status":"ready"' || fail "readiness did not report ready"

for path in /metrics /health /docs /redoc /openapi.json; do
  code="$(docker exec "$NAME" curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8080${path}")"
  [ "$code" = "404" ] || fail "operational endpoint ${path} answered ${code}"
done

log "checking that no key material was written"
if docker exec "$NAME" sh -c \
  'grep -Eqi "MNEMONIC|PRIVATE[ _]KEY|WALLET|SIGNING_KEY" /opt/ezchain/ezchain.env'; then
  fail "the environment file contains key material"
fi

ENV_BEFORE="$(docker exec "$NAME" sha256sum /opt/ezchain/ezchain.env)"
SECRETS_BEFORE="$(docker exec "$NAME" sh -c \
  'grep -E "^(POSTGRES_PASSWORD|EZCHAIN_API_TOKEN|EZCHAIN_API_KEY_PEPPER)=" /opt/ezchain/ezchain.env')"

log "seeding a row so the upgrade can prove data survives"
docker exec "$NAME" sh -c \
  'cd /opt/ezchain/current/infra && docker compose -p ezchain exec -T postgres \
     psql -U ezchain -d ezchain -v ON_ERROR_STOP=1 \
     -c "create table if not exists install_probe(x int); insert into install_probe values (42);"' \
  >/dev/null || fail "could not seed the database"

log "re-running the installer at the same tag (idempotence)"
run_installer "$TAG" /tmp/release.tar.gz "$SHA" || fail "the second installer run failed"

ENV_AFTER="$(docker exec "$NAME" sha256sum /opt/ezchain/ezchain.env)"
[ "$ENV_BEFORE" = "$ENV_AFTER" ] || fail "the rerun regenerated the secrets"

log "upgrading to ${NEXT_TAG} (symlink swap, backup, secrets kept)"
run_installer "$NEXT_TAG" /tmp/release-next.tar.gz "$NEXT_SHA" ||
  fail "the upgrade run failed"

CURRENT="$(docker exec "$NAME" sh -c 'readlink /opt/ezchain/current')"
[ "$CURRENT" = "releases/${NEXT_TAG}" ] ||
  fail "the upgrade did not move the release symlink (current -> ${CURRENT})"
docker exec "$NAME" test -f "/opt/ezchain/releases/${NEXT_TAG}/infra/compose.yaml" ||
  fail "the new release was not extracted"
INSTALLED_VERSION="$(docker exec "$NAME" sh -c \
  'sed -n "s/^EZCHAIN_VERSION=//p" /opt/ezchain/ezchain.env')"
[ "$INSTALLED_VERSION" = "$NEXT_TAG" ] ||
  fail "EZCHAIN_VERSION is ${INSTALLED_VERSION}, expected ${NEXT_TAG}"
docker exec "$NAME" test -d /opt/ezchain/backups ||
  fail "the upgrade did not create the backup directory"

ENV_UPGRADED="$(docker exec "$NAME" sh -c \
  'grep -E "^(POSTGRES_PASSWORD|EZCHAIN_API_TOKEN|EZCHAIN_API_KEY_PEPPER)=" /opt/ezchain/ezchain.env')"
[ "$SECRETS_BEFORE" = "$ENV_UPGRADED" ] || fail "the upgrade regenerated the secrets"

COUNT="$(docker exec "$NAME" sh -c \
  'cd /opt/ezchain/current/infra && docker compose -p ezchain exec -T postgres \
     psql -U ezchain -d ezchain -tAc "select count(*) from install_probe"')"
[ "$(printf '%s' "$COUNT" | tr -d '[:space:]')" = "1" ] || fail "database data did not survive the upgrade"

docker exec "$NAME" curl -fsS http://127.0.0.1:8080/readyz >/dev/null ||
  fail "the API is not ready after the upgrade"

log "PASS: clean-container install, health checks, loopback-only operations, same-tag rerun, and ${TAG} -> ${NEXT_TAG} upgrade"
