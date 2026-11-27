# LIVE EXECUTION — the real-money contract (v1)

Status: **contract for live trading.** Normative together with [SECURITY.md](SECURITY.md)
§1–§3 and [ARCHITECTURE.md](ARCHITECTURE.md). ADR-0026 records the decision. Paper and
live are designed to share one pipeline — `Intent → RiskDecision → Quote → Order → Fill →
Journal` — with only the custodian (executor) and the venue transaction differing.

## 0. What exists in this build

This document is the contract for a system that is **partly built**. Read this table before
treating any section below as a description of running code; where the two disagree, the
code is right and this table is the index.

| Contract element | In this build |
|---|---|
| The one pipeline (`services/executor`, `Intent → … → Journal`) | **Built**, behind the `Venue` protocol, with a fake venue and 18 transaction-binding tests |
| Signing boundary: decode and bind before any signer runs | **Built** |
| Chain-native decoders/builders and submission-only broadcast (§5) | **Built** for five families: real Solana/EVM/TON/Sui/Cosmos unsigned-byte decoders feed the trust `decode_and_bind` boundary, builders speak `TransactionAdapter.prepare`, and broadcast is idempotent/time-bounded. **Not built:** signing, `Fill`, `Venue.submit` wiring. An RPC hash is a `submitted` receipt, never a fill. |
| Read-only chain confirmation/status and executor reconciliation seam (§6) | **Built**: typed `confirmed`/`failed`/`pending`/`unknown` readers over documented RPC shapes, plus `ChainConfirmationVenue` for the existing outbox. A confirmed hash maps to `unknown` for the outbox because no executed amount is proven, so no claim journals from status alone. |
| Sui signed transport (§5) | **Built as a request shape only**: `SignedTransaction` carries an optional *public* key so the adapter can serialize `flag‖sig‖pk`. Sui Foundation disabled legacy JSON-RPC on mainnet in July 2026, so this shape is not live-ready; no node was contacted. |
| Custody seam: `KeyHandle`, `SignerTransport`, typed refusals | **Built** — a *seam*, with a fake transport in tests; no shipped device or local-vault transport |
| Broadcast outbox and reconciliation of claims without a journal | **Built** — `FileAttemptLog` + `Reconciler`; the durable log must be injected, the in-memory default is not durable |
| Executor HTTP service, `executor_client`, HMAC loopback (§1) | **Not built** — no FastAPI app, no client module, no port |
| Encrypted `vault.json` with argon2id + AES-GCM (§2) | **Built**, import-only: versioned record, Argon2id KEK, AES-256-GCM with the header as associated data, typed refusals. No signing transport, CLI or auto-lock wiring |
| Simulate stage and `SIMULATION_FAILED` (§3.4) | **Not built**. The code is registered for the pure preflight kernel; no pipeline stage emits it yet. |
| Live tables `live_intents` / `live_orders` / `live_fills` (§3.1) | **Not built** — no live tables exist |
| `live:` ledger account prefix (§3.8) | **Not built** — no live book. Paper accounts are `cash:USD`, `fees:USD`, `pnl:realized:USD`, `position:<chain>:<token>` |
| On-chain balance reconciliation and `RECONCILE_DRIFT` (§6) | **Not built**, and the code is not in the taxonomy |
| `LIVE_EXECUTOR_SHIPPED` flipped, a live intent executed | **False** — mode `live` refuses with `GATE_NOT_MET` |

The pipeline's real success and refusal codes are the taxonomy in
[REASON-CODES.md](REASON-CODES.md); anything named in this document but absent there is a
plan, not a code.

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

With a signing boundary configured, what is signed is provably what was approved. The
venue adapter receives an immutable `ezchain_core.signing.ApprovedIntent` (chain, cluster,
mint, side, amount, minimum output, recipient, program id, idempotency key) and returns
bytes; it is not handed the order or the quote, so it has no second source of truth. The
pipeline itself decodes those bytes and compares every one of those fields with the
approval before any signer runs: a swapped mint, an inflated amount, a changed recipient, a
foreign program, another cluster, the wrong side, a lowered floor or a replaced key refuses
with its own `TRANSACTION_*` code and no signature. Because the check is in the pipeline,
not in the signer, a substitute or buggy `TransactionSigner` cannot disarm it; a signer that
also checks keeps that as defence in depth rather than as the guarantee. The signature that
returns is verified again against the pipeline's decode — the approval it carries must be
the approved one (`TRANSACTION_APPROVAL_MISMATCH`) and its bytes and digest must be the ones
that were bound. The signed payload's SHA-256 digest is then carried through submission, and a
receipt that does not echo it is refused (`TRANSACTION_DIGEST_MISMATCH`) and never
journaled. Paper may run without a boundary and submits unsigned; a pipeline in any other
mode refuses an unsigned submit with `SIGNING_BOUNDARY_REQUIRED` before the venue is
called.

Live also requires custody, not just a boundary (ADR-0040). Custody is the user's: a key is
added by its owner, addressed by a `KeyHandle` that names that owner, and signed through
the `Vault` seam. A signer that carries no signable per-user handle — a bare in-memory key
ring — refuses in live with `VAULT_BOUNDARY_REQUIRED` before the venue, because a key
nobody custodies must not spend real money. A watch-only handle cannot sign and refuses
there too. The device is one custody option among `local_vault` and `hardware`, behind a
vendor-agnostic `SignerTransport`, so an unreachable device, a declined approval, an
unknown handle, a locked signer or a device that answers for another key each produce no
signature and one typed reason.

## 1. Deployables and trust

```
web ──proxy──▶ api (+ inline engine) ──HMAC/loopback──▶ executor ──▶ RPC / venue APIs
                     │ Postgres (orders, ledger, intents)        │ vault file (0600)
```

**None of this topology is deployed yet** (§0): `services/executor` is a library with no
HTTP app, no client module and no port. The box below is the design the boundary must
satisfy when it is written, kept here because the custody argument depends on it.

- `services/executor` is the intended separate process (`ezchain-executor`, FastAPI on
  `127.0.0.1:8100` or a unix socket). It is the **only** code meant to import signing
  libraries (`solders`, `eth-account`) or read the vault. `apps/api` and the engine would
  talk to it only through `ezchain_api.executor_client` with a per-request HMAC
  (`EZCHAIN_EXECUTOR_SECRET`, ≥ 32 bytes) over `intent_id|ts|body-sha256`, 30 s skew. The
  gate already refuses to run with that variable set, so a test can never inherit one.
- The executor is to expose **no generic sign endpoint**. Endpoints: `GET /health`,
  `GET /wallets` (public addresses only), `POST /vault/unlock`, `POST /vault/lock`,
  `POST /intents/{intent_id}/execute`, `GET /intents/{intent_id}`.
- The executor re-reads the kill switch and the live-arm state before every signature (fail
  closed if unreadable) and re-validates the intent (amount ≤ approved, wallet, chain, side,
  venue, expiry ≤ 60 s). The kill-switch half of that is built in the pipeline; the arm
  state has no reader because there is no arm flow.

## 2. Vault

**The custody seam and an import-only encrypted file are built** (§0).
`services/executor/src/ezchain_executor/vault.py` holds the signing seam: a
`SignerTransport`, a `HardwareSignerVault` over it, a `VaultTransactionSigner` that binds
a prepared transaction to an `ApprovedIntent`, and one typed refusal per failure.
`vault_local.py` holds import validation, the versioned record and
`EncryptedLocalVaultStore`. The store seals an imported secret with Argon2id
(`argon2-cffi`) and AES-256-GCM (`cryptography`) into a mode-0600 file written atomically,
and binds the version, id, family, address, fingerprint, KDF parameters and nonce as AEAD
associated data. The vault passphrase must be non-empty, and version 1 reads only the
reviewed Argon2id parameters this build writes; a header that asks for more work is
refused before the KDF runs. Files over 8 MiB are refused before parsing. BIP39
sentences are checked with `mnemonic`, including the optional passphrase. Derivation
paths are bound as public metadata; no child key is derived here. Unlock compares the
handle's owner, custody, address, chain and fingerprint before authenticating, and
returns a `KeyHandle`, never the bytes it opened.

What the rest of this section describes is still a design, not running code:

- File `${EZCHAIN_DATA_DIR}/vault.json`, mode 0600. One record per key: `{id, label,
  family (svm|evm), address, fingerprint, kdf: {alg: argon2id, salt, t: 3, m: 65536, p: 1},
  cipher: {alg: aes-256-gcm, nonce, ct}, created_at}`. The KEK comes from Argon2id over the
  owner's vault passphrase with that record's salt; the imported secret is sealed under
  the KEK.
- Unlock is explicit (passphrase, plus TOTP when 2FA is enabled) and auto-locks after
  `EZCHAIN_VAULT_IDLE_MINUTES` (default 30). Every unlock/lock/sign is an audit row. Only
  the passphrase part is implemented; TOTP, idle timeout and audit rows are not built.
- Keys are **imported only**: no generation, export, recovery kit or browser key entry, and
  no wallet CLI. The public address and fingerprint are supplied by the caller, because
  this tier has no chain signing library and derives neither. The encrypted file is the
  only persistence, not a backup product.
- The API stores wallet metadata (`wallets` table: id, label, family, address, custody
  `local_vault|watch`, chains, policy) — never key material.

## 3. Live pipeline

1. **Intent** (`live_intents` row): manual order or strategy decision with `mode=live`,
   wallet id, chain, token, side, `amount_usd`/`quantity`, `max_slippage_bps`,
   idempotency key. Unique by idempotency key. **There is no `live_intents` table**; the
   pipeline's `Intent` is the typed object in `ezchain_core.execution` and dedupe is on the
   idempotency key it carries, not on a row.
2. **RiskDecision**: the paper policy **plus** live limits from settings/DB:
   `live_max_order_usd` (default 50), `live_max_daily_notional_usd` (default 250),
   `live_max_daily_loss_usd`, per-chain enable list, wallet policy, LIVE armed, kill
   switch off, strategy paper gate passed (strategies only). Any unknown → deny. **The live
   limits are not implemented**: no `live_max_*` setting exists in this build, so the paper
   policy is the whole policy. A sell is re-bounded against the held quantity before submit
   and refuses a receipt larger than the order (`RECEIPT_EXCEEDS_ORDER`), which is the one
   place the two sides are treated differently.
3. **Quote/Build**: the venue adapter builds the unsigned transaction from the immutable
   `ApprovedIntent` for the wallet address — it is handed nothing else. Solana via Jupiter
   `/swap/v1/swap` (versioned tx); EVM via KyberSwap `/api/v1/route/build` (router
   calldata) with an exact ERC-20 `approve` for the amount when allowance is short (never
   unlimited). `min_out` comes from the quote and `max_slippage_bps`. The pipeline decodes
   the returned bytes and checks them against the approval before any signer runs (program
   id, cluster, mint, side, amount, minimum output, recipient, idempotency key); any
   difference is a `TRANSACTION_*` refusal with no signature. **Real chain-native decoders
   and builders now exist for five families** (§5): each builder takes the immutable
   approval and returns chain-native unsigned bytes, and each decoder reads those bytes
   into the field map the trust `decode_and_bind` boundary compares. The executor pipeline
   still uses the neutral envelope for its own signing-boundary tests; it does not call a
   chain-native adapter yet.
4. **Simulate**: *not built* (§0). The stage is designed as Solana `simulateTransaction` and
   EVM `eth_estimateGas` + `eth_call`, refusing a failed simulation before any signature.
5. **Sign + broadcast** in the executor. The pipeline decodes, binds, re-reads the kill
   switch, then signs — in that order, so an arm that lands during the build refuses before
   the key is touched. The boundary rejects a signature whose approval, bytes or digest are
   not the ones it bound, and the signed payload's digest is recorded as the transaction's
   identity. **Nonce serialisation and one-in-flight-transaction-per-wallet are not built**:
   there is no nonce manager, and the only in-flight guard is one per idempotency key, so
   two *different* intents for one wallet can be in flight together.
6. **Confirm**: poll receipt/signature status to finality (`confirmed` Solana, 1–3 blocks
   EVM per chain config) with a timeout (default 90 s). The receipt must echo the signed
   payload's digest; a different or missing digest is `TRANSACTION_DIGEST_MISMATCH` and
   nothing is journaled. A timeout returns `TIMEOUT` on an order whose status is `timeout`,
   and the outbox claim it left behind stays open for reconciliation. A persisted
   `UNCONFIRMED` state is not built; there is never a blind resend.
7. **Fill**: computed from the **receipt** (token balance deltas / Transfer logs), not
   from the quote. Gas/fees in USD from the receipt.
8. **Journal**: ledger entries in the same transaction as the order/fill rows, through the
   sink, and only from the receipt. Paper and live are meant never to share accounts; the
   `live:` prefix that would separate them is part of the unbuilt live book (§0), so today
   there is one book.

## 4. Modes and gates

- System mode is `PAPER` until an operator sets `EZCHAIN_EXECUTION_MODE=live` and a signed
  live-gate record verifies against `EZCHAIN_LIVE_GATE_RECORD` + `EZCHAIN_LIVE_GATE_KEY`.
  The mode is derived server-side and stamped on every number; the UI cannot hard-code it.
  The in-app arming flow (1.2 s hold, TOTP, an expiry setting) is part of the unbuilt live
  surface, not a behaviour of this build: `mode.py` reads those two values and nothing else.
- A strategy can trade live only if its **paper gate** passed and the owner promoted it
  (L4). The thresholds are configuration, and the defaults in code are ≥ 14 days,
  ≥ 100 intents, ≥ 20 fills, no invariant breach and the same-window backtest
  (`paper_gate_min_days`, `paper_gate_min_intents`, `paper_gate_min_fills`,
  `paper_gate_require_backtest` in `apps/api/src/ezchain_api/config.py`; ADR-0009). The
  gate is **computed on read** by `services/readiness.py` and reported per strategy; no
  gate record is persisted against a strategy version, and a live intent is not yet
  refused by it — the only live refusal today is the deployment gate below. Manual live
  orders need LIVE armed and an L2 confirm with the quote.
- Kill switch: the executor refuses new **entries** while armed, and a re-read sits
  immediately before the signer call so an arm that lands mid-intent still refuses without
  spending the key. Exits are deliberately not recalled — a sell only reduces the held
  position. The "L3 explicit owner confirmation" for live exits is part of the unbuilt live
  surface; today the pipeline lets a reduce-only sell settle with no extra prompt. The
  persisted switch reports one flag at the widest scope in force, with `global` and
  `strategy` levels; per-chain and per-wallet scopes are not built.

## 5. Chains in v1

**The live column is still the plan.** The *quote* clients (`JupiterClient`,
`KyberSwapClient`, and the other keyless providers) are unchanged and are reused; this
change does not duplicate quote parsing. What is now built, per `docs/CHAINS.md` §2.1, is a
real chain-native decoder and builder for Solana, EVM, TON, Sui and Cosmos, plus an
idempotent, time-bounded broadcast adapter. Each builder implements
`TransactionAdapter.prepare(approved)` and is handed only the immutable approval; each
decoder reads chain-native bytes into the field map `decode_and_bind` compares. Wire-layer
evidence comes from real public fixtures recorded with source URL, retrieval date, hash and
a no-secret rationale (§2.2 of `CHAINS.md`); negative cases are constructed and labelled as
such. Every request shape is answered by a mocked HTTP transport. There is no funded
address, no real transaction and no live venue contact.

Sui's reviewed request shape is implemented but is **not live-ready**. Official Sui docs
define the serialized signature as `flag || sig || pk`; `SignedTransaction` now carries one
optional public field for that assembly, and unsigned or keyless submissions refuse with a
typed error. Sui Foundation disabled legacy JSON-RPC on mainnet in July 2026 with full
decommission in October 2026, so a future live path must be reviewed against its gRPC or
GraphQL replacement. No Sui mainnet node was contacted.

**A submission receipt is not a fill.** A successful RPC call returns a `submitted`
receipt with the hash the RPC returned. It is not an executed trade, not a balance delta
and not a journal entry. There is no conversion from that receipt to a `Fill`, and no
executor `Venue.submit` wiring. That wiring stays undone until execution amounts can be
proven from chain evidence and bound to the order; treating a hash as filled would put an
unconfirmed claim into the ledger path. Read-only status readers exist (§6) but never
produce a `Fill`. Signing also still stays behind `SignerTransport` in the executor; these
adapters hold no key.

| Family | Chains | Live in v1 | Test harness |
|---|---|---|---|
| svm (Solana) | `solana` | no (`live_execute: false`) | constructed legacy-message bytes; `MockTransport` for `sendTransaction` |
| evm | Ethereum, Base, Arbitrum, Polygon, BSC, Optimism, Avalanche, Linea and the other registered EVM chains | no (`live_execute: false`) | constructed RLP legacy bytes with each chain's real EIP-155 `v`; `MockTransport` for `eth_sendRawTransaction` |
| tvm (TON) | `ton` | no (`live_execute: false`) | real TL-B corpus BoC for framing; `MockTransport` for v2 `sendBocReturnHash` |
| move (Sui) | `sui` | no (`live_execute: false`) | real SDK BCS bytes for wire evidence; signed-shape `MockTransport`; keyless input refused |
| cosmos | `osmosis`, `cosmos` | no (`live_execute: false`) | real CometBFT `TxRaw` for hash identity; `MockTransport` for `broadcast_tx_sync` |

The old plan's forked-anvil and Solana-simulation harnesses do not exist in this
repository, and there is no funded address to run one against. A mocked submission test
is a different claim from a confirmed, forked-chain fill: it proves only that the bytes
were submitted and that an ambiguous answer fails closed.

RPC endpoints are the design's configuration story; there is no `chain_rpc` table and no RPC
URL setting in this build, and `/chains` renders registry data with no editable endpoint.
When that lands, secrets in RPC URLs are references, never logged.

## 6. Reconciliation

Built: an **outbox reconciler** for broadcasts this platform sent and did not journal. The
pipeline writes a claim to the attempt log and fsyncs it before the venue sees the order; a
claim older than the submit timeout is asked about once and resolved to `journaled` or
`failed`. A venue that answers `unknown`, a status read that raises, and a receipt that
cannot be booked all leave the claim **pending** — never resolved into money nobody
confirmed. The reconcile is safe beside a live fill (age window) and safe to run twice (the
group check). `FileAttemptLog` is the durable log; the in-memory default is not, and
production must inject the file one. There is no boot hook.

Added in this field: `ChainConfirmationVenue`, a read-only seam the outbox can call. A
failed chain transaction resolves an old claim as never filled; `confirmed`, `pending`,
`unknown` and an unreadable status all leave the claim pending, because no executed amount
is proven. No `Fill` is constructed from chain status and no journal entry follows from it.

Not built: comparing on-chain balances (RPC) with ledger positions, pausing a wallet or
strategy on drift, and the `RECONCILE_DRIFT` code it would raise — that code is absent from
the taxonomy, so nothing emits it.

## 7. What is never done

No unlimited approvals, no key export, no browser key entry, no auto-retry of a failed
broadcast with a new nonce, no live order without a successful simulation, no live
number shown without `LIVE`, no mixing of paper and live totals.

The fourth of those, "no live order without a successful simulation", is a rule about a
stage this build does not have (§0). It holds trivially today because no live order runs at
all; it must be re-read as an obligation when the simulate stage is written, not as a
property the current code enforces.
