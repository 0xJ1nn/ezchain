# Backup and restore rehearsal — measured recovery time

Measured 2026-11-14 on branch `ops/backup-restore-rehearsal` at commit `756bc80`, based on
`main` at the same commit. Every number below is the output of the command shown next to
it. Nothing is inferred, and no run is quoted from an earlier session.

This covers the release-gate item (issue #232):

- scripted, idempotent backup and restore in `tools/ops/`;
- a restore into a **fresh** database that reproduces the ledger row-for-row;
- a measured recovery time, from "the database is gone" to "a verified copy is serving";
- a runbook section for a restore that comes back short.

The dump/restore ran against a **real Postgres 16 server**, not a mock. Docker Compose was
not exercised: the authoring host has no Docker daemon (see "Not verified" below).

## Environment

macOS 27.2.1 (Darwin, arm64), Apple M3, 8 cores, 8 GB RAM. PostgreSQL 16.15 (Homebrew) on
`127.0.0.1:5432`, `max_connections=100`, no password (peer/trust local role `0xj1n`).
Python 3.11.18 in `.venv`, `pg_dump`/`pg_restore` 16.15 on `PATH`. `main` at
`756bc80c57913c0d6169f334f2aef2854d932111`.

## What the rehearsal does

`python -m tools.ops rehearse` runs the incident, not a happy path. Each phase is timed
separately:

1. create a disposable source database (`…_source_<stamp>_test`) and `alembic upgrade head`;
2. fill it through the **real order path** (`ezchain_api.services.paper.place_order`):
   two buys and an add on token A, two buys on token B, a partial reduce-only exit on
   each, a full exit on A, and one order replayed by its idempotency key. Only the market
   read is a deterministic in-process feed, so no venue and no network are involved;
3. `pg_dump` with an exported snapshot, and record a per-table row digest **from the same
   snapshot**, so the manifest describes exactly the rows in the dump;
4. **drop the source database** — this is the corruption the drill must survive;
5. restore into a different, newly created database, then require every ledger table to
   match the manifest row-for-row and the API's reconciliation report to be `ok: true`.

Step 5 is the measured recovery. Step 4 is instantaneous by construction, so the number
answers the operator's question after a loss.

## Measured runs

Five consecutive runs on an otherwise idle host, same commit:

```bash
python -m tools.ops rehearse \
  --admin-database "postgresql+psycopg://0xj1n@127.0.0.1:5432/postgres" \
  --dest /tmp/ez215-final3/run<N> --report /tmp/ez215-final3/run<N>/report.json
# exit=0 per run
```

| Run | Recovery | Backup | Dump bytes | Dump SHA-256 |
|---|---|---|---|---|
| 1 | 7.444 s | 1.493 s | 51 800 | `23c7787a…a2d390` |
| 2 | **0.539 s** | 1.021 s | 51 785 | `4c236a8c…b964ad1` |
| 3 | 1.238 s | 1.454 s | 51 773 | `c1589840…f1ff82` |
| 4 | 0.693 s | 0.956 s | 51 778 | `89b0531c…d52e0d970` |
| 5 | 1.638 s | 0.992 s | 51 754 | `f361e685…e2d390` |

**Recovery time on an idle host: 0.54 s to 7.44 s, median 1.24 s** for this dataset.

The spread is not a property of the tool. Three earlier runs taken while the full test
suite was running on the same laptop (8 GB, shared with pytest and a browser suite) took
**16.93 s, 3.09 s and 11.63 s**. The correct planning number is the slowest observed under
contention, **16.9 s**, not the median. A rehearsal quoted as a recovery target should be
run on the hardware and under the load the target describes.

Every run reported `ok: true` and reproduced identical row counts:

| Table | Rows |
|---|---|
| `ledger_entries` | 33 |
| `paper_orders` | 7 |
| `paper_fills` | 7 |
| `paper_wallets` | 26 |
| `market_snapshots` | 2 |
| `alembic_version` | 1 (`0022_strategy_creator`) |

The seed produced 7 orders, 7 fills and 1 idempotency replay in every run. The restored
database's reconciliation report, computed by the API's own service against the restored
rows, was identical in all eight runs (five idle, three under load):

```
11 checks pass
cash_usd 9847.09  genesis_usd 10000  fills 7  filled_orders 7  open_positions 1
orphan_fills 0  filled_orders_without_single_fill 0  negative_cash 0  negative_positions 0
fills_cash_usd 9847.09  quote_kinds {"paper_mark": 7}
```

That cash figure and the fill count are the identity the issue asks for: the restored
ledger projects the same balances and positions as the source, because they are the same
rows under the same journal. The dump SHA-256 differs per run because each run builds a
new source database with new row ids; the row counts and the reconciliation are the stable
evidence.

### Row-for-row, not just counts

`restore` compares a per-table digest over every row in canonical order (`md5` of
`string_agg(quote_nullable(x::text), … ORDER BY <key>)`), so equal counts with different
rows fail. Two details make the comparison sound:

- both sides pin the session to UTC, because a `timestamptz` renders in the session's
  zone and the composite text is what gets hashed (the first run of this tool failed on
  exactly that and was fixed — see "Defects found");
- the dump and its fingerprint come from **one exported snapshot**, so a concurrent write
  cannot make the manifest describe rows the dump does not contain.

## Failure path

The acceptance criteria require the failure path to be tested, and a drill that only
passes is not evidence. Two failure modes were exercised against the real server.

### A short restore is detected

Restore a good dump into a disposable database, delete one fill (a truncated load with the
same schema), then verify:

```bash
python -m tools.ops restore --database "$DEMO" --manifest "$MANIFEST"
# restore: ok — 33 ledger entries reproduced

psql "$DEMO" -Atc "delete from paper_fills where id=(select id from paper_fills order by id limit 1);"
# DELETE 1

python -m tools.ops verify --database "$DEMO" --manifest "$MANIFEST"
# differences: ["paper_fills: 7 row(s) in source, 6 in restore"]
# matches: False
# exit=1
```

The row count is named, not a vague failure. The same test exists as
`tests/ops/test_rehearsal.py::test_a_restore_that_is_short_is_reported_as_a_failed_drill`.

### A dump that changed after the backup is refused

```bash
cp "$DUMP" /tmp/altered.dump && printf 'x' >> /tmp/altered.dump
python -m tools.ops restore --database "$DEMO" --manifest "$MANIFEST" --dump /tmp/altered.dump
# ops: dump /tmp/altered.dump does not match its manifest:
#   sha256 04abab30… != 13c066b4…
# exit=2
```

### A live-looking target is refused before anything is dropped

```bash
python -m tools.ops restore --database "postgresql+psycopg://0xj1n@127.0.0.1:5432/ezchain" \
  --manifest "$MANIFEST"
# ops: refusing to drop or overwrite postgresql://127.0.0.1:5432/ezchain:
#   the database name must contain test, scratch or _ci
# exit=2
```

### A stale dump is never returned for a changed source

The first implementation reused an existing dump whenever its file hash matched the
manifest. That is wrong in the one case that matters: the source moved on, so the
"existing backup" no longer covers it. Reuse now also requires the source's current
digests to match the manifest; otherwise a new dump is taken. This is covered by
`test_a_changed_source_is_never_answered_with_a_stale_dump`.

## Defects found and fixed during this work

1. **Timezone-dependent digests.** The first rehearsal reported equal row counts and
   different digests for every non-empty table, because the backup connection and the
   verify connection disagreed about the session time zone. Fixed by pinning UTC in the
   digest function itself, so no caller can forget it. Before the fix: 5 differences;
   after: 0.
2. **A false reuse.** As above — reuse keyed on the dump hash alone could return a stale
   dump for a source that had changed.
3. **A replayed-order miscount.** The seeder counted replays from a `contextvars` flag that
   does not propagate out of `asyncio.run`, so it reported 8 replays for 7 orders. It now
   counts distinct order ids, which is observed evidence, and asserts the count matches
   the stored rows.

## Reproduction

```bash
# the drill, one run per iteration
for i in 1 2 3 4 5; do
  python -m tools.ops rehearse \
    --admin-database "postgresql+psycopg://0xj1n@127.0.0.1:5432/postgres" \
    --dest /tmp/ez215-final3/run$i --report /tmp/ez215-final3/run$i/report.json
done
```

The rehearsal creates and drops its own databases; it never touches a configured
application database, and every target name must contain `test`, `scratch` or `_ci`.

## Not verified

- **Docker Compose.** The host has no Docker daemon, so the Compose path in
  [`docs/INFRA.md`](../../INFRA.md#7-storage-backup-and-restore) was neither run nor
  changed. The tooling uses the host `pg_dump`/`pg_restore` binaries against any reachable
  Postgres; inside the stack those binaries live in the `postgres` container.
- **The installed self-hosting layout** (`$HOME/ezchain`) was not exercised; this drill
  used a local Postgres 16 directly.
- **A multi-gigabyte production dump.** These runs used a 51 KB dump with 33 ledger rows.
  Recovery time scales with dump size and index rebuild, and this number does not predict a
  large one. Re-run `rehearse` on representative data before quoting a recovery target for
  a real deployment.
- **Point-in-time recovery / WAL archiving** is out of scope: the tool restores a dump, not
  a transaction log.
