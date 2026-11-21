#!/usr/bin/env bash
#
# ezCHAIN native installer for a Linux host the operator owns.
#
# This is the primary host install: no Docker, no container runtime, no root. It puts the
# paper workstation on the machine as two systemd *user* services and keeps every file it
# owns under $EZCHAIN_HOME (default $HOME/.local/share/ezchain):
#
#   1. creates or reuses a Python 3.11 virtual environment in $EZCHAIN_HOME/venv;
#   2. installs the pinned runtime dependencies (requirements-runtime.lock, hash-checked)
#      and the local workspace packages from this checkout;
#   3. builds the PyO3 bridge with tools/build-native.sh and installs ezchain_bridge.so
#      into that venv (the app fails closed without it);
#   4. builds the Next.js standalone server and stages it in $EZCHAIN_HOME/web;
#   5. writes $EZCHAIN_HOME/ezchain.env (mode 0600) with a generated API token that is
#      never printed;
#   6. applies the Alembic migrations to the configured PostgreSQL database;
#   7. installs and enables ezchain-api.service and ezchain-web.service.
#
# It never creates, reads or writes trading key material. No wallet, mnemonic, signing key
# or vault file is touched; those stay with the operator or the vault. There are no keys in
# PAPER mode at all.
#
# No telemetry and no phone-home. Outbound traffic happens only while building and
# installing: the Python package index, crates.io for the Rust dependencies, and the npm
# registry for the web dependencies. --dry-run and --configure-only make no network call.
#
# Re-running is an in-place upgrade: the venv is reused, existing secrets in the env file
# are kept, and the database is only ever migrated forward. Nothing in this script drops,
# truncates or recreates a database.
#
# Database: PostgreSQL is required. SQLite is not supported for a server install because
# migration 0003_paper_fill_order runs a PostgreSQL `DO` block and stops the chain at
# 0002_genesis_index; see docs/SELF-HOSTING-NATIVE.md.
#
# Target: Linux with a systemd user manager. macOS and Windows are not supported.
#
# Usage: tools/install-native.sh --help

set -Eeuo pipefail

readonly DEFAULT_HOME="$HOME/.local/share/ezchain"
readonly API_PORT=8000
readonly WEB_PORT=3000
readonly UNIT_NAMES=(ezchain-api.service ezchain-web.service)

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EZCHAIN_HOME="${EZCHAIN_HOME:-$DEFAULT_HOME}"
DATABASE_URL="${EZCHAIN_DATABASE_URL:-}"
PUBLIC_URL="${EZCHAIN_PUBLIC_URL:-}"
WAIT_SECONDS="${EZCHAIN_WAIT_SECONDS:-60}"
DRY_RUN=0
CONFIGURE_ONLY=0
UNINSTALL=0
START=1

VENV=""
ENV_FILE=""
DATA_DIR=""
WEB_ROOT=""
UNIT_DIR=""
TMP_DIR=""

log() { printf '%s\n' "$*"; }
step() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Install ezCHAIN natively on one Linux host. Paper mode only. No Docker, no root.

Usage: tools/install-native.sh [options]

  --home <path>         Install root (default: $HOME/.local/share/ezchain)
  --database-url <url>  PostgreSQL URL, e.g.
                        postgresql+psycopg://ezchain:secret@127.0.0.1:5432/ezchain
  --public-url <url>    Browser origin for links and the proxy's same-origin check
                        (default: http://127.0.0.1:3000)
  --configure-only      Write the environment file and stop. No build, no migration,
                        no service change, no network.
  --no-start            Install and enable the units but do not start them, and skip the
                        readiness probe.
  --uninstall           Stop and remove the two systemd user units. Data, the venv and
                        the environment file are kept.
  --dry-run             Print the plan and change nothing; no network.
  --help                Show this help.

Environment: EZCHAIN_HOME, EZCHAIN_DATABASE_URL, EZCHAIN_PUBLIC_URL,
EZCHAIN_WAIT_SECONDS, EZCHAIN_PYTHON (default python3.11), EZCHAIN_SYSTEMD_DIR
(default $XDG_CONFIG_HOME/systemd/user).

What it installs, all under the install root:
  venv/            pinned Python runtime plus the local workspace packages
  web/             Next.js standalone server (read-only at run time)
  apps/api/        alembic.ini and the migration scripts
  ezchain.env      deployment configuration, mode 0600, generated on this host
  data/            the only directory the services may write

The services bind loopback only: the API on 127.0.0.1:8000 and the web app on
127.0.0.1:3000. Put your own TLS terminator in front for remote access.

SQLite is not supported for a server install; use PostgreSQL.
No trading keys are created, read or stored. No telemetry.
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --home) EZCHAIN_HOME="${2:?--home needs a value}"; shift 2 ;;
    --home=*) EZCHAIN_HOME="${1#*=}"; shift ;;
    --database-url) DATABASE_URL="${2:?--database-url needs a value}"; shift 2 ;;
    --database-url=*) DATABASE_URL="${1#*=}"; shift ;;
    --public-url) PUBLIC_URL="${2:?--public-url needs a value}"; shift 2 ;;
    --public-url=*) PUBLIC_URL="${1#*=}"; shift ;;
    --configure-only) CONFIGURE_ONLY=1; shift ;;
    --no-start) START=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
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

intent() {
  log "==> $*"
}

resolve_paths() {
  VENV="$EZCHAIN_HOME/venv"
  ENV_FILE="$EZCHAIN_HOME/ezchain.env"
  DATA_DIR="$EZCHAIN_HOME/data"
  WEB_ROOT="$EZCHAIN_HOME/web"
  # EZCHAIN_SYSTEMD_DIR exists for a user manager whose unit path is not
  # $XDG_CONFIG_HOME/systemd/user (for example a test host). The default is the standard
  # location, and install_units proves the running manager can see the files either way.
  UNIT_DIR="${EZCHAIN_SYSTEMD_DIR:-${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user}"
}

validate_home() {
  case "$EZCHAIN_HOME" in
    ""|"/"|*[!A-Za-z0-9._/-]*) die "invalid install root: $EZCHAIN_HOME" ;;
  esac
  case "$EZCHAIN_HOME" in
    /*) ;;
    *) die "install root must be an absolute path: $EZCHAIN_HOME" ;;
  esac
}

# The units use PrivateTmp=yes, so /tmp, /var/tmp and /dev/shm inside the service are a
# private mount that does not contain the host's files. An install root under one of them
# is invisible to the service and ReadWritePaths makes systemd refuse at start (exit 226).
# This is only true once a unit runs, so it is checked at install time; --dry-run and
# --configure-only stage a tree anywhere and warn instead.
root_is_hidden_by_private_tmp() {
  case "$EZCHAIN_HOME" in
    /tmp|/tmp/*|/var/tmp|/var/tmp/*|/dev/shm|/dev/shm/*) return 0 ;;
    *) return 1 ;;
  esac
}

require_service_visible_root() {
  if root_is_hidden_by_private_tmp; then
    die "install root must not be under /tmp, /var/tmp or /dev/shm: the systemd units use PrivateTmp=yes, so the service cannot see that path and ReadWritePaths would stop it starting (exit 226). Put it under \$HOME (default \$HOME/.local/share/ezchain)"
  fi
}

validate_public_url() {
  [ -n "$PUBLIC_URL" ] || return 0
  case "$PUBLIC_URL" in
    http://*|https://*) ;;
    *) die "public URL must start with http:// or https://: $PUBLIC_URL" ;;
  esac
  case "$PUBLIC_URL" in
    *\"*|*\'*|*\\*|*[[:space:]]*) die "public URL contains an unsafe character" ;;
  esac
}

validate_database_url() {
  [ -n "$DATABASE_URL" ] || return 0
  case "$DATABASE_URL" in
    sqlite:*|sqlite+*)
      die "SQLite is not supported for a server install: migration 0003_paper_fill_order runs a PostgreSQL DO block and stops the chain at 0002_genesis_index. Use PostgreSQL, e.g. --database-url postgresql+psycopg://ezchain:secret@127.0.0.1:5432/ezchain"
      ;;
    postgresql://*|postgresql+psycopg://*) ;;
    *) die "database URL must be a PostgreSQL URL (postgresql+psycopg://...): see --help" ;;
  esac
  # The value is written to an EnvironmentFile and parsed by systemd, which does not
  # unquote a value that contains spaces or comment characters. Refuse rather than write
  # a file that parses differently from what the operator typed.
  case "$DATABASE_URL" in
    *[[:space:]]*|*\"*|*\'*|*\\*|*\#*)
      die "database URL contains a character systemd cannot represent in an EnvironmentFile (space, quote, backslash or #); percent-encode the password"
      ;;
  esac
  case "$DATABASE_URL" in
    *@*) ;;
    *) die "database URL has no credentials; a server install needs a user and password in the URL" ;;
  esac
}

redact_url() {
  printf '%s' "$1" | sed -E 's#(://[^:/@]+):[^@]*@#\1:***@#'
}

python_version_ok() {
  "$1" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 11) else 1)' >/dev/null 2>&1
}

resolve_python() {
  local candidate="${EZCHAIN_PYTHON:-python3.11}"
  if ! command -v "$candidate" >/dev/null 2>&1; then
    die "Python 3.11 not found ($candidate); install it or set EZCHAIN_PYTHON"
  fi
  python_version_ok "$candidate" || die "$candidate is not Python 3.11; the workspace requires 3.11"
  PYTHON="$candidate"
}

ensure_venv() {
  if [ -x "$VENV/bin/python" ]; then
    python_version_ok "$VENV/bin/python" ||
      die "$VENV/bin/python is not Python 3.11; remove $VENV and re-run"
    log "reusing the virtual environment at $VENV"
    return
  fi
  intent "create the virtual environment at $VENV"
  "$PYTHON" -m venv "$VENV"
}

install_python_packages() {
  local pip="$VENV/bin/python"
  intent "install the pinned runtime dependencies (requirements-runtime.lock, --require-hashes)"
  "$pip" -m pip install --upgrade pip --quiet --disable-pip-version-check
  "$pip" -m pip install --disable-pip-version-check \
    --require-hashes -r "$ROOT/requirements-runtime.lock"

  # The workspace packages are installed non-editable: the venv keeps working after the
  # checkout it was installed from is replaced. --no-deps keeps requirements-runtime.lock
  # the single source of their transitive pins.
  intent "install the local workspace packages"
  "$pip" -m pip install --disable-pip-version-check --no-build-isolation --no-deps \
    "$ROOT/packages/core-py" \
    "$ROOT/packages/adapters" \
    "$ROOT/packages/sdk-py" \
    "$ROOT/apps/api" \
    "$ROOT/services/engine"
}

stage_alembic() {
  local target="$EZCHAIN_HOME/apps/api"
  intent "stage alembic.ini and the migration scripts in $target"
  install -d "$target"
  # Replace only the bundle this installer owns; the data and env files are elsewhere.
  rm -rf "$target/alembic"
  cp -a "$ROOT/apps/api/alembic.ini" "$target/alembic.ini"
  cp -a "$ROOT/apps/api/alembic" "$target/alembic"
  find "$target/alembic" -name __pycache__ -type d -prune -exec rm -rf {} +
}

build_bridge() {
  intent "build the PyO3 bridge (cargo --locked --release) into $VENV"
  EZCHAIN_VENV="$VENV" bash "$ROOT/tools/build-native.sh"
}

build_web() {
  local standalone="$ROOT/apps/web/.next/standalone"
  # The workspace install is a prerequisite for the build and is idempotent: with a lock
  # match it is a no-op. NODE_ENV is unset for the install for the reason tools/setup-web.sh
  # records: a caller's NODE_ENV=production would omit the devDependencies the build needs.
  intent "install the JavaScript workspace from the lockfile (pnpm install --frozen-lockfile)"
  ( cd "$ROOT" && unset NODE_ENV && pnpm install --frozen-lockfile )
  intent "build the Next.js standalone server (pnpm --filter @ezchain/web build)"
  ( cd "$ROOT" && NEXT_TELEMETRY_DISABLED=1 pnpm --filter @ezchain/web build )
  [ -f "$standalone/apps/web/server.js" ] ||
    die "the web build produced no standalone server at $standalone/apps/web/server.js"

  intent "stage the standalone server in $WEB_ROOT"
  rm -rf "$WEB_ROOT"
  install -d "$WEB_ROOT"
  cp -a "$standalone/." "$WEB_ROOT/"
  install -d "$WEB_ROOT/apps/web/.next"
  cp -a "$ROOT/apps/web/.next/static" "$WEB_ROOT/apps/web/.next/static"
  if [ -d "$ROOT/apps/web/public" ]; then
    cp -a "$ROOT/apps/web/public" "$WEB_ROOT/apps/web/public"
  fi
}

env_value() {
  local key="$1" file="$2"
  [ -f "$file" ] || { printf ''; return; }
  awk -v k="$key" '
    { eq = index($0, "=") }
    eq > 1 && substr($0, 1, eq - 1) == k {v = substr($0, eq + 1); sub(/\r$/, "", v)}
    END {print v}
  ' "$file"
}

set_env_value() {
  local key="$1" value="$2" file="$3" tmp
  tmp="$(mktemp "${TMP_DIR}/env.XXXXXX")"
  awk -v k="$key" -v v="$value" '
    BEGIN { found = 0 }
    {
      eq = index($0, "=")
      if (eq > 1 && substr($0, 1, eq - 1) == k) {
        if (!found) { print k"="v; found = 1 }
        next
      }
      print
    }
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

write_environment() {
  local origin="$PUBLIC_URL"
  if [ -z "$origin" ]; then
    origin="http://127.0.0.1:${WEB_PORT}"
  fi
  validate_public_url "$origin"

  [ ! -L "$ENV_FILE" ] || die "refusing to write through a symlink: ${ENV_FILE}"
  install -d "$EZCHAIN_HOME"
  install -d "$DATA_DIR"
  chmod 700 "$EZCHAIN_HOME"
  chmod 700 "$DATA_DIR"
  if [ ! -f "$ENV_FILE" ]; then
    ( umask 077; : > "$ENV_FILE" )
    {
      printf '%s\n' '# ezCHAIN native install environment, generated by tools/install-native.sh.'
      printf '%s\n' '# Mode 0600. Never commit this file. The two secrets below are generated'
      printf '%s\n' '# on this host and are never printed or transmitted.'
    } >> "$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"

  # Fail before writing anything when an existing secret is unusable. Never replace a
  # value the operator may already depend on.
  local key present
  for key in EZCHAIN_API_TOKEN EZCHAIN_API_KEY_PEPPER; do
    present="$(env_value "$key" "$ENV_FILE")"
    if [ -n "$present" ] && [ "${#present}" -lt 32 ]; then
      die "existing ${key} in ${ENV_FILE} is shorter than 32 characters; fix it or remove the file"
    fi
  done

  ensure_secret EZCHAIN_API_TOKEN "$ENV_FILE"
  ensure_secret EZCHAIN_API_KEY_PEPPER "$ENV_FILE"

  set_env_value EZCHAIN_DATABASE_URL "$DATABASE_URL" "$ENV_FILE"
  set_env_value EZCHAIN_ENVIRONMENT "prod" "$ENV_FILE"
  set_env_value EZCHAIN_AUTO_CREATE_SCHEMA "false" "$ENV_FILE"
  set_env_value EZCHAIN_AUTH_ENABLED "true" "$ENV_FILE"
  set_env_value EZCHAIN_REQUIRE_AUTH_FOR_READS "false" "$ENV_FILE"
  set_env_value EZCHAIN_DATA_MODE "live" "$ENV_FILE"
  # Server-derived mode. Paper is the only mode this installer starts; live needs a
  # signed gate record and the executor, neither of which is installed here.
  set_env_value EZCHAIN_EXECUTION_MODE "paper" "$ENV_FILE"
  set_env_value EZCHAIN_LOG_LEVEL "INFO" "$ENV_FILE"
  set_env_value EZCHAIN_PUBLIC_URL "$origin" "$ENV_FILE"
  set_env_value EZCHAIN_BIND "127.0.0.1" "$ENV_FILE"
  set_env_value EZCHAIN_API_URL "http://127.0.0.1:${API_PORT}" "$ENV_FILE"
  set_env_value EZCHAIN_WEB_MODE "live" "$ENV_FILE"
  set_env_value EZCHAIN_CORS_ORIGINS "[\"${origin}\"]" "$ENV_FILE"
  set_env_value EZCHAIN_ALEMBIC_INI "$EZCHAIN_HOME/apps/api/alembic.ini" "$ENV_FILE"
  PUBLIC_URL_RESOLVED="$origin"
}

run_migrations() {
  intent "apply Alembic migrations to head"
  # Only the URL is exported: the migration process needs nothing else from the env file,
  # and sourcing that file would let a password character be misread as shell syntax.
  EZCHAIN_DATABASE_URL="$DATABASE_URL" \
    "$VENV/bin/alembic" -c "$EZCHAIN_HOME/apps/api/alembic.ini" upgrade head
}

stop_units_if_running() {
  # An upgrade rewrites site-packages in place. Replacing a mapped extension module under
  # a running process kills it (a rewritten .so gives the interpreter SIGSEGV/SIGBUS), so
  # the services are stopped before the build and started again after the units refresh.
  local unit running=0
  for unit in "${UNIT_NAMES[@]}"; do
    if systemctl --user is-active --quiet "$unit" 2>/dev/null; then
      running=1
    fi
  done
  [ "$running" -eq 1 ] || return 0
  intent "stop the running services before replacing the installed bridge"
  systemctl --user stop "${UNIT_NAMES[@]}"
}

install_units() {
  local src="$ROOT/tools/install/native" unit node_bin
  intent "install the systemd user units in $UNIT_DIR"
  install -d "$UNIT_DIR"
  node_bin="$(command -v node)"
  for unit in "${UNIT_NAMES[@]}"; do
    [ -f "$src/$unit" ] || die "missing unit file: $src/$unit"
    # The units name %h/.local/share/ezchain so systemd can verify them as committed.
    # A non-default --home is substituted here into absolute paths. The web unit calls
    # `/usr/bin/env node`; the resolved Node path is substituted too, because the systemd
    # user manager's default PATH may not contain a per-user Node install.
    sed -e "s|%h/.local/share/ezchain|$EZCHAIN_HOME|g" \
      -e "s|/usr/bin/env node|$node_bin|g" "$src/$unit" > "$UNIT_DIR/$unit"
    chmod 644 "$UNIT_DIR/$unit"
  done
  systemctl --user daemon-reload
  # Prove the running user manager actually found the files. XDG_CONFIG_HOME can differ
  # from the manager's own search path (a manager started before the variable changed does
  # not move), and "enable" would otherwise fail with a bare "unit does not exist".
  for unit in "${UNIT_NAMES[@]}"; do
    systemctl --user cat "$unit" >/dev/null 2>&1 ||
      die "systemd did not find ${unit} in ${UNIT_DIR}; the running user manager's unit path differs. Set EZCHAIN_SYSTEMD_DIR to the directory it reads (see systemd-path user-configuration) or install to the default \$HOME/.config"
  done
  if [ "$START" -eq 1 ]; then
    systemctl --user enable --now "${UNIT_NAMES[@]}"
  else
    systemctl --user enable "${UNIT_NAMES[@]}"
    log "units enabled but not started (--no-start)"
  fi
}

http_code() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$1" 2>/dev/null)" || code="000"
  printf '%s' "${code:-000}"
}

wait_for_ready() {
  local url="$1" waited=0 code
  log "waiting for ${url} (up to ${WAIT_SECONDS}s)"
  while [ "$waited" -lt "$WAIT_SECONDS" ]; do
    code="$(http_code "$url")"
    [ "$code" = "200" ] && return 0
    sleep 2
    waited=$((waited + 2))
  done
  systemctl --user status "${UNIT_NAMES[@]}" --no-pager || true
  die "the service did not become ready within ${WAIT_SECONDS}s; read the logs with: journalctl --user -u ezchain-api -u ezchain-web"
}

uninstall_units() {
  step "Removing the systemd user units"
  local unit
  systemctl --user disable --now "${UNIT_NAMES[@]}" >/dev/null 2>&1 || true
  for unit in "${UNIT_NAMES[@]}"; do
    rm -f "$UNIT_DIR/$unit"
  done
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  log "units removed; the services will not start again"
  cat <<EOF

Kept, deliberately:
  environment  ${ENV_FILE} (mode 0600)
  runtime      ${VENV}
  web bundle   ${WEB_ROOT}
  data         ${DATA_DIR}

The database is untouched. Removing the files above is an operator decision, not part
of --uninstall. A re-install picks the existing secrets and data back up.
EOF
}

print_summary() {
  cat <<EOF

ezCHAIN is installed natively. Paper mode only.

  URL          ${PUBLIC_URL_RESOLVED}
  API          http://127.0.0.1:${API_PORT} (loopback only)
  Web          http://127.0.0.1:${WEB_PORT} (loopback only)
  Install root ${EZCHAIN_HOME}
  Secrets      ${ENV_FILE} (mode 0600, generated on this host, never printed)
  Services     ezchain-api.service, ezchain-web.service (systemd --user)

Next steps
  1. Open ${PUBLIC_URL_RESOLVED} and create your paper workflow.
  2. Remote host? Forward the web port from your machine:
       ssh -L ${WEB_PORT}:127.0.0.1:${WEB_PORT} <user>@<server>
  3. Back up before every upgrade: pg_dump the database, and copy ${ENV_FILE}.
  4. Upgrade in place (secrets and data are kept):
       tools/install-native.sh --home ${EZCHAIN_HOME} --database-url ...
  5. Logs:
       journalctl --user -u ezchain-api -u ezchain-web -f

No trading keys were created, read or stored. Keys stay with you or your vault.
EOF
}

dry_run_plan() {
  local python_state database
  python_state="${EZCHAIN_PYTHON:-python3.11} (checked at install time)"
  if command -v "${EZCHAIN_PYTHON:-python3.11}" >/dev/null 2>&1; then
    python_state="${EZCHAIN_PYTHON:-python3.11} present"
  else
    python_state="missing: install Python 3.11 before a real run"
  fi
  if [ -n "$DATABASE_URL" ]; then
    database="$(redact_url "$DATABASE_URL")"
  else
    database="missing: --database-url or EZCHAIN_DATABASE_URL is required"
  fi
  local service_state="enable and start"
  if [ "$START" -eq 0 ]; then
    service_state="enable only (--no-start), readiness probe skipped"
  fi
  cat <<EOF
dry run — nothing will be changed and no network call will be made.

  install root    ${EZCHAIN_HOME}
  python          ${python_state}
  virtualenv      ${VENV} (created when missing, reused when present)
  dependencies    requirements-runtime.lock (--require-hashes) + the workspace packages
  bridge          cargo build --locked --release -p ezchain-bridge -> ${VENV}
  web             pnpm install --frozen-lockfile, then pnpm --filter @ezchain/web build
                  -> staged in ${WEB_ROOT}
  database        ${database}
  migrations      alembic -c ${EZCHAIN_HOME}/apps/api/alembic.ini upgrade head
  environment     ${ENV_FILE} (mode 0600; EZCHAIN_API_TOKEN generated, never printed)
  units           ${UNIT_DIR}/ezchain-api.service ${UNIT_DIR}/ezchain-web.service
  services        ${service_state}
  bind            API 127.0.0.1:${API_PORT}, web 127.0.0.1:${WEB_PORT}
  public URL      ${PUBLIC_URL:-http://127.0.0.1:${WEB_PORT}}
  mode            paper (server-derived; live is not installed)
EOF
}

main() {
  validate_home
  if [ -n "$PUBLIC_URL" ]; then
    validate_public_url
  fi
  validate_database_url
  case "$WAIT_SECONDS" in
    ""|*[!0-9]*) die "EZCHAIN_WAIT_SECONDS must be a whole number of seconds" ;;
  esac
  resolve_paths

  if [ "$UNINSTALL" -eq 1 ] && [ "$DRY_RUN" -eq 1 ]; then
    die "--uninstall and --dry-run cannot be combined"
  fi

  if [ "$UNINSTALL" -eq 1 ]; then
    require_cmd systemctl
    uninstall_units
    return
  fi

  if [ "$DRY_RUN" -eq 1 ]; then
    if root_is_hidden_by_private_tmp; then
      warn "install root ${EZCHAIN_HOME} is under /tmp, /var/tmp or /dev/shm; a real run refuses it because the units use PrivateTmp=yes"
    fi
    dry_run_plan
    return
  fi

  require_cmd awk
  require_cmd sed
  require_cmd mktemp
  require_cmd openssl

  if [ "$CONFIGURE_ONLY" -eq 1 ]; then
    [ -n "$DATABASE_URL" ] || die "--configure-only needs --database-url or EZCHAIN_DATABASE_URL"
    if root_is_hidden_by_private_tmp; then
      warn "install root ${EZCHAIN_HOME} is under /tmp, /var/tmp or /dev/shm; a real run refuses it because the units use PrivateTmp=yes"
    fi
    TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ezchain-native.XXXXXX")"
    step "Writing the environment file"
    write_environment
    log "wrote ${ENV_FILE} (mode 0600); nothing was built, migrated or started"
    log "public URL ${PUBLIC_URL_RESOLVED}, database $(redact_url "$DATABASE_URL")"
    return
  fi

  [ -n "$DATABASE_URL" ] ||
    die "a PostgreSQL database is required: pass --database-url or set EZCHAIN_DATABASE_URL (see --help)"

  # Checked before any tool requirement or filesystem change, so a refused root costs nothing.
  require_service_visible_root

  require_cmd systemctl
  require_cmd curl
  require_cmd cargo
  require_cmd pnpm
  require_cmd node
  [ "$(uname -s)" = "Linux" ] || die "the native install supports Linux only (found $(uname -s))"
  systemctl --user show-environment >/dev/null 2>&1 ||
    die "no systemd user manager is reachable; this install runs two systemd user units"
  resolve_python

  TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ezchain-native.XXXXXX")"

  step "Python environment"
  ensure_venv
  install_python_packages
  stage_alembic

  step "Native bridge"
  stop_units_if_running
  build_bridge

  step "Web server"
  build_web

  step "Configuration"
  write_environment
  log "wrote ${ENV_FILE} (mode 0600, secrets generated on this host and never printed)"

  step "Database"
  run_migrations

  step "Services"
  install_units
  if [ "$START" -eq 1 ]; then
    wait_for_ready "http://127.0.0.1:${API_PORT}/readyz"
    wait_for_ready "http://127.0.0.1:${WEB_PORT}/"
  fi

  print_summary
}

main "$@"
