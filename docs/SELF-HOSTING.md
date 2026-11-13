# SELF-HOSTING — ezCHAIN on a server you own

Status: **PAPER only.** This guide covers running the paper workstation on one Linux
server. It does not cover live trading, because live trading does not exist in this
release. There are no keys, no signing, no executor and no venue order path.

The supported install path is [`tools/install/install.sh`](../tools/install/install.sh),
covered in [tools/install/README.md](../tools/install/README.md). The deployment contract
behind it is [docs/INFRA.md](INFRA.md); incidents and recovery are in
[docs/RUNBOOK.md](RUNBOOK.md).

## 1. What the installer starts, and what it does not

The stack is Postgres, the one-shot Alembic migration job, the API, the Next.js web app
and Caddy. Compose starts them in that order and Caddy is the only published host port.
The full service table is in [INFRA §2](INFRA.md#2-services-in-the-paper-stack).

The environment file sets `EZCHAIN_EXECUTION_MODE=paper`. Paper mode is server-derived:
the installer never starts a live stack, and live mode additionally needs a signed gate
record and an executor, neither of which is installed. `services/executor`, `services/ingest`,
Redis, NATS, a standalone engine and the OTel collector are not part of this stack; they
are recorded in [`infra/compose.blueprint.yaml`](../infra/compose.blueprint.yaml).

This is an alpha paper workstation, not a real-money deployment. Do not point it at
funds.

## 2. Trust model: keys, access, and the shared origin

**ezCHAIN never holds, sees or signs with your trading keys, and the project never needs
access to your server.** Nobody should give anyone access to a server that holds their
keys: not a maintainer, not a support channel, not a hosted helper. If you would not hand
over the machine, do not hand over SSH, a root token or a copy of the vault.

In this release there are no trading keys at all. The installer does not create, read or
write a wallet, mnemonic, signing key or vault file. Its outbound requests are the
distribution mirrors for Docker and its helper packages (only when Docker is missing,
including `apt-get update` and ca-certificates, curl, gnupg and openssl), the GitHub API
to resolve the latest release when no version is named, and the release download itself.
Nothing else leaves the server.

### Where keys will live once live mode ships — not available yet

**Live mode has not shipped, and this installer has no key location to configure.** The
normative design is in [docs/LIVE-EXECUTION.md](LIVE-EXECUTION.md) and
[docs/CONTRACT.md](CONTRACT.md): key material is confined to the executor process on the
operator's machine, never in the web bundle, API responses, logs or strategy plugins, and
signing is never exposed through a generic API. Those are design constraints, not a
feature you can deploy today. Do not infer a key path from this guide.

### The shared HTTPS origin is one trusted workspace

**One HTTPS origin is one paper workspace.** Every browser that reaches the edge uses the
same deployment bearer token, which the Next.js route handler adds server-side; the
browser never receives it. There is no per-user auth and no RBAC, so anyone who can reach
the origin can mutate the same paper account. Treat the origin as trusted, and keep the
default loopback bind unless you have put it behind your own access control.

## 3. Server sizing and prerequisites

- **OS:** Linux. The automatic Docker install supports Ubuntu and Debian; on another
  distribution, install Docker Engine and the Compose v2 plugin yourself.
- **Floor:** 2 vCPU, 4 GB RAM, 20 GB disk. The Compose resource limits add up to about
  3.25 CPUs and 2.4 GiB across the five services, and image builds are the peak. A
  4 vCPU / 8 GB host builds more comfortably. These are starting points, not a measured
  benchmark.
- **Network:** outbound HTTPS to Docker's package repository (only when Docker is
  missing) and to the release URL. No inbound port is needed while the edge stays on
  loopback.
- **Tools:** `curl`, `tar`, `awk`, `sed`, `openssl` and a SHA-256 tool (`sha256sum`,
  `shasum` or `openssl`). The installer checks for them.
- **Access:** a user with `sudo` when Docker is not already installed.

## 4. Firewall

Caddy publishes the web edge, and nothing else. Postgres and the API are never published
to the host; `/metrics`, `/health`, `/docs`, `/redoc` and `/openapi.json` are not published
through the edge either (§8).

Recommended inbound policy:

- allow SSH from the addresses you administer from, not the whole internet;
- allow 80 and 443 only when a TLS terminator is in front (§7);
- do not allow the edge port (default 8080) from the internet;
- do not add rules for 5432 or 8000.

Example with `ufw` — adapt it to your provider's firewall as well. This example is not
exercised by the installer tests:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status verbose
```

## 5. Install: verify before extract

Download the archive and its checksum, verify the archive, then extract and run the
installer. `<tag>` is a published release tag. At the time of writing no release tag has
been published; see §11.

```bash
TAG="<tag>"
curl -fsSLO "https://github.com/0xJ1nn/ezchain/releases/download/${TAG}/ezchain-${TAG}.tar.gz"
curl -fsSLO "https://github.com/0xJ1nn/ezchain/releases/download/${TAG}/ezchain-${TAG}.tar.gz.sha256"

# The checksum file names the archive. A bare digest, or one written for a
# different file, is refused.
sha256sum -c "ezchain-${TAG}.tar.gz.sha256"

tar -xzf "ezchain-${TAG}.tar.gz"
"./ezchain-${TAG}/tools/install/install.sh" --version "${TAG}"
```

The digest comes from the same HTTPS origin as the archive, so it proves the download was
not corrupted or altered in transit. It cannot defend against a compromised release host,
which could replace both files. The installer repeats the verification itself and refuses
a mismatch; it also refuses archive paths that escape the extraction root and any
symlink, hardlink or special file in the archive.

The installer then:

1. installs Docker Engine, Buildx and Compose v2 from Docker's apt repository when they
   are missing (Ubuntu/Debian only), unless `--skip-docker-install` was given;
2. downloads `ezchain-<tag>.tar.gz` and its `.sha256` when you did not pass `--archive`,
   and verifies them;
3. generates the deployment secrets on the host (§6);
4. writes `$EZCHAIN_INSTALL_DIR/ezchain.env` (mode 0600), points the release directory at
   it, and starts the stack with the Compose project name `ezchain`;
5. waits for `/readyz` and checks the edge surface, then prints the URL and next steps.

The defaults are install root `$HOME/ezchain`, edge bind `127.0.0.1:8080`, and public URL
derived from them. To install from a local archive instead of a download, pass
`--archive <path> --sha256 <64-hex-digest>`; `--sha256` is required with `--archive`.

A dry run changes nothing:

```bash
"./ezchain-${TAG}/tools/install/install.sh" --version "${TAG}" --dry-run
```

`--no-start` fetches, verifies and configures without starting containers. On a host
that is not Linux, `--no-start` and `--dry-run` are the supported modes.

## 6. Secrets

There are exactly three deployment secrets, and they are generated on the server:

| Secret | Used for |
|---|---|
| `POSTGRES_PASSWORD` | the `ezchain` database user |
| `EZCHAIN_API_TOKEN` | the shared bearer token for `/api/*`, added server-side by the web proxy |
| `EZCHAIN_API_KEY_PEPPER` | scoped agent-key hashing |

They live in `$EZCHAIN_INSTALL_DIR/ezchain.env`, mode 0600, created with
`umask 077` and `openssl rand -hex 32`. The installer never prints or transmits them.
They are not in the release archive, the images or the repository. The script has no
telemetry and phones home to nobody.

They never leave the server through ezCHAIN: the API token is consumed by the Next.js
route handler inside the Compose network, the database password is consumed by Compose
and Postgres, and no component sends either to the project or to a third party. Your
own backup policy is the exception you choose: if you back up the install root, the
environment file is part of that backup.

Do not commit the file, paste it into an issue, or put the token in a `NEXT_PUBLIC_*`
variable, browser code, screenshot or log. Reads are unauthenticated in this stack
(`EZCHAIN_REQUIRE_AUTH_FOR_READS=false`); mutations require the token. The edge injects
one token for every caller, so this is a shared deployment secret, not per-user auth.

Re-running the installer keeps existing values. It only generates a missing key, and a
pre-existing value shorter than 32 characters is an error rather than something it
silently replaces.

## 7. TLS and reaching the edge

The default `127.0.0.1:8080` bind is reachable only from the server itself. Use an SSH
tunnel from your machine:

```bash
ssh -L 8080:127.0.0.1:8080 <user>@<server>
# then open http://localhost:8080
```

For access from a browser, put a TLS terminator in front. Caddy inside the stack serves
plain HTTP on `:8080` and does not terminate TLS; use your own Caddy, nginx, or a cloud
load balancer, then re-run the installer so the browser origin is exact:

```bash
"$HOME/ezchain/current/tools/install/install.sh" \
  --version "${TAG}" --bind 0.0.0.0 --public-url "https://<domain>"
```

`--public-url` must be the exact origin the browser uses (scheme included). It feeds the
web proxy's same-origin check, so a mismatch breaks mutations. Do not bind `0.0.0.0`
without a TLS terminator and a matching public URL.

## 8. Operational endpoints and what the edge publishes

| Path | Through the edge | Reach it how |
|---|---|---|
| `/` and the UI | yes | browser |
| `/api/*` | yes | through the Next.js route handler, which adds the bearer token |
| `/healthz`, `/readyz` | yes | public probes |
| `/metrics`, `/health` | **404** | `api:8000` over the Docker network with `EZCHAIN_API_TOKEN`; the API also serves them to a loopback caller |
| `/docs`, `/redoc`, `/openapi.json` | **404** | not published; use a local API or an operator tunnel |

After startup the installer probes every denied path through the edge and aborts if one
answers with anything other than 404, and checks that both public probes answer 200. If
you add your own reverse proxy, do not route those operator paths to `api:8000`.

The optional observability profile publishes Prometheus on `127.0.0.1:9090` and Grafana
on `127.0.0.1:3001`; it is not part of the default install. See
[INFRA §6](INFRA.md#6-health-metrics-and-alerts).

## 9. Rerun, upgrade and data retention

Re-running the installer at the same tag is safe. If the release marker exists it reuses
the release instead of downloading it, keeps `ezchain.env` and its secrets, and runs
`compose up` again to reconcile the containers.

To upgrade, run the new tag's installer (the copy under `current` works):

```bash
"$HOME/ezchain/current/tools/install/install.sh" --version "<new-tag>"
```

The installer downloads and verifies the new release, keeps the existing secrets, tries a
best-effort `pg_dump` into `$EZCHAIN_INSTALL_DIR/backups/`, moves the `current` symlink,
and starts the stack. A failed dump is a warning, not a refusal; take your own verified
dump before an upgrade (§10). Migrations are forward-only, so a failed migration stops the
rollout and is fixed forward, not rolled back.

Data lives in the named volume `ezchain_pgdata`. It survives reruns, upgrades and
`docker compose down`; only an explicit volume removal deletes it. Old release
directories stay under `$EZCHAIN_INSTALL_DIR/releases/`, secrets stay in `ezchain.env`,
and the pre-upgrade dumps stay in `backups/`. There is no automatic pruning or retention
policy; clean those up deliberately.

Rollback of code is re-running the installer with the previous tag; the previous release
and the volume are still on the host. Schema rollback is an incident, not a routine
command ([INFRA §8](INFRA.md#8-upgrade-and-rollback)).

```bash
# logs
docker compose -p ezchain -f "$HOME/ezchain/current/infra/compose.yaml" logs -f

# stop the stack (the data volume remains)
docker compose -p ezchain -f "$HOME/ezchain/current/infra/compose.yaml" down

# destructive: delete the paper database; take a verified dump first
docker volume rm ezchain_pgdata
```

## 10. Backup and restore

Take a dump before every upgrade and keep it off the server:

```bash
docker compose -p ezchain -f "$HOME/ezchain/current/infra/compose.yaml" \
  exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' \
  > "ezchain-$(date +%F).dump"
```

Restore non-destructively into a scratch database first, verify it, and only then replace
the live one:

```bash
docker compose -p ezchain -f "$HOME/ezchain/current/infra/compose.yaml" \
  exec -T postgres sh -c 'createdb -U "$POSTGRES_USER" ezchain_restore'

docker compose -p ezchain -f "$HOME/ezchain/current/infra/compose.yaml" \
  exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d ezchain_restore --no-owner' < ezchain-YYYY-MM-DD.dump
```

Compare row counts and check `GET /api/v1/paper/reconciliation` on a stack pointed at the
scratch database. A disaster restore into `ezchain` is a deliberate, audited action: stop
the API and web, restore, then start `migrate`, `api`, `web` and `caddy` again. A restore
that cannot reproduce the ledger to the last entry is a failed drill. The authoritative
steps, including the exact verification expectations, are
[INFRA §7](INFRA.md#7-storage-backup-and-restore).

The installer's pre-upgrade dump is a convenience, not a backup guarantee. A backup you
have not restored is not a backup you have tested.

## 11. Tested with, and what is not verified

**Tested with:** commit `5c0db13` (the installer change from #272). **No release tag has
been published at the time of writing** — `git tag` and the GitHub release list are both
empty — so the guide uses `<tag>` and the release URL is the path the installer implements,
not one that has been downloaded publicly.

What is exercised automatically at that commit:

- `tests/install/test_container_install.sh` starts a clean privileged Docker-in-Docker
  container, installs a locally packaged release with the real installer, checks
  `/healthz`, `/readyz` and the denied operational paths, re-runs the same tag, then
  installs a second tag and checks that the release symlink moved, the backup directory
  exists, and the secrets and a seeded database row survived. It packages
  `v0.0.0-installtest` and `v0.0.1-installtest`, not a public release. It prints `SKIP`
  when no Docker daemon is reachable.
- `tests/install/test_install.py` covers the dry run, checksum refusal, archive traversal
  and symlink refusal, secret generation and permissions, rerun idempotence and the
  absence of telemetry. The authoring host for this guide had no Docker daemon, so the
  clean-container run was not re-executed here.

Not verified by that test, and therefore not claimed here:

- a download from a published release, because no release exists yet;
- public DNS, a real TLS terminator and the firewall example in §4;
- the restore commands in §10 at the current migration head. The last recorded
  dump/restore drill is the 2026-10-22 Docker drill
  ([docs/evidence/p0-production/docker-up.md](evidence/p0-production/docker-up.md)),
  which ran at migration head `0003_paper_fill_order`; [PRODUCTION-READINESS §2](PRODUCTION-READINESS.md)
  records that the stack has not been re-drilled at the current head.

Update this section with the release tag and the fresh-server result when both exist.

## 12. What this guide does not cover

- **Live mode, keys and the executor.** Not available in this release. No key custody,
  no vault configuration, no signing, no venue orders. See the note in §2 and
  [docs/LIVE-EXECUTION.md](LIVE-EXECUTION.md).
- **Multi-user hosting, per-user auth and RBAC.** One instance is one paper workspace.
- **Horizontal scaling, Kubernetes and Helm.** No manifests are shipped.
- **Windows and macOS hosts.** Linux is the target; the installer's start path requires
  Linux.
- **Choosing a venue, funding an account or any trading decision.** Those are operator
  decisions outside the software.

For the manual Compose path, local development and the service-level details, read
[docs/INFRA.md](INFRA.md) and [tools/install/README.md](../tools/install/README.md).
