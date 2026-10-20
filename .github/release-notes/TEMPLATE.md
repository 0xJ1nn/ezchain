# Release vX.Y.Z — YYYY-MM-DD

One honest paragraph: what this release changes for the user, and what it does not.

## Highlights

- …

## Upgrade notes

- Backup first: `ezchain-admin backup`.
- Migrations: forward-only, N new revisions; no destructive changes.
- Config: new keys, removed keys, changed defaults.
- Downgrade path: previous image `ghcr.io/ezchain/api:<digest>`; schema rollback is an incident.

## Verification

| Gate | Result |
|---|---|
| `tools/ci.sh` | pass (commit …) |
| compose smoke | pass (paper trade, kill switch, restart) |
| restore drill | pass (duration …) |
| invariant suite | pass (ledger drift 0) |

## Known limitations (never omit)

- …

## Artifacts

| Image | Digest |
|---|---|
| api | `sha256:…` |
| engine | `sha256:…` |
| executor | `sha256:…` |
| ingest | `sha256:…` |
| web | `sha256:…` |

## Reporting

Security issues: `SECURITY.md`, never a public issue.
