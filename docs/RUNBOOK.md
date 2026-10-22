# RUNBOOK — incidents and recovery

Commands use the `ezchain-admin` interface defined in `docs/INFRA.md`. Until M1 they are
the contract, not a working binary. Every action here is audited.

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

1. Provision a clean host and the same image digests.
2. Restore the newest dump whose `SHA256SUMS` verifies; restore artifacts.
3. Run `ezchain-admin verify` (schema, ledger invariant, artifact hashes).
4. Start in `read-only`, compare balances at the restore block, then enable paper.
5. Record the drill: date, dump id, duration, drift result, operator.

A restore that cannot reproduce the ledger to the last entry is a failed drill.

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

### 10.1 Paper strategy switch (implemented, P0)

The paper slice implements only the **strategy** scope. The state is a row in
`system_flags` keyed `engine_kill_switch`, seeded from `EZCHAIN_ENGINE_KILL_SWITCH` on
first boot, so it survives a restart and is read by every process.

```bash
curl -s localhost:8000/api/v1/system/kill-switch

curl -s -X POST localhost:8000/api/v1/system/kill-switch \
  -H 'content-type: application/json' \
  -d '{"active": true, "reason": "venue incident"}'
```

- `POST` returns `{active, reason, updated_at, scope: "strategy"}`. `updated_at` is the
  audit timestamp; there is **no authentication yet**, so the actor is not recorded.
  Keep the endpoint on a trusted network until ops adds auth. Releasing (`active: false`)
  requires a non-empty reason and returns 422 otherwise; arming may omit it.
- Two processes may boot together; the seed insert is idempotent and the loser of the
  race reads the winner's row instead of failing startup.
- The engine checks the flag on every strategy-originated order, and an armed switch makes
  the scheduler skip its pass before any market read. When a manual run is attempted, the
  run log records `hold` / `KILL_SWITCH_ACTIVE` with the attempted action and amount, and
  the strategy's interval window is **not** consumed — releasing the switch retries on the
  next tick.
- Manual paper orders stay allowed. The HTTP route does not accept `strategy_id`;
  strategy attribution is internal to the engine. As defence in depth, the order service
  also rejects any strategy-attributed order while the switch is armed
  (`KILL_SWITCH_ACTIVE`) before it requests a quote. Existing positions are not sold by
  the switch; close them deliberately.
- Before disarming, read `GET /api/v1/paper/reconciliation` and confirm `ok: true`.

## 11. Market monitor (market eyes)

The API observes every registered chain continuously when
`EZCHAIN_MARKET_SCAN_INLINE=true` (default). One scan makes a single `chain=all`
discovery call, uses its per-chain coverage, and persists every row to
`market_snapshots` before it is served. A chain the fan-out already attempted is not
re-probed; a failed all-chain call falls back to one probe per chain. The adapter fan-out issues one token lookup per
registered chain (seeds first, four in flight) and round-robins ranked rows before the
page limit. The scan interval is
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

## 12. PAPER stress harness

Use this before a paper demo or after changing the order, ledger, market or strategy path.
It is PAPER-only and refuses a non-paper API before mutating anything.

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
5. Strategy cleanup is part of the run. If a strategy survives, the report records
   `STRATEGY_CLEANUP_FAILED` and the run is red; delete it from the strategies page only
   after recording why.
6. Do not point the harness at a live-venue API or add auth/key options to it. Chaos never
   kills processes or injects 5xx responses; it only retries an invalid path and an
   unreachable loopback port.

Details and scheduling examples: [`tools/stress/README.md`](../tools/stress/README.md).
