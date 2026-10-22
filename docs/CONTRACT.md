# CONTRACT — ezCHAIN invariants

Normative. A change that violates one of these is rejected regardless of how good it looks.
Each invariant names its enforcement point and the incident/learning behind it.

---

## I1 — The ledger is the only accountant

Cash, position size, realized PnL and fees exist **only** as append-only
`ledger_entries` (signed minor units, per account). Balances are `SUM(amount)` inside a
transaction. No cached scalar is authoritative. Position state and its ledger journal
commit atomically.

*Enforced by:* `packages/core-py/ledger` + property test `ledger_invariants`
(balance = sum, no negative unapproved accounts, every fill balances), repository layer
forbids direct balance updates.

*Learned from:* pumpbot's `balance() == SUM(wallet_rows.amount_usd)` was its best idea;
v2's fire-and-forget `loop.create_task(_write_ledger())` let the cache drift; v1/v3
accepted "ghost position reconciliation" as normal. Never again.

## I2 — Paper, testnet and live are one pipeline

An order is always `Intent → RiskDecision → Quote → Order → Fill → Journal`. The only
difference between modes is the `VenueAdapter` and the key custodian. Paper fills use the
same route and price envelope as live; no local constant-product fallback unless the
adapter explicitly reports `simulated_model` and the UI shows it.

*Enforced by:* execution contract tests that run every venue adapter twice (paper/live)
and assert identical intent/decision objects.

*Learned from:* pumpbot's paper==real mirror worked; v1 double-counted fees because the
paper path was a separate formula; v2 had dual PnL math.

## I3 — Fail closed on money

No new risk when any required input is unknown: token safety, liquidity, quote freshness,
oracle age, route identity, risk-service health. "Unknown" is a distinct state from zero.
Every decision records the exact failing dimension.

*Enforced by:* `RiskDecision.reasons[]` is mandatory on reject; adapter contract requires
explicit `unknown` for missing data; tests assert no order on timeout/exception.

*Learned from:* v2's cold-start deadlock was a gate-vs-scorer mismatch; pumpbot's
unknown-as-zero (`mcap_usd = 0`) manufactured data; v3's fail-closed picker diagnostics
were the right shape.

## I4 — Keys live in one place

Only `services/executor` can decrypt key material. Keys never appear in the web bundle,
API responses, logs, traces, strategy plugins, fixtures or container env after boot. The
vault is envelope-encrypted; the master key comes from env/file/KMS and is never logged.
Every signature is audited (who, what, intent id, timestamp).

*Enforced by:* import-linter boundary (`executor` is the only vault consumer), secret scan
in CI, API schema has no key fields, executor redacts error payloads.

*Learned from:* v2 imported the private key in routers; v3 kept plaintext keys in
`scripts/wallets.json` and a live `.env.mac_learn`; solbot committed `.secret_key`.

## I5 — No strategy code in this repository

The platform ships zero strategies. A strategy is a versioned plugin package implementing
the SDK manifest and intent interface. Platform code never imports a strategy by name.
Examples live in `ezchain-strategies` (separate repo).

*Enforced by:* CI greps `services/`/`apps/` for the strategies directory and a dependency
rule; the SDK conformance suite loads a fixture plugin from outside the tree.

## I6 — Chains are capabilities, not branches

Core code branches on capability flags (`can_swap`, `has_mempool`, `finality_depth`,
`supports_gasless`, …), never on `chain_id`/name. A new chain is a new adapter package
plus a registry manifest, no core edit.

*Enforced by:* adapter contract suite + a lint rule that bans chain-name literals outside
`packages/adapters/`.

## I7 — Every number has one home

Every parameter (risk, fees, timeouts, thresholds, slippage caps) is a schema field with
one default, one owner and one docs entry. The UI reads from the API; adapters and
strategies receive values, never their own copies. Changing a default is a reviewed PR
with a changelog line.

*Enforced by:* config schema validation at boot; a test asserting no duplicate constant
names across modules; ADR for any default that can move money.

*Learned from:* v3's 346 KB settings module, pumpbot's 200 constants and v1's five
`set_config` clobberers.

## I8 — Migrations are forward-only, never destructive

Schema changes are additive or explicitly migrating. No `wipe`, `clean_slate`,
`full_reset`, or `try: ALTER TABLE except: pass`. A fresh database must be creatable from
`main` in CI.

*Enforced by:* `tools/check-migrations.sh` upgrades a disposable Postgres database from
scratch and runs `alembic check`; `tools/ci.sh` calls it when
`EZCHAIN_TEST_DATABASE_URL` is set. A lint rejects destructive `upgrade()` bodies. The
baseline is `apps/api/alembic/versions/0001_baseline.py` and `0002_genesis_index.py` is
forward-only; `tests/api/test_migrations.py` scans every `upgrade()` body, and `/readyz`
returns 503 unless `alembic_version` equals the script head.

*Learned from:* v2 had ~30 destructive migrations; v3 needed an out-of-repo shim to build
a fresh DB; pumpbot had 12 silent ALTERs.

## I9 — Replay or it didn't happen

Every rule that gates money must be computable from persisted inputs. Observations are
recorded before decisions; counterfactual outcomes are recorded for rejected candidates.
Market data has declared units and freshness; mixed-unit values are rejected, not coerced.

*Enforced by:* event schemas require `observed_at`, `source`, `units`; replay harness
reconstructs each decision from the event log and must match the recorded decision.

*Learned from:* pumpbot's liquidity brakes were unmeasurable; v2 had no false-negative
tracking; v3 had a 1000× lamport/token unit bug.

## I10 — One position model, one tile

The API exposes a single `PositionView` (docs/WIREFRAME.md). PnL, equity, ladder and
freshness are computed once, server-side; clients format only. No page may recompute PnL.

*Enforced by:* OpenAPI schema is the only tile source; UI has no PnL arithmetic; a
contract test asserts all screens render from the same fixture.

*Learned from:* three divergent tile implementations in solbot/pumpbot; tile-lab field
drift; v3's client-side `economic_return_pct` vs `cash_return_pct` confusion.

## I11 — Boring process, hard limits

One core deployable by default; services split only when a measured need exists (CPU,
failure domain, security boundary). Every process has a memory/CPU budget, a health
endpoint and a restart policy. Background loops have jitter, backoff and a kill switch.
No unbounded queues, no in-memory-only money state.

*Enforced by:* compose resource limits, load/soak tests in CI-nightly, a queue-depth
alert.

*Learned from:* v1's "Postgres/Redis decoration", v2/v3's one-process "microservices",
pumpbot's 81 KB single-page UI and mega-modules.

## I12 — Claim only what was measured

Every performance claim in docs, PRs or UI names its harness, data population and time
window. Paper results are labelled PAPER. Backtests are labelled with fees, slippage and
fill model. No synthetic "demo" numbers in production surfaces.

*Enforced by:* PR template checkbox; docs lint for unlabelled returns; UI badge on every
metric card (`LIVE | PAPER | BACKTEST`).

*Learned from:* every bot's docs drifted from code; one shipped a +$1,625 fabrication.
