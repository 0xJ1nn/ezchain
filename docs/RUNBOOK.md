# RUNBOOK — incidents and recovery

**PAPER stack (implemented):** §12 contains the commands that work today: health checks,
migrate/upgrade/rollback, 401 debugging and backup/restore.

Sections 1–11 describe the target live system. They use the `ezchain-admin` interface,
which does not exist yet; they are the contract, not a working binary. Every action there
is audited once the executor and audit log exist.

## 0. First five minutes

1. Open **Operations** (`25-operations.html` in the wireframe) and note which service is red.
2. Check the **kill switch** state. If unsure, arm it: it halts new risk, never sells.
3. Read the latest `AuditEvent` and `Alert` rows. Do not restart anything until you know
   whether an order is in flight.
4. Preserve evidence: `ezchain-admin support-bundle` (logs, config redacted, metrics).
5. Announce in the incident log: start time, suspected scope, owner.

**Do not:** delete the DB, truncate tables, rotate the vault key, or "retry until it fills".

## 1. Ledger drift

Symptom: Operations shows `ledger_drift_entries > 0`, or a wallet balance disagrees with
the chain after reconciliation.

1. Arm the kill switch (scope: all).
2. Export the affected accounts: `ezchain-admin ledger export --account <id> --since 7d`.
3. Compare chain state at a known block/slot with the last reconciled entry.
4. If the difference is a missing fill, create a reconciliation entry with
   `reason=chain_reconcile` and link the transaction. Never edit an existing entry.
5. If the difference cannot be explained, stop. Restore the last verified dump into a
   scratch instance and replay the outbox. Escalate with the bundle.

**Exit criteria:** drift = 0, reconciliation entry reviewed by a second person, kill switch
released with a recorded reason.

## 2. Executor down or unreachable

1. New intents fail closed; confirm the API reports `executor_unreachable`, not a generic 500.
2. Check the last submitted order. If a tx hash exists, poll the chain before retrying.
3. Restart only the executor: `ezchain-admin restart executor`.
4. On boot it reconciles open orders; wait for `executor_ready` before disarming the kill switch.
5. If the vault cannot unlock, **do not** recreate keys. Restore the vault from its sealed
   backup and the recovery phrase.

## 3. Chain degraded / stale source

1. The chain page shows the source and its last event age.
2. If one RPC provider is degraded, the adapter rotates; no action.
3. If all providers are stale, trading for that chain is disabled automatically. Confirm
   the breaker is open and the UI shows `degraded`.
4. Never force a mark from a stale source to "unblock" a position. Stale is a valid state.

## 4. Quote mismatch, expired quote, or route failure

1. No order should be sent. Verify `order.state` is `rejected`/`expired`, not `submitted`.
2. Check the quote envelope: buy, full-size sell, route identity, divergence, rent.
3. If a partial fill occurred, reconcile fills before retrying; the retry uses a new intent id.
4. Report the venue/route in the incident log; repeat failures open the breaker.

## 5. Unsellable position

1. Confirm the state is `unsellable` and the last exact quote is displayed.
2. Do not write off to make the UI look clean. A write-off is a paper-only accounting act.
3. Try smaller size / alternate route via the position menu; each attempt is recorded.
4. If the token is confirmed dead, write off with a reason and keep the position's history.

## 6. Wallet unsynced or balance mismatch

1. Pause the wallet; new intents from it are blocked.
2. Re-run sync: `ezchain-admin wallet sync <id> --chain <chain>`.
3. If the mismatch persists, create a reconciliation entry, alert, and review.
4. For a compromised wallet: revoke (do not delete), rotate to a new wallet, sweep what is
   safe **only** after confirming the destination.

## 7. Key compromise

Assume funds are at risk; speed over elegance.

1. Arm the global kill switch and stop the executor.
2. Move remaining assets with a known-good device/wallet (hardware preferred).
3. Revoke API keys, sessions, and the vault's master key reference.
4. Preserve logs and the audit trail; do not wipe the host.
5. Rotate every credential the host could read (RPC, CEX, webhooks).
6. Publish a post-mortem within 7 days; add a failure-ledger entry.

## 8. Restore drill / disaster recovery

The PAPER stack has a scripted, idempotent drill. Run it after any change to the schema, a
long-running deployment, or before an upgrade you cannot reverse — a backup nobody has
restored is not a backup. §12.4 documents the commands; this section is the procedure and
what to do when it fails.

1. Provision a clean host and the same image digests.
2. `python -m tools.ops backup --database "$EZCHAIN_DATABASE_URL" --dest backups/` — this
   writes `<label>.dump` and `<label>.manifest.json`. The manifest is the evidence: the
   dump's SHA-256 plus a row digest of the ledger at the same exported snapshot.
3. `python -m tools.ops rehearse --admin-database "$ADMIN_URL" --dest backups/<drill> --report backups/<drill>/report.json`
   on a machine with the database server. It builds a source database, fills it through the
   real order path, backs it up, **drops the source**, restores into a second fresh
   database, verifies, and records the elapsed recovery time.
4. Restore into a scratch database and verify before replacing anything live
   (`python -m tools.ops restore --database "$SCRATCH_URL" --manifest …`).
5. Start the API against the verified restore in `read-only`, confirm
   `GET /api/v1/paper/reconciliation` is `ok: true`, then enable paper.
6. Record the drill: date, dump SHA-256, recovery seconds, reconciliation result, operator.
   The 2026-11-14 run is in
   [docs/evidence/release-gate/backup-restore-rehearsal.md](evidence/release-gate/backup-restore-rehearsal.md).

A restore that cannot reproduce the ledger to the last entry is a failed drill.

### 8.1 When the restore is short

A "short" restore is a load that completes but does not reproduce the ledger: fewer rows,
the same count with different rows, or reconciliation failing. The tool names which of
those it is; do not proceed past a difference.

```bash
python -m tools.ops restore --database "$SCRATCH_URL" --manifest backups/<label>.manifest.json
# restore: FAILED — 2 difference(s)
#   - paper_fills: 7 row(s) in source, 5 in restore
#   - reconciliation: equity.identity: ledger equity … != genesis + fill effects …
# exit 1
```

Read the difference before doing anything else:

| Difference | Meaning | Next action |
|---|---|---|
| `N row(s) in source, M in restore` | the load lost rows | do **not** promote it. Re-restore from an older dump whose digest verifies. If the dump itself is bad, the last good dump is the recovery point — reconcile the gap from `paper_orders`/`paper_fills` evidence rather than hand-editing |
| `same row count (N) but different rows` | rows changed or were replaced | treat as a different database, not a restore: restore again and re-run `verify` before using it |
| `missing in restore` | a table did not load (often a schema the dump's migration did not create) | run `ezchain-api migrate` against the restored database, then `python -m tools.ops verify` |
| `schema: restored database is at …, backup was …` | the restored schema is behind the dump | run `ezchain-api migrate` forward. Never stamp or downgrade `alembic_version` to make a dump fit |
| `reconciliation: <check>` | rows loaded but the ledger does not balance | the dump is inconsistent. Restore an earlier dump; if none reconciles, stop and escalate with the manifest and the failing check |
| `dump … does not match its manifest` | the dump file changed after the backup | discard it and take a new backup. Never restore a dump you cannot hash to its manifest |

After a second failed attempt on the same dump, stop. Keep the dump, the manifest and the
`--json` output as incident evidence, and restore the newest dump that verifies. A restore
you cannot verify is not a recovery point.

## 9. Upgrade and rollback

1. `ezchain-admin backup` and verify the dump.
2. Pull pinned image digests; run `migrate` (forward-only).
3. Start, run `ezchain-admin verify`, reconcile, then unpause.
4. Rollback = redeploy the previous digests. Schema rollback is an incident, not a routine
   step; migrations must remain backward-compatible for one minor release.

## 10. Kill switch scope

| Scope | Stops | Does not stop |
|---|---|---|
| Global | all new intents, signing | exits you trigger manually |
| Chain | new intents on that chain | other chains |
| Wallet | new intents from that wallet | watching, alerts |
| Strategy | new intents from that strategy | closing existing positions |

Arming is immediate and audited. Disarming requires a reason and, in live mode, 2FA.

### 10.1 Paper operator switch (implemented, P0)

The paper slice implements the **global** and **strategy** levels over one flag. Global is
the widest: an armed switch refuses every new risk-increasing order, whoever places it
(the REST route, the MCP server, the web ticket or a strategy). Strategy is the narrower
second level: the engine still refuses strategy-attributed intents with its own
`KILL_SWITCH_ACTIVE` hold. The state is a row in `system_flags` keyed
`engine_kill_switch`, seeded from `EZCHAIN_ENGINE_KILL_SWITCH` on first boot, so it
survives a restart and is read by every process.

```bash
# Through the edge: Caddy routes /api/* to the Next route handler, which adds the
# bearer token server-side. No token in the browser or in this command.
curl -s http://localhost:8080/api/v1/system/kill-switch

curl -s -X POST http://localhost:8080/api/v1/system/kill-switch \
  -H 'content-type: application/json' \
  -d '{"active": true, "reason": "venue incident"}'
```

- `POST` returns `{active, reason, updated_at, scope: "global", scopes: ["global", "strategy"]}`.
  `scope` is the widest level in force; `scopes` lists both levels the one flag governs. `updated_at` is the
  audit timestamp. Mutating routes require `Authorization: Bearer $EZCHAIN_API_TOKEN`
  (or reach the API through the web server's proxy, which injects it); the token
  identifies the deployment, not an individual operator, so record the human in the
  incident log. Releasing (`active: false`) requires a non-empty reason and returns 422
  otherwise; arming may omit it.
- Two processes may boot together; the seed insert is idempotent and the loser of the
  race reads the winner's row instead of failing startup.
- The engine checks the flag on every strategy-originated order, and an armed switch makes
  the scheduler skip its pass before any market read. When a manual run is attempted, the
  run log records `hold` / `KILL_SWITCH_ACTIVE` with the attempted action and amount, and
  the strategy's interval window is **not** consumed — releasing the switch retries on the
  next tick.
- The executor pipeline and the paper order service both treat the switch as an
  **entry-only** gate. Every read is buy-only; a sell is bounded by the held quantity and
  cannot open a short, so an armed switch refuses a new entry and still settles an exit,
  stop or reduce-only sell. Blocking an exit would trap funds. Existing positions are not
  sold by the switch; close them deliberately.
- The executor reads the switch before any venue contact, again after the quote, a third
  time after the transaction is built and immediately before the signer is called, and a
  fourth time before the submit. The sign-time read is what makes the switch bind at the
  key: building the transaction awaits, so an arm can land between the quote and the
  signature, and that read refuses before the key is used rather than discarding a
  signature already produced. An entry that meets an armed switch is refused
  `KILL_SWITCH_ACTIVE` (or `KILL_SWITCH_UNAVAILABLE` when the state cannot be read) and
  nothing is signed and nothing is submitted. The paper order service reads the switch
  before the market read and the quote (refusal reason `KILL_SWITCH_ARMED`), and again
  inside the fill window after the quote, so a buy that was already in flight when the
  operator armed is refused too: it keeps the rejected row and its quote but books no fill
  and no journal entry.
- An unreadable switch is not a released switch: both paths refuse an entry with the typed
  `KILL_SWITCH_UNAVAILABLE` instead of raising out of the order path, and neither contacts
  the venue for new risk.
- Each read crosses the process boundary; `read_kill_switch` forces the SELECT past the
  session identity map, so a value read earlier in the session is never reused. An arm is
  therefore obeyed by the same intent when it lands before one of the reads above, and in
  the worst case by the next tick: obedience is at most one tick old.
- A signature already sent is not recalled. Once the signed submit has left for the venue,
  a later arm cannot unwind it; the fill is journaled from the receipt. The switch prevents
  new signatures, not orders already in flight.
- A refusal persists the rejected order row (so the attempt stays replayable) but writes no
  fill and no journal entry.
- Before disarming, read `GET /api/v1/paper/reconciliation` and confirm `ok: true`.

### 10.2 The gate order for a paper order

Every gate is a gate *on* `place_order`, and they compose in one order. Knowing it is what
lets you tell an incident reviewer which gate refused an order, because the order row
persists the reason code of the first gate that refused it.

1. Idempotency replay and integrity (side, amount) — no network, no second fill.
2. The kill switch, **entry-only**, before the market read (`KILL_SWITCH_ARMED` /
   `KILL_SWITCH_UNAVAILABLE`); a sell is never refused.
3. The settlement wallet, entry-only (`WALLET_UNKNOWN` / `SETTLEMENT_ASSET_UNKNOWN`).
4. The trade-policy profile, resolved once from the strategy row and the request
   (`resolve_order_profile`): `PROFILE_UNKNOWN` for an undefined name,
   `PROFILE_LOOSENS_STRATEGY` when an entry asks for a looser profile than its strategy
   is pinned to (a sell is exempt).
5. The market read and the quote; a stale mark refuses an entry (`SNAPSHOT_STALE`), a
   reduce-only exit sizes from the held quantity and still settles.
6. Inside the fill window: the kill switch is re-read (entry-only), then the position
   replay and the quote-size checks.
4. The agent's bucket, entry-only (`AGENT_BUCKET_EXCEEDED` / `AGENT_BUCKET_UNREADABLE`): an
   agent may deploy only the net cash its owner capped for it. A cap lowered below what is
   already deployed stops new buys and never refuses a sell.
5. The market read and the quote; a stale mark refuses an entry (`SNAPSHOT_STALE`), a
   reduce-only exit sizes from the held quantity and still settles.
6. Inside the fill window: the kill switch is re-read (entry-only), the bucket is re-checked
   at the quoted notional plus fee (entry-only), then the position replay and the quote-size
   checks.
7. The risk decision.
8. The automation tier / approval queue: an agent order, and a strategy order whose
   strategy an agent created (or whose provenance is unknown), is stored as
   `pending_approval` and queued with the server's own evidence under `manual` — nothing
   is journaled; an unreadable queue refuses `APPROVAL_STATE_UNREADABLE`, and a
   `full_auto` whose consent record is missing refuses `AUTOMATION_CONSENT_REQUIRED`.
   The tier belongs to the ordering principal's owner, not the workspace. A strategy a
   person created is not gated.
9. Fill and journal.

A person's approval re-enters the same function with the stored intent, so steps 2–7 and 9
run again (the trade keeps its id and idempotency key) and step 8 is skipped. Consent for
`full_auto` is asked once, at `PUT /api/v1/paper/automation`; it is never re-asked at
placement. The executor's live path refuses an unattended intent whose
`AutomationSetting` still requires approval with `AUTOMATION_CONSENT_REQUIRED`, so a
manual-tier intent cannot reach a venue by going around the queue.

## 11. Market monitor (market eyes)

The API observes every registered chain continuously when
`EZCHAIN_MARKET_SCAN_INLINE=true` (default). One scan makes a single `chain=all`
discovery call, uses its per-chain coverage, and persists every row to
`market_snapshots` before it is served. A chain the fan-out already attempted is not
re-probed; a failed all-chain call falls back to one probe per chain. The adapter fan-out issues one token lookup per
registered chain (seeds first, four in flight) and round-robins ranked rows before the
page limit. After the chain scan it refreshes one mark per open paper position (bounded by
`max_open_positions`), including quote-side tokens. The scan interval is
`EZCHAIN_MARKET_SCAN_INTERVAL_SECONDS` (default 90, minimum 30) and the per-call row
budget is `EZCHAIN_MARKET_SCAN_LIMIT` (default 200).

Symptom: `/api/v1/markets/overview` reports a chain as `no_data`, `stale` or `error`, or
`/api/v1/markets` looks empty for a chain that used to work.

1. Read `/api/v1/markets/overview`. Each chain carries `status`, `rows`,
   `last_observed_at`, `age_seconds`, `last_attempt_at` and `last_error`.
2. `error`: read `last_error`; a `RateLimited` message means the shared adapter cooldown
   is active. Do not restart in a loop — wait for `last_attempt_at` to advance.
3. `stale`: rows exist but the newest observation is older than
   `max(60s, scan interval + last scan duration + 15s)`. Check that scans are still
   succeeding and that the provider is returning rows for that chain.
4. `no_data`: the scan succeeded and returned no pairs. Some chains are not indexed by
   DexScreener every day. A seed without a real pair is not a price; do not add one.
5. Verify persistence directly:
   `curl 'localhost:8000/api/v1/markets/stored?chain=<id>&limit=5'`. Every money field is
   a decimal string, null means unknown, and `source`/`observed_at` come from the row.
6. To switch observation off for a process, set `EZCHAIN_MARKET_SCAN_INLINE=false` and
   restart the API. The existing `/markets` request path keeps working; the overview
   then shows only DB-derived rows and statuses.
7. Never edit `market_snapshots` to make a chain look covered. A missing or stale row is
   a valid state and a monitoring fact.

## 12. PAPER stack operations (implemented)

The commands below target `infra/compose.yaml`. They assume `infra/.env` exists and that
you are in the repository root. Nothing here needs a published API port: the edge on
`localhost:8080` is the only host entry point. For a server you own, start from
[docs/SELF-HOSTING.md](SELF-HOSTING.md); the installed layout lives under
`$HOME/ezchain` and uses `-p ezchain -f $HOME/ezchain/current/infra/compose.yaml`.

### 12.1 Health and readiness

```bash
docker compose -f infra/compose.yaml ps
curl -fsS http://localhost:8080/healthz
curl -fsS http://localhost:8080/readyz
# /metrics is operator-only and is not published by the edge.
docker compose -f infra/compose.yaml exec -T api python - <<'PY' | head
import os, urllib.request
request = urllib.request.Request(
    "http://127.0.0.1:8000/metrics",
    headers={"Authorization": "Bearer " + os.environ["EZCHAIN_API_TOKEN"]},
)
print(urllib.request.urlopen(request).read().decode())
PY
docker compose -f infra/compose.yaml logs --tail=100 api
```

- `/healthz` is liveness plus the server-derived execution mode (`mode.execution`,
  `live_available`, `live_blocked_reason`). A 200 means the API process is serving HTTP.
  It never reads the database, so it must not be used to decide whether the database is
  ready. A refused live start reports `paper`, and the public reason omits the gate
  record's path.
- `/readyz` is readiness. A non-200 means the API cannot serve paper traffic (usually
  Postgres is down or migrations have not run). Check
  `docker compose -f infra/compose.yaml ps postgres` and the `migrate` logs.
- `/metrics` is Prometheus text format. It is served by the API, not by the web handler.
  A non-loopback scrape needs the operator token; see §12.6.4.
- A container marked `unhealthy` in `ps` has failed its own healthcheck. Read
  `docker compose -f infra/compose.yaml logs <service>` before restarting anything.

### 12.2 401 Unauthorized from `/api/*`

The browser never holds the API token. Caddy sends `/api/*` to the Next.js route handler,
which adds `Authorization: Bearer $EZCHAIN_API_TOKEN` server-side. A 401 almost always
means the API and web containers disagree about the token, or a request bypassed the edge.

1. Confirm `EZCHAIN_API_TOKEN` is set in `infra/.env` and is at least 32 characters.
2. Compare the value the two containers actually received **without printing it**:

   ```bash
   docker compose -f infra/compose.yaml exec -T web sh -c \
     'printf %s "$EZCHAIN_API_TOKEN" | sha256sum'
   docker compose -f infra/compose.yaml exec -T api sh -c \
     'printf %s "$EZCHAIN_API_TOKEN" | sha256sum'
   ```

   The digests must match. If they do not, the containers were created from different
   `.env` values.
3. Recreate both after editing `.env`:

   ```bash
   docker compose -f infra/compose.yaml up -d --force-recreate api web
   ```

4. Confirm the request went through the edge (`http://localhost:8080/api/...`), not to a
   direct API port. The API is not published.
5. If you are debugging the API directly, send the header from inside the container (the
   API image has Python, not curl):

   ```bash
   docker compose -f infra/compose.yaml exec -T api python - <<'PY'
   import os, urllib.request
   req = urllib.request.Request(
       "http://127.0.0.1:8000/api/v1/chains",
       headers={"Authorization": "Bearer " + os.environ["EZCHAIN_API_TOKEN"]},
   )
   print(urllib.request.urlopen(req).status)
   PY
   ```

6. Never add the token to browser code, `NEXT_PUBLIC_*`, screenshots or logs. If a token
   leaks, rotate it in `infra/.env` and recreate `api` and `web`; with the
   observability profile running, recreate `prometheus` too so its token file is
   rewritten.

### 12.3 Migrate, upgrade and rollback

```bash
# Run the one-shot migration and inspect the exit status.
docker compose -f infra/compose.yaml up -d migrate
docker compose -f infra/compose.yaml ps -a migrate
docker compose -f infra/compose.yaml logs migrate

# Rebuild and roll the stack forward. api starts only if migrate exits 0.
docker compose -f infra/compose.yaml up -d --build

# A failed migration leaves the old api container running. Fix the migration
# forward; do not edit an applied revision or run a down-migration.
```

Rollback is redeploying the previous image tag:

```bash
EZCHAIN_VERSION=<previous-tag> docker compose -f infra/compose.yaml up -d --no-build api web caddy
```

If the previous tag is not present locally, rebuild it from the previous commit. Schema
rollback is an incident, not a routine command. A forward-only migration must remain
backward-compatible for one minor release.

### 12.4 Backup and restore

Prefer the scripted path, which records the fingerprint of the ledger it dumped and can
verify a restore against it:

```bash
# dump + manifest (idempotent: a second run reuses the verified dump)
python -m tools.ops backup --database "$EZCHAIN_DATABASE_URL" --dest backups/
# load into a FRESH disposable database and verify (drops + recreates the target)
python -m tools.ops restore --database "$SCRATCH_URL" \
  --manifest backups/<label>.manifest.json
# compare an existing database to a manifest without touching it
python -m tools.ops verify --database "$SCRATCH_URL" \
  --manifest backups/<label>.manifest.json
```

Exit codes: `0` verified, `1` a verification failed (the differences are printed), `2`
refused or an external tool failed. A target database name must contain `test`, `scratch`
or `_ci`, the same rule `tools/check-migrations.sh` applies; the command refuses anything
else before it drops a database. The practitioner procedure and the failure table are
[§8](#8-restore-drill--disaster-recovery).

The raw `pg_dump`/`pg_restore` commands in
[`docs/INFRA.md`](INFRA.md#7-storage-backup-and-restore) remain valid for a hand drill, but
they do not write a manifest, so nothing can verify the result against the dump. If you
use them, still restore into `ezchain_restore` first and check
`GET /api/v1/paper/reconciliation` before replacing the live database.

`docker compose down` keeps named volumes. `docker compose down -v` deletes `pgdata` and
is a destructive data reset: take a verified dump first, and record the action.

### 12.5 Logs, restart and profiles

```bash
docker compose -f infra/compose.yaml logs -f api
docker compose -f infra/compose.yaml restart api
docker compose -f infra/compose.yaml --profile observability up -d prometheus
```

Do not restart the stack in a loop while an order may be in flight; read the paper order
and fill rows first. Strategy execution is blocked by the persisted kill switch (§10.1),
which survives a restart.

## 12.6 Deploy migrations, auth and probes

### 12.6.1 Migrate on deploy

The `migrate` service runs before the API starts. Manually it is:

```bash
ezchain-api migrate                        # packaged entry point
python -m ezchain_api.migrate              # same runner without the script
alembic -c apps/api/alembic.ini upgrade head
```

- The URL comes from `EZCHAIN_DATABASE_URL`; never from `alembic.ini`.
- The ini is normally found at `apps/api/alembic.ini`. In a packaged image that places it
  elsewhere, set `EZCHAIN_ALEMBIC_INI` to the absolute path.
- Migrations are forward-only (CONTRACT I8). `downgrade()` exists for local recovery,
  but production rollback is redeploying the previous image, not a schema downgrade.
- A failed migration stops the rollout. Restore the pre-migration backup before retrying;
  do not edit `alembic_version` by hand to "unblock".
- Fresh-DB gate: `EZCHAIN_TEST_DATABASE_URL=postgresql+psycopg://.../ezchain_test \
  bash tools/check-migrations.sh` runs `upgrade head` and `alembic check`. `tools/ci.sh`
  runs it when that variable is set; the script refuses a URL whose database name does
  not contain `test`, `scratch` or `_ci`.
- Legacy dev database created by `create_all()` before Alembic: `/readyz` returns 503 with
  `migration: missing`. After confirming the schema matches the models, run
  `alembic -c apps/api/alembic.ini stamp 0001_baseline` **once**, then run
  `alembic -c apps/api/alembic.ini upgrade head` so the forward-only 0002 genesis index
  is applied. Never stamp a production database; it hides real drift.

### 12.6.2 Health vs readiness vs metrics

| Endpoint | Question | Failure |
|---|---|---|
| `/healthz` | is the process alive, and what is the server-derived mode? | always 200; no DB; reports `paper` when a live start is refused |
| `/readyz` | can it serve writes with the right schema? | 503 with JSON when DB is down, schema missing or not at head |
| `/health` | legacy compatibility probe | 503 when DB is down; includes scheduler/monitor flags |
| `/metrics` | Prometheus text | 200; `ezchain_up 0` when DB is unreachable |

Use `/healthz` for restart policy, `/readyz` for load-balancer membership and deploy
gates, and `/metrics` for alerting. A 200 `/healthz` with a 503 `/readyz` means the
process is running but must not receive work.

### 12.6.2.1 Operational metric families

Every family below is sampled from live state on each scrape; none is a constant.

| Metric | Labels | Read it as |
|---|---|---|
| `ezchain_source_freshness_seconds` | `source`, `source_class` | age of the newest persisted observation; `NaN` means the age cannot be established (future-dated) |
| `ezchain_source_freshness_budget_seconds` | `source_class` | the configured budget the age is compared with |
| `ezchain_breaker_state` | `provider`, `state` | `0` closed, `1` half-open, `2` open |
| `ezchain_queue_depth` | `queue` | waiting callers on `provider:<kind>`, unpublished rows on `market_outbox` |
| `ezchain_queue_capacity` | `queue` | the configured `max_queue` of a bounded provider queue; the outbox is unbounded and has none |
| `ezchain_ledger_drift_entries` | — | failing reconciliation checks right now |

`source_class` covers `mark` and `candle` here: those are the classes with a persisted
observation table. `quote` and `feed` have budgets but no stored observation, so they are
absent rather than reported at an invented age. When the database cannot be read, the four
families are omitted and `ezchain_up 0` explains why — an unreachable database is not
"zero drift, empty queue". `infra/prometheus/alerts.yml` alerts on each family and on its
absence; a `NaN` age is its own alert (`SourceFreshnessUnknown`, matched with `x != x`)
because it compares false against the budget and does not count as absent.

### 12.6.3 401 and 403 triage

Symptom: a web mutation returns `401` with `detail.code == "UNAUTHORIZED"`, while pages
read normally.

1. Reads (`GET`/`HEAD`/`OPTIONS`) are open by design, except
   `/api/v1/agent/state`, which always needs a principal; mutations are protected.
2. The browser should call the Next server at `/api/...`, never the API directly. The
   proxy drops any browser `Authorization` header and injects `EZCHAIN_API_TOKEN`.
3. Confirm the **web** process has `EZCHAIN_API_TOKEN` set; restart it after changing it.
4. Confirm the **API** process has the same token. In prod it must be at least 32 chars or
   the API refuses to boot with `EZCHAIN_API_TOKEN` in the error.
5. Do not paste the token into browser code, a query string or a support bundle. Rotate it
   if it was exposed: change both processes, restart, then re-test a mutation.
6. In local dev only, an unset token allows mutations with one warning. That is not a
   production mode.

`403 CROSS_ORIGIN_DENIED` means the Next proxy rejected a browser request whose
`Origin`/`Sec-Fetch-Site` did not match the edge. Set `EZCHAIN_PUBLIC_URL` to the exact
public origin (for example `https://ezchain.example.com`) in the web container; the
proxy also accepts the forwarded host/proto. A non-browser client with no Origin header
is still allowed, so this only affects browser CSRF attempts and misconfigured origins.

### 12.6.4 Operational endpoint boundary

| Endpoint | Class | Rule |
|---|---|---|
| `/healthz` | public-safe | Liveness and the derived execution mode; never reads the database, never returns the gate record's path. |
| `/readyz` | public-safe | Database and migration status; no ledger data. |
| `/health` | operator | Loopback, or `Authorization: Bearer $EZCHAIN_API_TOKEN`. |
| `/metrics` | operator | Loopback, or `Authorization: Bearer $EZCHAIN_API_TOKEN`. |
| `/docs`, `/redoc`, `/openapi.json` | loopback-only | 404 to every other client. |
| `/api/v1/agent/state` | authenticated | A principal with the `read` scope, even when reads are open. |
| `/api/v1/agent/keys` | authenticated | A principal with the `admin` scope. |

A remote request for an operator endpoint without the token returns 401 with
`detail.code == "UNAUTHORIZED"`. The loopback test uses the TCP peer address.
`X-Forwarded-For` is read only from a loopback peer or from a peer listed in
`EZCHAIN_TRUSTED_PROXY_CIDRS` (a JSON list, empty by default); the rightmost address that
is not a loopback or trusted proxy hop is the client. A remote client cannot make itself
loopback with a header, and a same-host proxy forwarding a public client stays remote.
A reverse proxy that forwards to the API must set `X-Forwarded-For`: without the header,
a same-host proxy is indistinguishable from a local client.

Set `EZCHAIN_TRUSTED_PROXY_CIDRS` only when a reverse proxy sits in front and the API
cannot see the client directly. List the proxy's own address or network, never a client
network: a host that can reach the API directly from a trusted network could otherwise
claim a loopback address. Leave it empty when the API port is reachable from the network
without a proxy.

Metrics consumers outside the host send the same bearer token. The web server adds it
server-side for `/health` and `/metrics`. The edge does not publish `/metrics`; a
Prometheus scrape goes to `api:8000` over the app network and reads the token from
`/etc/prometheus/api-token`, which Compose fills from `EZCHAIN_API_TOKEN`. Do not put the
token in `prometheus.yml`.

### 12.7 Agent keys (scoped MCP credentials)

Mint one key per agent/user with the deployment admin token:

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/agent/keys \
  -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"subject":"hermes-alice","scopes":["read","trade"],"label":"Alice desk"}'
```

The response is the only time the plaintext token is shown. Store it in the agent's
secret store, not in a prompt. List metadata with `GET /api/v1/agent/keys`; revoke with
`DELETE /api/v1/agent/keys/{id}` (204). Revocation is immediate on the next request.

- `read` — GET/HEAD; required when `EZCHAIN_REQUIRE_AUTH_FOR_READS=true`.
- `trade` — paper orders, strategies, runs, backtests. It cannot administer.
- `admin` — key management, plugin reload, kill-switch mutations. Do not give this to an agent
  unless the operator explicitly wants it to manage credentials or the switch.

Set `EZCHAIN_API_KEY_PEPPER` to >=32 random bytes before minting keys in production.
Changing the pepper invalidates every existing key; rotate by minting new keys, updating
agents, then revoking the old ones. A 401 means the key is missing, revoked or expired;
a 403 means the route needs a broader scope (usually `admin`).

Run `hermes mcp test ezchain` or `openclaw plugins inspect ezchain --json` after a key
change. Neither command should print the token.

### 12.8 Market data: retention, backup and restore

Normalised market observations live in `market_events` (one row per
`ezchain_core.MarketEvent`), keyed by the contract's own idempotency identity
`(source_name, pair, source_observed_at)`. A retried write cannot duplicate an observation.
`market_outbox` carries one message per newly written observation, committed in the same
transaction; a relay publishes it and sets `published_at`. The JSONL recording written by
`services/ingest` stays the replay input — the tables are the query path, not the replay
source.

**Retention is an operator action, never a side effect of a write.**

```bash
python -m ezchain_ingest                 # dry run: prints what would be deleted
python -m ezchain_ingest --apply         # deletes inside one transaction
```

The windows are `EZCHAIN_RETENTION_RAW_DAYS` (7–30, default 14) and
`EZCHAIN_RETENTION_CANDLE_1M_DAYS` (30–3650, default 365). A value outside the range is
refused, not clamped. Pruning ages an event by `source_observed_at` — the source's clock —
so a late report cannot keep an old observation alive. An unpublished outbox row is never
deleted: dropping it would lose a delivery for a row that committed. Only `1m` candles are
truncated by the candle window; coarser timeframes are left to their owner.

On a Postgres where `timescaledb` is installed, revision `0020_market_events` additionally
converts `market_events` into a hypertable partitioned on `source_observed_at`. That is
legal because the column is already part of the primary key. An extension that is only
*installable* is not enough: the revision checks `pg_extension` first, then tries
`CREATE EXTENSION` in a savepoint. A role that lacks the privilege is not an error — the
upgrade continues and leaves ordinary tables, exactly as on plain `postgres:16-alpine`
(what `infra/compose.yaml` ships), where the same guarantees hold. The hypertable
conversion itself has the same fall-back, so a server that refuses it still upgrades.
`market_candles` is **not** converted: its surrogate `id` primary key does not contain
`open_time`, so Timescale would refuse it and rewriting that key is a destructive change
this revision does not make. Retention for both databases comes from
`ezchain_ingest.retention`, one validated configuration, not a database policy.

**Backup.** Market evidence is in the same database as everything else, so the existing
dump covers it:

```bash
pg_dump -Fc --no-owner -f backups/ezchain-$(date -u +%Y%m%dT%H%M%SZ).dump "$EZCHAIN_DATABASE_URL"
```

Take the dump before a migration and before an `--apply` retention run. A dump of a
Timescale deployment includes the hypertable's chunks; plain `pg_dump` is enough because
nothing here uses Timescale's own compression policies.

**Restore.** Restore into a *scratch* database first and check that it reaches the schema
head before touching the live one:

```bash
createdb ezchain_restore_test
pg_restore --no-owner --clean --if-exists -d ezchain_restore_test backups/<dump>
EZCHAIN_DATABASE_URL=postgresql+psycopg://.../ezchain_restore_test ezchain-api migrate
# then confirm the restored row counts match the dump's, e.g.
psql "$SCRATCH_URL" -c 'SELECT count(*), min(source_observed_at), max(source_observed_at) FROM market_events;'
```

Do not restore over a live database. Do not edit `alembic_version` to make a restored dump
"fit" — a restored database below head is upgraded forward, which is what the migrations are
for. A retention run is not part of a restore: restore the dump, verify, then plan retention
again rather than assuming a count.

### 12.9 Compose drill

`tools/ops/drill.sh` (wrapping `python -m tools.ops drill`) starts the stack under an
isolated Compose project and proves it at the migration head of *this* checkout:

```bash
bash tools/ops/drill.sh --static-only   # compose structure + Alembic chain, no daemon
bash tools/ops/drill.sh                 # the whole drill; needs a Docker daemon
bash tools/ops/drill.sh --keep          # leave the project up; prints the down -v command
```

Phases: `static` → `preflight` → `up` → `ready` → `migrate` → `paper` → `restart` →
`backup` → `teardown`. `ready` requires `/readyz` to report `migration: head` at the
revision in this checkout, so a stack that comes up on an older schema fails rather than
passing. `migrate` requires the one-shot container to have exited `0`. `paper` places a
buy through the edge, replays it by idempotency key and requires the same order id and a
reconciliation of `ok: true`. `restart` restarts the API and requires cash and the open
position count to be unchanged. `backup` dumps the database, restores it into the
`ezchain_scratch_drill` database inside the stack and requires the same ledger row count.

Exit codes: `0` every phase passed, `1` a phase failed (the project is still removed),
`2` the drill refused to start — no Docker CLI, no daemon, an `infra/.env` without real
secrets, or a `compose config` rejection. **`2` is not a pass**: on a host without Docker
the default invocation refuses, and `--static-only` is the honest mode.

The gate takes it only on request: `EZCHAIN_GATE_DRILL=static` or `=run`, default `off`.
The evidence for the current head, and what was skipped for the missing daemon, is in
[`docs/evidence/compose-drill/README.md`](evidence/compose-drill/README.md).

## 13. PAPER stress harness

Use this before a paper demo or after changing the order, ledger, market or strategy path.
It is PAPER-only and refuses a non-paper API before mutating anything. For an
auth-enabled API, export `EZCHAIN_STRESS_API_TOKEN` (never a CLI flag); the harness sends a
bearer header and redacts it from reports.

```bash
tools/stress/run.sh --mode smoke --duration 30
tools/stress/run.sh --mode soak --duration 300 --concurrency 4
tools/stress/run.sh --mode idempotency --duration 60
tools/stress/run.sh --mode chaos --duration 30
```

1. Confirm `GET /api/v1/paper/account` reports `mode: paper`. If not, the harness exits
   `2` with `REFUSING_NON_PAPER`; do not bypass it.
2. Read the newest Markdown report under `tools/stress/reports/` (gitignored). The JSON is
   the comparison artifact: request counts, p50/p95/p99, invariant rows and at most five
   failure samples.
3. A failure is a red run: `0` pass, `1` invariant/HTTP/latency finding, `2` config or
   safety refusal, `3` issue filing failed (report kept).
4. To file deduplicated issues for a failing run, opt in explicitly:
   `tools/stress/run.sh --mode soak --duration 300 --file-issues --repo owner/name`.
   Re-runs skip fingerprints already present in open `stress` issues. Pass
   `--dry-run-issues` to inspect payloads without calling `gh`.
5. Set `--strategy-order-usd` above the risk limit (for example `1500`) to exercise a
   rejected strategy order. The run must log `hold` / `ORDER_REJECTED`, never `buy`/`sell`
   with a rejected order id; `strategy.run_order_filled` enforces this.
6. Strategy cleanup is part of the run. If a strategy survives, the report records
   `STRATEGY_CLEANUP_FAILED` and the run is red; delete it from the strategies page only
   after recording why.
7. Do not point the harness at a live-venue API or add auth/key options to it. Chaos never
   kills processes or injects 5xx responses; it only retries an invalid path and an
   unreachable loopback port.

Details and scheduling examples: [`tools/stress/README.md`](../tools/stress/README.md).
