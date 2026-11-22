# SELF-HOSTING-NATIVE — ezCHAIN on a host you own, no Docker

Status: **PAPER only.** This is the primary self-hosting path. It installs the paper
workstation directly on a Linux host as two systemd *user* services, with no container
runtime and no root. It does not cover live trading, because live trading does not exist
in this release: there are no keys, no signing, no executor and no venue order path.

The Docker/Compose path in [docs/SELF-HOSTING.md](SELF-HOSTING.md) remains supported as a
secondary, reproducible container artifact. Same services, same data, different supervisor.

The installer is [`tools/install-native.sh`](../tools/install-native.sh). The deployment
contract it implements is [docs/INFRA.md](INFRA.md); incidents and recovery are in
[docs/RUNBOOK.md](RUNBOOK.md).

## 1. What the installer starts, and what it does not

Two services, both owned by your user:

| Unit | Process | Bind |
|---|---|---|
| `ezchain-api.service` | FastAPI edge, inline PAPER engine and market scanner | `127.0.0.1:8000` |
| `ezchain-web.service` | Next.js standalone server | `127.0.0.1:3000` |

The web server is the only listener the browser talks to. `/api/*` is proxied server-side
to the loopback API, and the route handler adds the deployment token, so the token never
reaches the browser. PostgreSQL is a separate process you run: the installer connects to
it and migrates it, it does not start or manage it.

The environment file sets `EZCHAIN_EXECUTION_MODE=paper`. Paper mode is server-derived:
the installer never starts a live stack, and live mode additionally needs a signed gate
record and an executor, neither of which is installed. `services/executor`,
`services/ingest`, Redis, NATS, a standalone engine and the OTel collector are not part of
this stack.

There is no Caddy and no TLS terminator in this path. The default loopback bind is
reachable through an SSH tunnel (§7); for a browser on another machine, put your own TLS
terminator in front.

This is an alpha paper workstation, not a real-money deployment. Do not point it at
funds.

## 2. Trust model: no keys, one operator

**ezCHAIN never holds, sees or signs with your trading keys, and the project never needs
access to your server.** Nobody should give anyone access to a server that holds their
keys: not a maintainer, not a support channel, not a hosted helper. If you would not hand
over the machine, do not hand over SSH, a root token or a copy of the vault.

In this release there are no trading keys at all. The installer does not create, read or
write a wallet, mnemonic, signing key or vault file, and it never writes outside the
install root except the two unit files in your systemd user directory. It runs no
`sudo`, asks for no root password and changes no system-wide setting.

Its outbound traffic is build and install traffic only: the Python package index, crates.io
for the Rust dependencies, and the npm registry for the web dependencies. The script has
no telemetry and phones home to nobody else. `--dry-run` and `--configure-only` make no
network call at all.

### Where keys will live once live mode ships — not available yet

**Live mode has not shipped, and this installer has no key location to configure.** The
normative design is in [docs/LIVE-EXECUTION.md](LIVE-EXECUTION.md) and
[docs/CONTRACT.md](CONTRACT.md): key material is confined to the executor process on the
operator's machine, never in the web bundle, API responses, logs or strategy plugins, and
signing is never exposed through a generic API. Those are design constraints, not a
feature you can deploy today. Do not infer a key path from this guide.

## 3. Prerequisites

- **OS:** Linux with a systemd user manager. The installer refuses to run elsewhere.
- **Python 3.11**, with `venv` and the `ensurepip` module. The workspace targets 3.11
  exactly; another minor version is refused rather than installed into.
- **Rust toolchain** (`cargo`, 1.85 or newer). The API and the money path call a compiled
  PyO3 extension, `ezchain_bridge`, and there is **no Python fallback**: without it the
  clients raise `NativeBridgeUnavailable` and the app fails closed. The installer builds it
  with [`tools/build-native.sh`](../tools/build-native.sh) into the install venv.
- **Node 22 and pnpm 9** (the versions in `package.json`), for the Next.js build. The
  built server is plain Node; only the build needs pnpm.
- **PostgreSQL 13 or newer**, reachable from the host, with a user and password. §6
  explains why SQLite is not supported.
- **Floor:** 2 vCPU, 4 GB RAM, 20 GB disk. These are starting points, not a measured
  benchmark, and the RAM line is the one to be careful with: the Next build's trace phase is
  the peak and it is where an install on a busy host dies first. On the 15 GB host used to
  test this guide, an install was killed by the kernel OOM killer while two Android
  emulators held about 6 GB; the same install finished in 2 minutes once they were gone. Add
  the memory you expect other software to use. 4 vCPU / 8 GB is comfortable for the
  installer alone.
- **No root.** Everything lives under `$EZCHAIN_HOME` (default
  `$HOME/.local/share/ezchain`) plus `~/.config/systemd/user`. A system unit would need
  root; this path deliberately does not use one.
- **Tools the installer checks for:** `bash`, `awk`, `sed`, `mktemp`, `openssl`,
  `curl`, `systemctl`, `cargo`, `node`, `pnpm`.

## 4. Install

From a checkout of the tag you want to run:

```bash
tools/install-native.sh \
  --database-url 'postgresql+psycopg://ezchain:YOUR-PASSWORD@127.0.0.1:5432/ezchain'
```

Or with the environment instead of flags:

```bash
export EZCHAIN_DATABASE_URL='postgresql+psycopg://ezchain:YOUR-PASSWORD@127.0.0.1:5432/ezchain'
export EZCHAIN_PUBLIC_URL='http://127.0.0.1:3000'   # optional; this is the default
tools/install-native.sh
```

The installer then:

1. creates `$EZCHAIN_HOME/venv` (or reuses an existing 3.11 venv there);
2. installs `requirements-runtime.lock` with `--require-hashes`, then the local workspace
   packages non-editable, so the venv keeps working if the checkout moves;
3. stages `apps/api/alembic.ini` and the migration scripts under the install root;
4. builds `ezchain_bridge` into that venv and proves it imports;
5. runs `pnpm install --frozen-lockfile`, builds the Next.js standalone server and stages it
   under `$EZCHAIN_HOME/web`;
6. writes `$EZCHAIN_HOME/ezchain.env` (mode 0600) with a generated `EZCHAIN_API_TOKEN`
   and agent-key pepper that are never printed;
7. runs `alembic -c ... upgrade head` against the configured database;
8. installs `ezchain-api.service` and `ezchain-web.service` into
   `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`, runs `systemctl --user daemon-reload`,
   and enables and starts them;
9. waits for `/readyz` on the API and `/` on the web server, then prints the URL.

On the test host (15 GB RAM, Python 3.11, Rust 1.100, Node 24) a full install took about two
minutes from an empty install root. The host's shared cargo registry and pnpm store were
already populated by earlier builds, so a first install on a fresh machine will spend longer
downloading crates and packages.

A dry run prints every action and changes nothing:

```bash
tools/install-native.sh --dry-run \
  --database-url 'postgresql+psycopg://ezchain:YOUR-PASSWORD@127.0.0.1:5432/ezchain'
```

`--configure-only` writes the environment file and stops: no build, no migration, no
service change, no network. It is how you stage secrets on a host before the first real
run, and it is what the install tests exercise.

`--no-start` installs and enables both units without starting them, and skips the readiness
probe. It is for a host where you want to inspect the units or finish other setup before the
services come up; a later `systemctl --user start ezchain-api ezchain-web` starts them.

Keep the install root off `/tmp`, `/var/tmp` and `/dev/shm`. The units use `PrivateTmp=yes`,
so inside the service those paths are a private mount that does not contain the host's
files; `ReadWritePaths` then points at a path the service cannot see and systemd refuses to
start it. An install refuses such a root before it builds anything, instead of leaving you
with a unit that exits 226/`NAMESPACE`. `--dry-run` and `--configure-only` start no service,
so there they print a warning and continue. The default `$HOME/.local/share/ezchain` is
correct.

The units are installed to `${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user`. If your user
manager reads units from somewhere else, set `EZCHAIN_SYSTEMD_DIR` to that directory; the
installer checks with `systemctl --user cat` that the running manager can actually see the
files before it enables them.

Read limits are per caller. `GET`/`HEAD`/`OPTIONS` under `/api/` are capped at 60 per
minute, so several dashboard tabs or a polling script can see `429 RATE_LIMITED`; the
window clears after a minute. To change it, add
`EZCHAIN_READ_RATE_LIMIT_PER_MINUTE=<n>` to `ezchain.env` (`0` disables the limiter) and
restart the units. The installer does not write that key: the schema default in
`apps/api/src/ezchain_api/config.py` is its one home, and an override belongs to you.

### Database setup

The installer migrates an existing database; it does not create the PostgreSQL role or
database. One minimal setup, if you need it:

```bash
sudo -u postgres createuser --pwprompt ezchain
sudo -u postgres createdb -O ezchain ezchain
```

Then pass `postgresql+psycopg://ezchain:...@127.0.0.1:5432/ezchain`. Keep the password out
of shell history where you can, and percent-encode any character that is not URL-safe.

## 5. The systemd units

Both units are in [`tools/install/native/`](../tools/install/native) and installed to
`~/.config/systemd/user`:

| Directive | Why the unit needs it |
|---|---|
| `Restart=on-failure`, `RestartSec=5s` | restart a crash with a short backoff; a clean `SIGTERM` stop is not restarted |
| `EnvironmentFile=` | the generated `ezchain.env` is the only place the API token and database URL live |
| `NoNewPrivileges=yes` | a compromised process cannot gain privileges through a setuid helper |
| `PrivateTmp=yes` | private `/tmp` and `/var/tmp`; another local process cannot read or race the service's temp files |
| `ProtectSystem=strict` | the whole filesystem is read-only to the service except `ReadWritePaths` |
| `ProtectHome=read-only` | `$HOME` is readable but not writable |
| `ReadWritePaths=$EZCHAIN_HOME/data` | the one writable directory; nothing else in the install root changes at run time |
| `PrivateDevices`, `ProtectKernel*`, `ProtectClock`, `ProtectHostname`, `ProtectProc=invisible` | no device nodes, kernel tunables, module loading, cgroup edits, clock or hostname changes |
| `RestrictNamespaces`, `RestrictSUIDSGID`, `RestrictRealtime`, `LockPersonality` | remove namespace, setuid/setgid, real-time and personality escalation paths |
| `RestrictAddressFamilies` | the API may open UNIX, IPv4, IPv6 and netlink sockets; the web server only UNIX, IPv4 and IPv6 |
| `UMask=0077` | anything the service writes is owner-only |

Every directive is explained in a header comment in each file. The units name
`%h/.local/share/ezchain`; the installer substitutes a non-default `--home` before
installing, and resolves Node into the web unit's `ExecStart`, since a per-user Node
install may not be on the systemd user manager's default `PATH`. The committed files are
mode 644 (configuration, not executables) so `systemd-analyze verify` does not warn, and
they verify on a default-root host.

The units are user units with `WantedBy=default.target`, so they start with your user
session. They do **not** keep running after you log out unless you enable lingering for
that user (`loginctl enable-linger "$USER"`), and they do not start before your first
login. That is the trade for not needing root. Lingering is an operator decision: it keeps
the paper workstation up across logout and reboot.

## 6. Database choice: PostgreSQL, not SQLite

**PostgreSQL is required for a server install.** SQLite is not supported here, and the
installer refuses a `sqlite:` URL with that reason instead of failing later.

The reason is measured, not stylistic. On a fresh SQLite file, the Alembic chain stops at
revision `0003_paper_fill_order`:

```text
sqlalchemy.exc.OperationalError: (sqlite3.OperationalError) near "DO": syntax error
[SQL:
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint WHERE conname = 'uq_paper_fill_order'
            ) THEN
                ALTER TABLE paper_fills
                    ADD CONSTRAINT uq_paper_fill_order UNIQUE (order_id);
            END IF;
        END
        $$;
```

That migration uses a PostgreSQL `DO` block to add a constraint idempotently, so the chain
never reaches head on SQLite: the database stops at `0002_genesis_index` with 8 tables and
`/readyz` would refuse it. On PostgreSQL 16 the same command completes the whole chain and
lands on head `0031_pair_address_width`.

The Electron desktop app is a different deployment and does use SQLite; see
[docs/DESKTOP.md](DESKTOP.md). Its database is created by the app, not by Alembic. Do not
point this installer at that file.

## 7. Reaching the edge

Both services bind loopback only. From your own machine, forward the web port:

```bash
ssh -L 3000:127.0.0.1:3000 <user>@<server>
# then open http://localhost:3000
```

For access from a browser on another machine, put a TLS terminator in front of
`127.0.0.1:3000` and re-run the installer so the browser origin is exact:

```bash
tools/install-native.sh \
  --database-url 'postgresql+psycopg://ezchain:...@127.0.0.1:5432/ezchain' \
  --public-url 'https://<domain>'
```

`--public-url` must be the exact origin the browser uses, scheme included. It feeds the web
proxy's same-origin check, so a mismatch breaks mutations. Do not expose
`127.0.0.1:3000` to a network without that terminator.

Firewall: allow SSH from the addresses you administer from. Do not add rules for `3000` or
`8000` on a host reached over the tunnel; if you add a terminator, allow only its port
(usually 80 and 443).

## 8. Logs

Both services log to the systemd journal:

```bash
journalctl --user -u ezchain-api -u ezchain-web -f      # follow both
journalctl --user -u ezchain-api --since today          # the API only
systemctl --user status ezchain-api ezchain-web         # state and last lines
```

Operational endpoints stay off the loopback edge the same way they are off the Docker edge.
`/healthz` and `/readyz` are public probes; `/metrics`, `/health`, `/docs`, `/redoc` and
`/openapi.json` are reachable only from a loopback caller with `EZCHAIN_API_TOKEN`:

```bash
curl -sS -H "Authorization: Bearer $EZCHAIN_API_TOKEN" http://127.0.0.1:8000/metrics
```

Do not paste that token into an issue, a log, a screenshot or a `NEXT_PUBLIC_*` variable.

## 9. Upgrade, rerun and uninstall

Re-running the installer from a newer checkout is the upgrade. It reuses the venv,
rebuilds only what changed, keeps `ezchain.env` and its secrets, re-stages the web bundle,
runs `alembic upgrade head`, reloads the units and restarts the services. Existing secrets
are only generated when missing; a pre-existing value shorter than 32 characters is an
error, never silently replaced.

If the units are running, the installer stops them before it replaces the installed
`ezchain_bridge` extension and starts them again through `systemctl --user enable --now`
once the units are refreshed. This is deliberate: rewriting a mapped shared object under a
live interpreter terminates that process (observed as `SIGSEGV` while developing this
installer). The brief downtime is the cost of an in-place upgrade.

The database is only ever migrated forward. Nothing in the installer drops, truncates or
recreates a database, and a failed migration stops the run rather than rolling back.
Migrations are forward-only ([docs/CONTRACT.md](CONTRACT.md), I8), so a bad migration is
fixed forward on a branch, not undone in place.

```bash
# upgrade in place: same root, same database, same secrets
git -C /path/to/ezchain fetch && git -C /path/to/ezchain checkout <new-tag>
tools/install-native.sh --database-url 'postgresql+psycopg://ezchain:...@127.0.0.1:5432/ezchain'

# restart without reinstalling
systemctl --user restart ezchain-api ezchain-web

# remove the services; data, venv and env file are kept
tools/install-native.sh --uninstall
```

`--uninstall` stops and disables both units and deletes the two unit files. It keeps
`$EZCHAIN_HOME` — environment file, venv, staged web bundle, data directory — and does not
touch the database. Deleting any of those is your deliberate act; a re-install picks the
existing secrets and data back up.

Rollback of code is checking out the previous tag and re-running the installer. Schema
rollback is an incident, not a routine command ([docs/INFRA.md §8](INFRA.md#8-upgrade-and-rollback)).

## 10. Backup and restore

Take a dump before every upgrade and keep it off the server:

```bash
pg_dump -Fc --dbname 'postgresql://ezchain:...@127.0.0.1:5432/ezchain' \
  > "ezchain-$(date +%F).dump"
```

Back up `$EZCHAIN_HOME/ezchain.env` as well: it holds the secrets the running services use.
Without it a restored application cannot authenticate to the same database.

Restore non-destructively into a scratch database first, verify it, and only then think
about replacing the live one:

```bash
createdb -O ezchain ezchain_restore
pg_restore --no-owner -d ezchain_restore ezchain-YYYY-MM-DD.dump
```

Point a scratch install at the restored database (`EZCHAIN_DATABASE_URL=.../ezchain_restore`,
a second `--home`, and a different `--public-url`) and compare row counts and
`GET /api/v1/paper/reconciliation` against what you expect. A disaster restore into the
live database is a deliberate, audited action: stop both units, restore, then start them.
A restore that cannot reproduce the ledger to the last entry is a failed drill. The
authoritative expectations are [docs/INFRA.md §7](INFRA.md#7-storage-backup-and-restore).

A backup you have not restored is not a backup you have tested.

## 11. Tested with, and what is not verified

**Tested on:** Fedora 45 (Linux 7.3.4, KDE), systemd 262, Python 3.11.18, Rust 1.100.0,
Node 24.22.3, pnpm 9.15.9, Next 15.5.28, PostgreSQL 16.16 (in a throwaway rootless Podman
container on this host, reached over loopback).

What was actually run, and what it returned:

- **A full install end to end**, from a clean install root and a fresh database, with
  `--home` under `$HOME` and a real PostgreSQL 16 URL. Exit 0 in 2m03s. The log shows every
  stage: venv created, `requirements-runtime.lock` installed with `--require-hashes`, the
  five workspace packages installed, the bridge built by `tools/build-native.sh`, `pnpm
  install --frozen-lockfile`, the Next build, the standalone bundle staged, `ezchain.env`
  written, migrations applied, both units installed and enabled, then `waiting for
  http://127.0.0.1:8000/readyz` and `waiting for http://127.0.0.1:3000/` both returning
  before the summary printed. This was the second attempt: the first was killed by the
  kernel OOM killer in Next's build-trace phase while two Android emulators held about 6 GB
  of the 15 GB host, so the memory floor in §3 is a real constraint rather than a guess. A
  later install of the same shape, followed by a rerun, a readiness check and `--uninstall`,
  repeated cleanly.
- **The result, checked after it returned:** `systemctl --user is-active` reports `active`
  for both units with `NRestarts=0`; `GET /healthz` 200 and `GET /readyz` 200 with
  `{"status":"ready","db":"ok","migration":"head","revision":"0031_pair_address_width","head":"0031_pair_address_width"}`;
  `GET /` on the web server 200. On the earlier install of the same shape,
  `GET /api/v1/paper/limits` through the web proxy returned the paper payload with 200 once
  the read-limiter window had cleared. `systemctl --user show` confirms on both units
  `NoNewPrivileges=yes`, `PrivateTmp=yes`, `ProtectSystem=strict`,
  `ProtectHome=read-only`, `ReadWritePaths=<root>/data` and `UMask=0077`, with the API unit
  additionally admitting `AF_NETLINK`.
- **A rerun (upgrade) over the running install.** Exit 0, `EZCHAIN_API_TOKEN` byte-identical
  before and after, both units `active` afterwards and `/readyz` 200. The run stops the
  services before replacing the installed bridge: rewriting a mapped `.so` under a live
  interpreter killed the previous process with `SIGSEGV`, and that was observed, not
  assumed.
- **`--uninstall`** exited 0: both units stopped and disabled, their files gone from
  `~/.config/systemd/user`, ports 8000 and 3000 free, and the install root, venv, env file
  and database kept.
- **`--dry-run --database-url ...`** exits 0. It printed the plan, redacted the password as
  `ezchain:***@`, and left the temporary `HOME` empty. The same run under `unshare -rn` (no
  route at all) also exited 0, so the dry run makes no network call.
- **`--configure-only --home <tmp> --database-url ...`** wrote `ezchain.env` with mode 0600,
  two 64-hex generated secrets, and neither secret appeared in the output. A second run kept
  the token; an existing short token was refused with `shorter than 32 characters` and the
  file was left byte-for-byte unchanged.
- **A `sqlite:///` URL** is refused with the migration reason before anything is written, as
  is an install root under `/tmp`, `/var/tmp` or `/dev/shm` (see §4).
- **`systemd-analyze --user verify tools/install/native/*.service`** exits 0 on both units
  (with a temporary `HOME` that has a venv `bin/python` and the env file, since systemd
  checks those paths).
- **`pytest -q tests/install/test_native_install.py`** — 15 passed. It covers the dry run and
  its no-network/no-write properties, the SQLite refusal, the refusal of a `PrivateTmp`-hidden
  install root, the 0600 environment file and token redaction, a value containing `=` surviving
  a rewrite, rerun idempotence, the unit directives, `systemd-analyze verify` where available,
  and a static assertion on the API Dockerfile. The whole `tests/install` directory is
  69 passed, 1 skipped.
- **The Alembic chain against PostgreSQL 16** from the staged install layout (the exact
  `apps/api/alembic.ini` + `apps/api/alembic` the installer copies) completed and reached
  `0031_pair_address_width` with 23 tables in the `public` schema.
- **The API image built with rootless Podman** from `apps/api/Dockerfile`, the same build a
  Docker host runs: `native.using_rust()` is `True` inside it and
  `ezchain_bridge.__file__` is `/usr/local/lib/python3.11/site-packages/ezchain_bridge.so`.
  Run against the same PostgreSQL 16 at head `0031_pair_address_width`, `/healthz` answered
  200 and `/readyz` answered 200 with
  `{"status":"ready","db":"ok","migration":"head","revision":"0031_pair_address_width","head":"0031_pair_address_width"}`.

Measured behaviour worth knowing before you read a log:

- **The read limiter is per caller and per minute.** `EZCHAIN_READ_RATE_LIMIT_PER_MINUTE`
  defaults to 60 reads/minute for `GET`/`HEAD`/`OPTIONS` under `/api/`. Browsing the
  dashboard in several tabs, or any script that polls it, gets `429` with
  `{"detail":{"code":"RATE_LIMITED","retryable":true}}` and a `Retry-After` header. The
  server keeps working; the window clears after 60 seconds. Raise the limit deliberately if
  you need to, rather than treating the 429 as an outage.
- **A cancelled quote request can log a 500.** On the first page load after a fresh start,
  the dashboard's quote fan-out issued three `/api/v1/quotes` requests whose caller had
  already gone; uvicorn logged `ERROR: Exception in ASGI application` with
  `anyio.EndOfStream` / `RuntimeError: No response returned.` and answered 500 for those
  three. The service stayed up (`NRestarts=0`) and the same page loads succeeded on
  repeat. It is the ASGI layer reporting a disconnected client, not the ledger or the
  gate; treat a 500 with that traceback as a dropped connection, and a repeating one as a
  real fault to report.

Not verified here, and therefore not claimed:

- **A clean install on hardware other than this authoring machine.** Everything above ran
  on one Fedora 45 host against a local PostgreSQL. No other distribution, no remote
  database over TLS, no arm64, no SELinux-enforcing variant was exercised.
- **An install under memory pressure.** The OOM kill above was environmental (other
  software), and the rerun succeeded with the same installer. No low-memory host was
  deliberately tested, so "it fits in 4 GB" is not something this guide claims to have
  measured.
- **Lingering, reboot survival, log rotation and TLS termination**, which are operator
  choices on the host (§5, §7). The units were enabled and started, but not across a
  reboot, because `loginctl enable-linger` was not turned on here.
- **The PostgreSQL role and database setup in §4.** The installer connects to an existing
  database; the `createuser`/`createdb` snippet is standard PostgreSQL usage and was not
  executed here (the test database already existed).
- **Uvicorn's bind behaviour behind a terminator**, i.e. any proxy configuration other than
  the loopback default. No TLS terminator was placed in front.

Update this section with a fresh-host run when one exists.

## 12. What this guide does not cover

- **Live mode, keys and the executor.** Not available in this release. No key custody,
  no vault configuration, no signing, no venue orders. See §2 and
  [docs/LIVE-EXECUTION.md](LIVE-EXECUTION.md).
- **Installing or tuning PostgreSQL.** Use your distribution's package or your managed
  database; this installer only connects and migrates.
- **Multi-user hosting, per-user auth and RBAC.** One instance is one paper workspace, and
  the browser never receives the shared token, but anyone who reaches the origin can mutate
  the same paper account.
- **Horizontal scaling, Kubernetes and Helm.** No manifests are shipped.
- **macOS, Windows and container-only hosts.** Linux with systemd is the target; the
  Docker path in [docs/SELF-HOSTING.md](SELF-HOSTING.md) is for hosts that have Docker.
- **Choosing a venue, funding an account or any trading decision.** Those are operator
  decisions outside the software.

For the container path, local development and the service-level details, read
[docs/INFRA.md](INFRA.md) and [tools/install/README.md](../tools/install/README.md).
