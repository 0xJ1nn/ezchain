# SELF-HOSTING-NATIVE — first-run readiness on a host you own

Status: **PAPER only.** A native install runs the API and the web server as two systemd
*user* services, without a container runtime and without root. This page documents the
read-only readiness check for that install. The container path is in
[SELF-HOSTING.md](SELF-HOSTING.md).

The check is a command, not a dashboard: it answers the questions an operator would
otherwise assemble from several documents, and it changes nothing.

## Where the decisions live

A host installed here keeps its deployment configuration in the environment file written by
the installer. A desktop workstation keeps the same kind of decisions in `host.json` in its
application data directory, shown and changed on its first-run setup screen. There is one
file of defaults per install, not a second copy of them: the supervisor derives the database
URL, backup directory and execution mode from the values the operator approved and hands
them to the API as `EZCHAIN_DATABASE_URL`, `EZCHAIN_BACKUP_DIR` and
`EZCHAIN_EXECUTION_MODE`. Both paths set paper mode. See
[DESKTOP](DESKTOP.md#first-run-setup).

A backup directory must exist and be writable when it is chosen. The setup screen refuses a
bad path with the reason instead of failing at the first backup. A snapshot is a consistent
SQLite copy that restores by replacing the configured database, and it holds no credential
material.

## First-run readiness check

Run it after the installer has written the environment file and migrated the database, and
again before you expose the API to anything other than a loopback listener:

```bash
ezchain-api readiness
```

Add `--bind` when the API is not on the default loopback address, point `--backup-dir` at
the directory holding your backup manifests, and lower or raise the freshness budget if
your backup cadence differs:

```bash
ezchain-api readiness \
  --bind 127.0.0.1 \
  --backup-dir <install-root>/backups \
  --max-backup-age-hours 24
```

| Option | Default | Meaning |
|---|---|---|
| `--bind` | `EZCHAIN_BIND`, else `127.0.0.1` | Address the API is configured to listen on. An address other than loopback needs a deployment token. |
| `--backup-dir` | `EZCHAIN_BACKUP_DIR`, else `./backups` | Directory scanned for `*.manifest.json` backup manifests. |
| `--max-backup-age-hours` | `24` | Oldest backup still counted as fresh. |

The command reports five facts:

- **listener** — the configured bind address and whether it is loopback;
- **deployment token** — *set* or *unset*, never the value;
- **read authentication** — *open* or *required*;
- **migration** — the applied Alembic revision against this checkout's head;
- **backup** — the newest verified backup, its age and creation time.

It prints `result: READY` and exits `0` only when every check passes. It exits `1` when the
listener is not loopback without a token, when the applied schema is behind the head or
cannot be read, or when there is no fresh verified backup. Each failed check is named on a
`problem:` line so an operator can fix the cause rather than guess at the exit code.

A backup counts as verified when it has a manifest written by `tools.ops.backup` whose dump
still matches the SHA-256 recorded in that manifest. The tool reads the dump to compute the
hash; it never prints the dump, a credential or the database URL. A backup you have never
restored is still only a candidate — [docs/RUNBOOK.md](RUNBOOK.md#8-restore-drill--disaster-recovery)
is the separate rehearsal that proves a restore works.

```text
ezchain first-run readiness
listener: 127.0.0.1 (loopback)
deployment token: set
read authentication: open
migration: head (0031_pair_address_width)
backup: verified (3.2 hours old, 2026-11-23T01:00:00+00:00)
result: READY
```

Nothing the command prints contains credential material: no token, pepper or database
password, no `Authorization` header and no raw connection error. The database URL is never
printed, so a password in it cannot reach a log, an issue or a screenshot.
