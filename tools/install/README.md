# Installer — one command on a server you own

`tools/install/install.sh` installs ezCHAIN on a fresh Linux server: Docker if it is
missing, a checksum-verified release, the deployment secrets, and the PAPER stack
(Postgres, migrations, API, web, Caddy).

It is the supported path for a self-hosted server. The manual
[`infra/compose.yaml`](../../infra/compose.yaml) route still works and is what the
installer drives.

```bash
# on the server, as a user with sudo
curl -fsSLO https://github.com/0xJ1nn/ezchain/releases/download/<tag>/ezchain-<tag>.tar.gz
curl -fsSLO https://github.com/0xJ1nn/ezchain/releases/download/<tag>/ezchain-<tag>.tar.gz.sha256

tar -xzf ezchain-<tag>.tar.gz
./ezchain-<tag>/tools/install/install.sh --version <tag>
```

Or run it from a checkout of the tagged release:

```bash
tools/install/install.sh --version v0.2.0
```

The default edge bind is `127.0.0.1:8080`: reach it with an SSH tunnel, or pass
`--bind 0.0.0.0` behind a TLS terminator. The API and Postgres are never published to
the host.

## What it changes

| Action | Detail |
|---|---|
| Docker | Installs Engine, Buildx and Compose v2 from Docker's apt repository on Ubuntu/Debian when `docker` is missing. `--skip-docker-install` fails instead. |
| Release | Downloads `ezchain-<tag>.tar.gz` and its `.sha256`, and refuses a mismatch. `--archive` + `--sha256` installs a local archive. |
| Secrets | Generates `POSTGRES_PASSWORD`, `EZCHAIN_API_TOKEN` and `EZCHAIN_API_KEY_PEPPER` on this host with `openssl rand -hex 32`. |
| Configuration | Writes `$EZCHAIN_INSTALL_DIR/ezchain.env` (mode 0600) and links it as the compose env file. |
| Stack | `docker compose up -d --build` with the project name `ezchain`; waits for `/readyz` and verifies the edge surface. |
| Upgrade | Re-running with a newer `--version` swaps the release symlink, takes a best-effort database dump, and keeps the secrets and named volume. |

It does **not** create, read or write trading key material. No wallet, mnemonic, signing
key or vault file is touched; those stay with the operator or the vault. The script has
no telemetry and phones home to nobody. Its only outbound requests are the Docker package
repository (when Docker is missing) and the release URL.

## Options

```text
--version <tag>        release tag (default: latest published release)
--dir <path>           install root (default: $HOME/ezchain)
--bind <address>       host address for the edge (default: 127.0.0.1)
--port <port>          host port for the edge (default: 8080; the container port is fixed)
--public-url <url>     browser origin; defaults to http://<host>:<port>
--archive <path>       local release archive instead of a download
--sha256 <hex>         expected SHA-256 for --archive (required)
--base-url <url>       release download base (default: GitHub releases)
--skip-docker-install  fail instead of installing Docker
--no-start             fetch, verify and configure only
--dry-run              print the plan, change nothing
```

Environment equivalents: `EZCHAIN_INSTALL_VERSION`, `EZCHAIN_INSTALL_DIR`,
`EZCHAIN_BIND`, `EZCHAIN_PORT`, `EZCHAIN_PUBLIC_URL`, `EZCHAIN_HOST`, `EZCHAIN_REPO`,
`EZCHAIN_RELEASE_BASE_URL`, `EZCHAIN_ARCHIVE`, `EZCHAIN_ARCHIVE_SHA256`,
`EZCHAIN_INSTALL_WAIT_SECONDS`.

## Endpoints

The edge publishes the product UI and the two public probes only.

| Path | Through the edge | Notes |
|---|---|---|
| `/` and the UI | yes | Product UI |
| `/api/*` | yes | Goes through the Next.js route handler, which adds the bearer token server-side |
| `/healthz` | yes | Public liveness probe |
| `/readyz` | yes | Public readiness probe (database + migration head) |
| `/metrics`, `/health` | **404** | Operator-only. Scrape `api:8000` over the Docker network with `EZCHAIN_API_TOKEN`; the API also serves them to a loopback caller |
| `/docs`, `/redoc`, `/openapi.json` | **404** | Interactive schema, loopback-only in the API and denied at the edge |

The installer aborts if any denied path answers through the edge or if a public probe
does not answer 200.

Paper mode is server-derived: the environment file sets `EZCHAIN_EXECUTION_MODE=paper`.
Live needs a signed gate record and the executor, neither of which this installer
installs.

## Rerun, upgrade, rollback, backup

Re-running the same tag is safe: the release is reused, the secrets are kept, and
`compose up` reconciles the containers. Secrets are only generated when a key is missing;
a pre-existing value shorter than 32 characters is an error, never silently replaced.

```bash
# upgrade (data and secrets are kept)
$HOME/ezchain/current/tools/install/install.sh --version v0.2.0

# logs
docker compose -p ezchain -f $HOME/ezchain/current/infra/compose.yaml logs -f

# backup
docker compose -p ezchain -f $HOME/ezchain/current/infra/compose.yaml \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > ezchain-$(date +%F).dump
```

Before an upgrade the installer tries to write a dump to
`$EZCHAIN_INSTALL_DIR/backups/`. A failed dump is a warning; take your own backup for a
production upgrade. Migrations are forward-only. Roll back by re-running the installer
with the previous tag; the old image and volume stay on the host.

`docker compose down` stops the stack; `docker volume rm ezchain_pgdata` deletes the
data. That is a deliberate operator action, not part of the installer.

## Releases

A release is two assets: `ezchain-<tag>.tar.gz` and `ezchain-<tag>.tar.gz.sha256`.
Build them from a tag, never a working tree:

```bash
tools/install/package-release.sh --tag v0.2.0
# archive  .../dist/ezchain-v0.2.0.tar.gz
# sha256   <digest>
# checksum .../dist/ezchain-v0.2.0.tar.gz.sha256
```

Upload both to the GitHub release. `--worktree` exists for the tests and must not be
used for a published release. `--source <dir>` packages another checkout, which is how
the test suite exercises the packager.

## Tests

Host tests (no Docker required):

```bash
pytest tests/install/test_install.py
```

They cover the dry run (no filesystem changes), the real download path with a published
`.sha256`, checksum refusal, archive traversal and symlink refusal, secret generation and
permissions, rerun idempotence, the packager, the edge policy, and the absence of
telemetry.

Clean-container test:

```bash
tests/install/test_container_install.sh
```

It starts a privileged Docker-in-Docker container, runs the installer against a locally
packaged release, checks `/healthz`, `/readyz` and the denied operational paths, re-runs
the installer, and verifies the secrets were not regenerated and a seeded database row
survived. Docker is required; without a daemon it prints `SKIP` and the container run is
not verified.

Deployment topology and the manual path are in
[`docs/INFRA.md`](../../docs/INFRA.md); the runnable stack is
[`infra/compose.yaml`](../../infra/compose.yaml).
