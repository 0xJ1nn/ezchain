#!/usr/bin/env bash
#
# ezCHAIN one-command installer for a server the operator owns.
#
# It does five things, and nothing else:
#   1. installs Docker Engine + Compose when this host has neither;
#   2. fetches a tagged release archive and verifies its SHA-256;
#   3. generates the deployment secrets on this host (database password, API bearer
#      token, agent-key pepper) and never prints or transmits them;
#   4. starts Postgres, the migration job, the API, the web app and Caddy;
#   5. prints the URL and the next steps.
#
# It never creates, reads or writes trading key material. No wallet, mnemonic,
# signing key or vault file is touched; those stay with the operator or the vault.
# The script has no telemetry and phones home to nobody. Its only outbound calls are
# the Docker package repository (when Docker is missing) and the release URL.
#
# Re-running it is an in-place upgrade: the environment file and its secrets are kept,
# the compose project name is fixed, and Postgres data lives in a named volume.
#
# Target: Linux (Ubuntu/Debian for the automatic Docker install). POSIX tools are used
# so `--dry-run` and `--no-start` can be exercised on other hosts.
#
# Usage: tools/install/install.sh --help

set -Eeuo pipefail

readonly REPO_DEFAULT="0xJ1nn/ezchain"
readonly RELEASE_BASE_DEFAULT=""
readonly PROJECT_NAME="ezchain"

VERSION="${EZCHAIN_INSTALL_VERSION:-}"
INSTALL_DIR="${EZCHAIN_INSTALL_DIR:-$HOME/ezchain}"
BIND_ADDR="${EZCHAIN_BIND:-127.0.0.1}"
PORT="${EZCHAIN_PORT:-8080}"
PUBLIC_URL="${EZCHAIN_PUBLIC_URL:-}"
HOST_NAME="${EZCHAIN_HOST:-}"
REPO="${EZCHAIN_REPO:-$REPO_DEFAULT}"
RELEASE_BASE_URL="${EZCHAIN_RELEASE_BASE_URL:-$RELEASE_BASE_DEFAULT}"
ARCHIVE="${EZCHAIN_ARCHIVE:-}"
ARCHIVE_SHA256="${EZCHAIN_ARCHIVE_SHA256:-}"
DOCKER_INSTALL=1
START=1
DRY_RUN=0
WAIT_SECONDS="${EZCHAIN_INSTALL_WAIT_SECONDS:-300}"

TMP_DIR=""
SUDO=()
DOCKER=()

log() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Install ezCHAIN on one server. Paper mode only.

Usage: tools/install/install.sh [options]

  --version <tag>       Release tag to install (default: latest published release)
  --dir <path>          Install root (default: $HOME/ezchain)
  --bind <address>      Host address for the web edge (default: 127.0.0.1)
  --port <port>         Host port for the web edge (default: 8080)
  --public-url <url>    Browser origin; default derives from bind and port
  --archive <path>      Install from a local release archive instead of downloading
  --sha256 <hex>        Expected SHA-256 for --archive (required with --archive)
  --base-url <url>      Release download base (default: GitHub releases)
  --skip-docker-install Fail instead of installing Docker when it is missing
  --no-start            Fetch, verify and configure only; do not start containers
  --dry-run             Print the plan and change nothing
  --help                Show this help

Environment: EZCHAIN_INSTALL_VERSION, EZCHAIN_INSTALL_DIR, EZCHAIN_BIND,
EZCHAIN_PORT, EZCHAIN_PUBLIC_URL, EZCHAIN_HOST, EZCHAIN_REPO,
EZCHAIN_RELEASE_BASE_URL, EZCHAIN_ARCHIVE, EZCHAIN_ARCHIVE_SHA256,
EZCHAIN_INSTALL_WAIT_SECONDS.

Release layout (GitHub release assets):
  <base>/<tag>/ezchain-<tag>.tar.gz
  <base>/<tag>/ezchain-<tag>.tar.gz.sha256

Secrets are generated on this host and stay here. No trading keys are created.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
    --version=*) VERSION="${1#*=}"; shift ;;
    --dir) INSTALL_DIR="${2:?--dir needs a value}"; shift 2 ;;
    --dir=*) INSTALL_DIR="${1#*=}"; shift ;;
    --bind) BIND_ADDR="${2:?--bind needs a value}"; shift 2 ;;
    --bind=*) BIND_ADDR="${1#*=}"; shift ;;
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --public-url) PUBLIC_URL="${2:?--public-url needs a value}"; shift 2 ;;
    --public-url=*) PUBLIC_URL="${1#*=}"; shift ;;
    --archive) ARCHIVE="${2:?--archive needs a value}"; shift 2 ;;
    --archive=*) ARCHIVE="${1#*=}"; shift ;;
    --sha256) ARCHIVE_SHA256="${2:?--sha256 needs a value}"; shift 2 ;;
    --sha256=*) ARCHIVE_SHA256="${1#*=}"; shift ;;
    --base-url) RELEASE_BASE_URL="${2:?--base-url needs a value}"; shift 2 ;;
    --base-url=*) RELEASE_BASE_URL="${1#*=}"; shift ;;
    --skip-docker-install) DOCKER_INSTALL=0; shift ;;
    --no-start) START=0; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) die "unknown argument: $1 (try --help)" ;;
  esac
done

cleanup() {
  if [ -n "$TMP_DIR" ] && [ -d "$TMP_DIR" ]; then
    rm -rf "$TMP_DIR"
  fi
}
trap cleanup EXIT

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

sha256_file() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | awk '{print $1}'
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  elif command -v openssl >/dev/null 2>&1; then
    openssl dgst -sha256 "$1" | awk '{print $NF}'
  else
    die "no SHA-256 tool found (sha256sum, shasum or openssl)"
  fi
}

lower() {
  printf '%s' "$1" | tr 'ABCDEF' 'abcdef'
}

validate_tag() {
  case "$1" in
    ""|*[!A-Za-z0-9._-]*) die "invalid release tag: $1" ;;
    ..*|.*) die "invalid release tag: $1" ;;
  esac
}

validate_port() {
  case "$1" in
    ""|*[!0-9]*) die "invalid port: $1" ;;
  esac
  [ "$1" -ge 1 ] && [ "$1" -le 65535 ] || die "port out of range: $1"
}

validate_public_url() {
  case "$1" in
    http://*|https://*) ;;
    *) die "public URL must start with http:// or https://: $1" ;;
  esac
  case "$1" in
    *\"*|*\'*|*\\*|*[[:space:]]*) die "public URL contains an unsafe character" ;;
  esac
}

detect_host() {
  local candidate
  if [ -n "$HOST_NAME" ]; then
    printf '%s' "$HOST_NAME"
    return
  fi
  case "$BIND_ADDR" in
    127.0.0.1|::1|\[::1\]) printf '127.0.0.1'; return ;;
    0.0.0.0|::|\[::\]) ;;
    *) printf '%s' "$BIND_ADDR"; return ;;
  esac
  if command -v hostname >/dev/null 2>&1; then
    # Linux: first address from `hostname -I`. Avoids an external IP service.
    for candidate in $(hostname -I 2>/dev/null || true); do
      case "$candidate" in
        *:*) continue ;;
        "") continue ;;
        *) printf '%s' "$candidate"; return ;;
      esac
    done
    candidate="$(hostname -f 2>/dev/null || true)"
    if [ -n "$candidate" ]; then
      printf '%s' "$candidate"
      return
    fi
  fi
  printf 'localhost'
}

compose_at() {
  local dir="$1"
  shift
  ( cd "$dir" && "${DOCKER[@]}" compose -p "$PROJECT_NAME" "$@" )
}

docker_ready() {
  "${DOCKER[@]}" info >/dev/null 2>&1
}

resolve_docker() {
  if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    DOCKER=(docker)
    return 0
  fi
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    DOCKER=(sudo docker)
    return 0
  fi
  return 1
}

start_docker_daemon() {
  command -v systemctl >/dev/null 2>&1 || return 0
  if [ "$(id -u)" -eq 0 ]; then
    systemctl start docker >/dev/null 2>&1 || true
  elif command -v sudo >/dev/null 2>&1; then
    sudo systemctl start docker >/dev/null 2>&1 || true
  fi
}

install_docker() {
  [ "$(uname -s)" = "Linux" ] || die "automatic Docker install supports Linux only"
  [ -r /etc/os-release ] || die "cannot read /etc/os-release; install Docker manually"
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    ubuntu|debian) ;;
    *) die "automatic Docker install supports Ubuntu/Debian only (found ${ID:-unknown}); install Docker manually" ;;
  esac
  [ -n "${VERSION_CODENAME:-}" ] || die "VERSION_CODENAME is empty; install Docker manually"

  step "Installing Docker Engine and Compose"
  if [ "$(id -u)" -eq 0 ]; then
    SUDO=()
  else
    command -v sudo >/dev/null 2>&1 || die "root or sudo is required to install Docker"
    SUDO=(sudo)
  fi

  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y --no-install-recommends ca-certificates curl gnupg openssl
  "${SUDO[@]}" install -m 0755 -d /etc/apt/keyrings

  local key_tmp
  key_tmp="$(mktemp)"
  curl -fsSL --proto '=https' "https://download.docker.com/linux/${ID}/gpg" -o "$key_tmp"
  "${SUDO[@]}" install -m 0644 "$key_tmp" /etc/apt/keyrings/docker.asc
  rm -f "$key_tmp"

  local arch
  arch="$(dpkg --print-architecture)"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/%s %s stable\n' \
    "$arch" "$ID" "$VERSION_CODENAME" |
    "${SUDO[@]}" tee /etc/apt/sources.list.d/docker.list >/dev/null

  "${SUDO[@]}" apt-get update
  "${SUDO[@]}" apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

  if command -v systemctl >/dev/null 2>&1; then
    "${SUDO[@]}" systemctl enable --now docker
  else
    "${SUDO[@]}" service docker start
  fi

  DOCKER=("${SUDO[@]}" docker)
  docker_ready || die "Docker was installed but the daemon is not reachable"
}

ensure_docker() {
  if command -v docker >/dev/null 2>&1 && ! docker info >/dev/null 2>&1; then
    start_docker_daemon
  fi
  if resolve_docker; then
    "${DOCKER[@]}" compose version >/dev/null 2>&1 ||
      die "Docker Compose v2 is required (the 'docker compose' plugin)"
    return
  fi
  if [ "$DOCKER_INSTALL" -eq 0 ]; then
    die "Docker is missing and --skip-docker-install was given"
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    log "would install Docker Engine and Compose (missing on this host)"
    return
  fi
  install_docker
  "${DOCKER[@]}" compose version >/dev/null 2>&1 ||
    die "Docker Compose v2 is required (the 'docker compose' plugin)"
}

resolve_version() {
  [ -n "$VERSION" ] && return
  [ "$DRY_RUN" -eq 1 ] && { VERSION="<latest release>"; return; }
  command -v curl >/dev/null 2>&1 ||
    die "curl is required to resolve the latest release; pass --version <tag>"
  local body
  body="$(curl -fsSL --proto '=https' --max-time 20 \
    "https://api.github.com/repos/${REPO}/releases/latest")" ||
    die "cannot resolve the latest release; pass --version <tag>"
  VERSION="$(printf '%s' "$body" | sed -n 's/.*"tag_name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
  [ -n "$VERSION" ] || die "the latest release has no tag; pass --version <tag>"
}

release_dir_for() {
  printf '%s/releases/%s' "$INSTALL_DIR" "$1"
}

download_release() {
  local version="$1" dest="$2" archive_name base url sha_url expected
  archive_name="ezchain-${version}.tar.gz"

  if [ -n "$ARCHIVE" ]; then
    [ -f "$ARCHIVE" ] || die "archive not found: $ARCHIVE"
    cp "$ARCHIVE" "$dest"
  else
    base="${RELEASE_BASE_URL:-https://github.com/${REPO}/releases/download}"
    url="${base}/${version}/${archive_name}"
    intent "download $url"
    local proto
    case "$url" in
      https://*) proto='=https' ;;
      http://*) proto='=https,http' ;;
      *) die "release base must be an http(s) URL: $base" ;;
    esac
    curl -fsSL --proto "$proto" --retry 3 --connect-timeout 15 "$url" -o "$dest" ||
      die "cannot download release archive: $url"
    if [ -z "$ARCHIVE_SHA256" ]; then
      sha_url="${url}.sha256"
      intent "download $sha_url"
      curl -fsSL --proto "$proto" --retry 3 --connect-timeout 15 "$sha_url" -o "$dest.sha256" ||
        die "cannot download the release checksum: $sha_url"
    fi
  fi

  if [ -n "$ARCHIVE_SHA256" ]; then
    expected="$(lower "$ARCHIVE_SHA256")"
  elif [ -f "${dest}.sha256" ]; then
    expected="$(awk -v f="$archive_name" '$2==f {print $1; exit}' "${dest}.sha256")"
    [ -n "$expected" ] || expected="$(awk 'NR==1{print $1}' "${dest}.sha256")"
    expected="$(lower "$expected")"
  else
    die "no checksum available; publish ${archive_name}.sha256 or pass --sha256"
  fi

  case "$expected" in
    [0-9a-f]*) ;;
    *) die "the release checksum is not a SHA-256 digest" ;;
  esac
  [ "${#expected}" -eq 64 ] || die "the release checksum is not a SHA-256 digest"

  local actual
  actual="$(sha256_file "$dest")"
  actual="$(lower "$actual")"
  [ "$actual" = "$expected" ] ||
    die "SHA-256 mismatch for ${archive_name}: expected ${expected}, got ${actual}"
  log "verified SHA-256 ${expected}"
  EXPECTED_SHA="$expected"
}

intent() {
  if [ "$DRY_RUN" -eq 1 ]; then
    log "would $*"
  else
    log "==> $*"
  fi
}

extract_release() {
  local archive="$1" target="$2" staging list top strip tops count
  staging="$(dirname "$target")/.staging.$$"
  rm -rf "$staging"
  mkdir -p "$staging"
  list="$TMP_DIR/archive.list"
  tar -tzf "$archive" > "$list"

  while IFS= read -r entry; do
    case "$entry" in
      /*|../*|*/../*|*/..) die "archive contains an unsafe path: $entry" ;;
    esac
  done < "$list"

  # A release is source only. A symlink, hardlink, device or FIFO in the archive could
  # write through the extraction root; refuse rather than reason about tar's defences.
  if tar -tvzf "$archive" | awk 'substr($1, 1, 1) ~ /[bchpsl]/ {found = 1} END {exit !found}'; then
    die "release archive contains a link or special file"
  fi

  strip=0
  tops="$(awk -F/ 'NF > 0 && $1 != "." && $1 != "" {print $1}' "$list" | sort -u)"
  count="$(printf '%s\n' "$tops" | sed '/^$/d' | wc -l | tr -d ' ')"
  top="$(printf '%s\n' "$tops" | head -1)"
  if [ "$count" = "1" ] && grep -qx "${top}/infra/compose.yaml" "$list"; then
    strip=1
  fi

  if [ "$strip" -eq 1 ]; then
    tar -xzf "$archive" -C "$staging" --strip-components=1 --no-same-owner
  else
    tar -xzf "$archive" -C "$staging" --no-same-owner
  fi

  [ -f "$staging/infra/compose.yaml" ] || die "release archive has no infra/compose.yaml"
  [ -f "$staging/infra/Caddyfile" ] || die "release archive has no infra/Caddyfile"
  [ -f "$staging/apps/api/Dockerfile" ] || die "release archive has no apps/api/Dockerfile"
  [ -f "$staging/apps/web/Dockerfile" ] || die "release archive has no apps/web/Dockerfile"

  printf '%s\n' "$EXPECTED_SHA" > "$staging/.ezchain-release"
  rm -rf "$target"
  mv "$staging" "$target"
  chmod 755 "$target"
}

env_value() {
  local key="$1" file="$2"
  [ -f "$file" ] || { printf ''; return; }
  awk -v k="$key" '
    index($0, k"=") == 1 {v = substr($0, length(k) + 2); sub(/\r$/, "", v)}
    END {print v}
  ' "$file"
}

set_env_value() {
  local key="$1" value="$2" file="$3" tmp
  tmp="$(mktemp "${TMP_DIR}/env.XXXXXX")"
  awk -v k="$key" -v v="$value" '
    BEGIN { found = 0 }
    index($0, k"=") == 1 { if (!found) { print k"="v; found = 1 }; next }
    { print }
    END { if (!found) print k"="v }
  ' "$file" > "$tmp"
  cat "$tmp" > "$file"
  rm -f "$tmp"
  chmod 600 "$file"
}

ensure_secret() {
  local key="$1" file="$2" value
  value="$(env_value "$key" "$file")"
  if [ -n "$value" ]; then
    [ "${#value}" -ge 32 ] ||
      die "existing ${key} in ${file} is shorter than 32 characters; fix it or remove the file"
    return
  fi
  set_env_value "$key" "$(openssl rand -hex 32)" "$file"
}

generate_configuration() {
  local version="$1" env_file="$2" host origin cors
  host="$(detect_host)"
  case "$host" in
    ""|*[!A-Za-z0-9.:\[\]-]*) die "invalid host name: $host (set EZCHAIN_HOST)" ;;
  esac
  if [ -z "$PUBLIC_URL" ]; then
    origin="http://${host}:${PORT}"
  else
    origin="$PUBLIC_URL"
  fi
  validate_public_url "$origin"
  cors="[\"${origin}\"]"

  [ ! -L "$env_file" ] || die "refusing to write through a symlink: ${env_file}"
  if [ ! -f "$env_file" ]; then
    umask 077
    {
      printf '%s\n' '# ezCHAIN server environment, generated by tools/install/install.sh.'
      printf '%s\n' '# Mode 0600. Never commit this file. The three secrets below are'
      printf '%s\n' '# generated on this host and are never printed or transmitted.'
    } > "$env_file"
  fi
  umask 022
  chmod 600 "$env_file"

  # Fail before writing anything when an existing secret is unusable. Never replace a
  # value the operator may already depend on.
  local key present
  for key in POSTGRES_PASSWORD EZCHAIN_API_TOKEN EZCHAIN_API_KEY_PEPPER; do
    present="$(env_value "$key" "$env_file")"
    if [ -n "$present" ] && [ "${#present}" -lt 32 ]; then
      die "existing ${key} in ${env_file} is shorter than 32 characters; fix it or remove the file"
    fi
  done

  set_env_value POSTGRES_DB "ezchain" "$env_file"
  set_env_value POSTGRES_USER "ezchain" "$env_file"
  ensure_secret POSTGRES_PASSWORD "$env_file"
  ensure_secret EZCHAIN_API_TOKEN "$env_file"
  ensure_secret EZCHAIN_API_KEY_PEPPER "$env_file"

  set_env_value EZCHAIN_ENVIRONMENT "prod" "$env_file"
  set_env_value EZCHAIN_AUTO_CREATE_SCHEMA "false" "$env_file"
  set_env_value EZCHAIN_AUTH_ENABLED "true" "$env_file"
  set_env_value EZCHAIN_REQUIRE_AUTH_FOR_READS "false" "$env_file"
  set_env_value EZCHAIN_DATA_MODE "live" "$env_file"
  # Server-derived mode. Paper is the only mode this installer starts; live needs a
  # signed gate record and the executor, neither of which is installed here.
  set_env_value EZCHAIN_EXECUTION_MODE "paper" "$env_file"
  set_env_value EZCHAIN_LOG_LEVEL "INFO" "$env_file"
  set_env_value EZCHAIN_PUBLIC_URL "$origin" "$env_file"
  set_env_value EZCHAIN_DOMAIN "$host" "$env_file"
  set_env_value EZCHAIN_BIND "$BIND_ADDR" "$env_file"
  set_env_value EZCHAIN_PORT "$PORT" "$env_file"
  set_env_value EZCHAIN_VERSION "$version" "$env_file"
  set_env_value EZCHAIN_API_URL "http://api:8000" "$env_file"
  set_env_value EZCHAIN_WEB_MODE "live" "$env_file"
  set_env_value EZCHAIN_CORS_ORIGINS "$cors" "$env_file"

  PUBLIC_URL_RESOLVED="$origin"
}

backup_before_upgrade() {
  local old_dir="$1" backup_dir="$2" stamp
  [ -n "$old_dir" ] || return 0
  [ -d "$old_dir/infra" ] || return 0
  intent "back up the database before upgrading"
  mkdir -p "$backup_dir"
  chmod 700 "$backup_dir"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  if compose_at "$old_dir/infra" ps -q postgres 2>/dev/null | grep -q .; then
    if compose_at "$old_dir/infra" exec -T postgres sh -c \
      'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup_dir/ezchain-${stamp}.dump"; then
      chmod 600 "$backup_dir/ezchain-${stamp}.dump"
      log "database backup: $backup_dir/ezchain-${stamp}.dump"
    else
      rm -f "$backup_dir/ezchain-${stamp}.dump"
      warn "database backup failed; continuing with the upgrade"
    fi
  else
    log "no running database to back up"
  fi
}

probe_host() {
  case "$BIND_ADDR" in
    0.0.0.0|::|\[::\]) printf '127.0.0.1' ;;
    *) printf '%s' "$BIND_ADDR" ;;
  esac
}

http_code() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)" || code="000"
  printf '%s' "${code:-000}"
}

wait_for_ready() {
  local base="$1" waited=0
  log "waiting for the edge at ${base} (up to ${WAIT_SECONDS}s)"
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    if [ "$(http_code "${base}/readyz")" = "200" ]; then
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  compose_at "$INSTALL_DIR/current/infra" ps || true
  die "the stack did not become ready within ${WAIT_SECONDS}s"
}

verify_operational_surface() {
  local base="$1" path code
  for path in /metrics /health /docs /redoc /openapi.json; do
    code="$(http_code "${base}${path}")"
    [ "$code" = "404" ] ||
      die "operational endpoint ${path} is reachable through the edge (HTTP ${code}); refusing to continue"
  done
  for path in /healthz /readyz; do
    code="$(http_code "${base}${path}")"
    [ "$code" = "200" ] || die "public probe ${path} answered HTTP ${code}"
  done
}

start_stack() {
  local current="$INSTALL_DIR/current" version
  step "Starting the stack"
  compose_at "$current/infra" up -d --build --remove-orphans
  wait_for_ready "$BASE_URL"
  verify_operational_surface "$BASE_URL"
  version="$(env_value EZCHAIN_VERSION "$INSTALL_DIR/ezchain.env")"
  log "stack is healthy (version ${version})"
}

print_summary() {
  local docker_cmd="docker" headline="ezCHAIN is installed."
  if [ "${#DOCKER[@]}" -gt 0 ]; then
    docker_cmd="${DOCKER[*]}"
  fi
  if [ "$START" -eq 0 ]; then
    headline="ezCHAIN is configured; the containers were not started."
  fi
  cat <<EOF

${headline}

  URL          ${PUBLIC_URL_RESOLVED}
  API probes   ${BASE_URL}/healthz  ${BASE_URL}/readyz
  Mode         paper (server-derived; live is not installed)
  Install root ${INSTALL_DIR}
  Secrets      ${INSTALL_DIR}/ezchain.env (mode 0600, generated on this host)

Next steps
  1. Open ${PUBLIC_URL_RESOLVED} and create your paper workflow.
  2. Remote server with the default loopback bind? Forward the port from your machine:
       ssh -L ${PORT}:127.0.0.1:${PORT} <user>@<server>
     For direct public access, put a TLS terminator in front and re-run with
       --bind 0.0.0.0 --public-url https://<domain>
  3. Back up before every upgrade:
       ${docker_cmd} compose -p ${PROJECT_NAME} -f ${INSTALL_DIR}/current/infra/compose.yaml \\
         exec -T postgres sh -c 'pg_dump -U "\$POSTGRES_USER" -d "\$POSTGRES_DB" -Fc' \\
         > ezchain-\$(date +%F).dump
  4. Upgrade in place (secrets and data are kept):
       ${INSTALL_DIR}/current/tools/install/install.sh --version <new-tag>
  5. Logs:
       ${docker_cmd} compose -p ${PROJECT_NAME} -f ${INSTALL_DIR}/current/infra/compose.yaml logs -f

The edge is bound to ${BIND_ADDR}:${PORT}. /metrics, /health, /docs, /redoc and
/openapi.json are not published through it; scrape the API over the Docker network
with the operator token. /healthz and /readyz stay public probes.

No trading keys were created, read or stored. Keys stay with you or your vault.
EOF
}

dry_run_plan() {
  local archive_name
  archive_name="ezchain-${VERSION}.tar.gz"
  local docker_state="would install when missing"
  if command -v docker >/dev/null 2>&1; then
    docker_state="present (compose checked at install time)"
  fi
  cat <<EOF
dry run — nothing will be changed.

  release tag     ${VERSION}
  install root    ${INSTALL_DIR}
  release source  ${ARCHIVE:-${RELEASE_BASE_URL:-https://github.com/${REPO}/releases/download}/${VERSION}/${archive_name}}
  checksum        ${ARCHIVE_SHA256:-<downloaded ${archive_name}.sha256>}
  edge bind       ${BIND_ADDR}:${PORT}
  public URL      ${PUBLIC_URL:-http://$(detect_host):${PORT}}
  secrets         generated in ${INSTALL_DIR}/ezchain.env (never printed or sent)
  docker          ${docker_state}
  start stack     $([ "$START" -eq 1 ] && printf 'yes' || printf 'no (--no-start)')

docker install: $([ "$DOCKER_INSTALL" -eq 1 ] && printf 'yes, when missing' || printf 'disabled')
EOF
}

main() {
  validate_port "$PORT"
  case "$BIND_ADDR" in
    ""|*[!A-Za-z0-9.:\[\]-]*) die "invalid bind address: $BIND_ADDR" ;;
  esac
  if [ -n "$ARCHIVE" ] && [ -z "$ARCHIVE_SHA256" ]; then
    die "--archive requires --sha256 (or EZCHAIN_ARCHIVE_SHA256)"
  fi
  if [ -n "$ARCHIVE_SHA256" ]; then
    case "$(lower "$ARCHIVE_SHA256")" in
      [0-9a-f]*) ;;
      *) die "SHA-256 must be hexadecimal" ;;
    esac
    [ "${#ARCHIVE_SHA256}" -eq 64 ] || die "SHA-256 must be 64 hexadecimal characters"
  fi

  require_cmd tar
  require_cmd awk
  require_cmd sed
  require_cmd mktemp

  resolve_version
  if [ "$DRY_RUN" -eq 0 ]; then
    validate_tag "$VERSION"
  fi

  if [ "$START" -eq 1 ] && [ "$(uname -s)" != "Linux" ] && [ "$DRY_RUN" -eq 0 ]; then
    die "starting the stack requires Linux; use --no-start or --dry-run on $(uname -s)"
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    dry_run_plan
    return
  fi

  if [ "$START" -eq 1 ]; then
    ensure_docker
  fi
  if [ -z "$ARCHIVE" ]; then
    require_cmd curl
  fi

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ezchain-install.XXXXXX")"
  mkdir -p "$INSTALL_DIR/releases"
  chmod 700 "$INSTALL_DIR" 2>/dev/null || true

  local release_dir marker current old_release old_dir
  release_dir="$(release_dir_for "$VERSION")"
  marker="$release_dir/.ezchain-release"

  if [ -z "$ARCHIVE" ] && [ -f "$marker" ]; then
    EXPECTED_SHA="$(cat "$marker")"
    log "release ${VERSION} is already installed; verifying configuration and containers"
  else
    step "Fetching release ${VERSION}"
    download_release "$VERSION" "$TMP_DIR/release.tar.gz"
    step "Extracting release"
    extract_release "$TMP_DIR/release.tar.gz" "$release_dir"
  fi

  step "Configuring secrets on this host"
  local env_file="$INSTALL_DIR/ezchain.env"
  require_cmd openssl
  generate_configuration "$VERSION" "$env_file"
  ln -sfn ../../../ezchain.env "$release_dir/infra/.env"

  current="$INSTALL_DIR/current"
  old_release=""
  old_dir=""
  if [ -L "$current" ]; then
    old_release="$(basename "$(readlink "$current")")"
    old_dir="$current"
  fi

  if [ -n "$old_release" ] && [ "$old_release" != "$VERSION" ] && [ "$START" -eq 1 ]; then
    backup_before_upgrade "$old_dir" "$INSTALL_DIR/backups"
  fi

  ln -sfn "releases/${VERSION}" "$current"

  BASE_URL="http://$(probe_host):${PORT}"
  if [ "$START" -eq 1 ]; then
    start_stack
  else
    log "configuration written; containers were not started (--no-start)"
  fi

  if [ -z "${PUBLIC_URL_RESOLVED:-}" ]; then
    PUBLIC_URL_RESOLVED="$(env_value EZCHAIN_PUBLIC_URL "$env_file")"
  fi
  print_summary
}

main "$@"
