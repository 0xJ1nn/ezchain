# LIVE EXECUTION — the real-money contract (v1)

Status: **contract for live trading.** Normative together with [SECURITY.md](SECURITY.md)
§1–§3 and [ARCHITECTURE.md](ARCHITECTURE.md). ADR-0026 records the decision. Paper and
live are designed to share one pipeline — `Intent → RiskDecision → Quote → Order → Fill →
Journal` — with only the custodian (executor) and the venue transaction differing.

The reference pipeline is implemented once, in
`services/executor/src/ezchain_executor/pipeline.py`. Its stages are the typed objects in
`ezchain_core.execution` (`Intent`, `RiskDecision`, `Quote`, `Order`, `Fill`, `Journal`);
the venue sits behind the core `Venue` protocol, so paper, testnet and live differ only by
the adapter and the custodian injected. **It is not yet the paper execution path:** paper
orders are still placed by the API paper order service
(`apps/api/src/ezchain_api/services/paper.py`), which validates risk, writes the order and
fill, and appends ledger rows directly. That service is the current caller of record; its
delegation to this pipeline is tracked separately. The executor runner fails closed: a buy
with no market snapshot is denied before any venue call, a missing or non-executable quote
produces no order, a stale quote or missing snapshot is denied after the quote stage for
both sides, and an unreadable kill switch refuses. Duplicate intents are deduped on the
idempotency key bound to the payload before the venue is quoted, so a retry yields one
order and one fill, while the same key with a different payload is a conflict. A
reduce-only exit is sized to the held position, re-bounded against it before submit, and is
never capped below it. The runner never writes the ledger: it appends a balanced `Journal`
through a sink, and only from the venue receipt.

## 1. Deployables and trust

```
web ──proxy──▶ api (+ inline engine) ──HMAC/loopback──▶ executor ──▶ RPC / venue APIs
                     │ Postgres (orders, ledger, intents)        │ vault file (0600)
```

- `services/executor` is a separate process (`ezchain-executor`, FastAPI on
  `127.0.0.1:8100` or a unix socket). It is the **only** code that imports signing
  libraries (`solders`, `eth-account`) or reads the vault. `apps/api` and the engine talk
  to it only through `ezchain_api.executor_client` with a per-request HMAC
  (`EZCHAIN_EXECUTOR_SECRET`, ≥ 32 bytes) over `intent_id|ts|body-sha256`, 30 s skew.
- The executor exposes **no generic sign endpoint**. Endpoints: `GET /health`,
  `GET /wallets` (public addresses only), `POST /vault/unlock`, `POST /vault/lock`,
  `POST /intents/{intent_id}/execute`, `GET /intents/{intent_id}`.
- The executor re-reads the kill switch and the live-arm state from Postgres before every
  signature (fail closed if unreadable) and re-validates the intent (amount ≤ approved,
  wallet, chain, side, venue, expiry ≤ 60 s).

## 2. Vault

- File `${EZCHAIN_DATA_DIR}/vault.json`, mode 0600. One record per key: `{id, label,
  family (svm|evm), address, kdf: {alg: argon2id, salt, t: 3, m: 65536, p: 1}, cipher:
  {alg: aes-256-gcm, nonce, ct}, created_at}`. Per-key DEK encrypted by a KEK derived from
  the owner passphrase.
- Unlock is explicit (passphrase, plus TOTP when 2FA is enabled) and auto-locks after
  `EZCHAIN_VAULT_IDLE_MINUTES` (default 30). Every unlock/lock/sign is an audit row.
- Keys are **generated inside the executor** (`ezchain-executor wallet new --family evm`)
  or imported through the **executor CLI only** (`wallet import`, reads stdin, never
  argv). There is no browser key entry (ADR-0016) and **no key export** — backup is the
  encrypted vault file itself.
- The API stores wallet metadata (`wallets` table: id, label, family, address, custody
  `local_vault|watch`, chains, policy) — never key material.

## 3. Live pipeline

1. **Intent** (`live_intents` row): manual order or strategy decision with `mode=live`,
   wallet id, chain, token, side, `amount_usd`/`quantity`, `max_slippage_bps`,
   idempotency key. Unique by idempotency key.
2. **RiskDecision**: the paper policy **plus** live limits from settings/DB:
   `live_max_order_usd` (default 50), `live_max_daily_notional_usd` (default 250),
   `live_max_daily_loss_usd`, per-chain enable list, wallet policy, LIVE armed, kill
   switch off, strategy paper gate passed (strategies only). Any unknown → deny.
3. **Quote/Build**: the venue adapter builds the unsigned transaction for the wallet
   address: Solana via Jupiter `/swap/v1/swap` (versioned tx); EVM via KyberSwap
   `/api/v1/route/build` (router calldata) with an exact ERC-20 `approve` for the amount
   when allowance is short (never unlimited). `min_out` comes from the quote and
   `max_slippage_bps`.
4. **Simulate**: Solana `simulateTransaction`; EVM `eth_estimateGas` + `eth_call`. A
   failed simulation rejects (`SIMULATION_FAILED`) — nothing is signed.
5. **Sign + broadcast** in the executor; nonce per (chain, address) serialized; one
   in-flight tx per wallet per chain.
6. **Confirm**: poll receipt/signature status to finality (`confirmed` Solana, 1–3 blocks
   EVM per chain config) with a timeout (default 90 s). Timeout → `UNCONFIRMED`, the
   reconciler resolves it later; never resend blindly.
7. **Fill**: computed from the **receipt** (token balance deltas / Transfer logs), not
   from the quote. Gas/fees in USD from the receipt.
8. **Journal**: ledger entries in the **live book** (`live:` account prefix) in the same
   transaction as the order/fill rows. Paper and live never share accounts or totals.

## 4. Modes and gates

- System mode is `PAPER` until the owner **arms LIVE** (L4: 1.2 s hold + TOTP; audited,
  expires after `EZCHAIN_LIVE_ARM_HOURS`, default 12). Disarm is one click. The mode is
  derived server-side and stamped on every number; the UI cannot hard-code it.
- A strategy can trade live only if its **paper gate** passed (configurable thresholds,
  defaults: ≥ 7 days, ≥ 20 intents, ≥ 5 fills, no invariant breach) and the owner
  promoted it (L4). Manual live orders need LIVE armed and an L2 confirm with the quote.
- Kill switch: the executor refuses new intents while armed; exits (sells) for already
  open live positions are still allowed only when the owner explicitly confirms (L3).

## 5. Chains in v1

| Family | Venue | Live in v1 | Test harness |
|---|---|---|---|
| svm (Solana) | Jupiter swap API | yes | `simulateTransaction` (sigVerify false) on mainnet RPC with a funded address; unit tests with recorded payloads |
| evm (Ethereum, Base, Arbitrum, Optimism, Polygon, BSC, Avalanche, Linea) | KyberSwap build | yes | Foundry `anvil --fork-url` of each chain: real build → sign → send → receipt → fill |
| others | — | no (`live_execute: false`, labelled) | — |

RPC endpoints are configuration (`chain_rpc` table / settings, public defaults), shown and
editable on Chains & RPC. Secrets in RPC URLs are references, never logged.

## 6. Reconciliation (live)

Every N minutes and on executor boot: for each live wallet and chain, compare on-chain
token balances (RPC) with the live ledger positions. Drift → `RECONCILE_DRIFT` alert and
the affected strategy/wallet pauses (fail closed). Unconfirmed intents are resolved from
chain state, never resent.

## 7. What is never done

No unlimited approvals, no key export, no browser key entry, no auto-retry of a failed
broadcast with a new nonce, no live order without a successful simulation, no live
number shown without `LIVE`, no mixing of paper and live totals.
