# CHAINS — adapter model and support matrix

"All chains" is not a list of RPCs. It is a capability contract, a set of adapters and an
honest matrix of what is actually verified. This document defines all three.

## 1. Adapter contract

```python
class ChainAdapter(Protocol):
    chain: ChainMeta  # id, family, native_asset, decimals, finality, explorer
    capabilities: Capabilities

    async def validate_address(self, address: str) -> AddressInfo: ...
    async def list_accounts(self, address: str, cursor: str | None) -> AccountPage: ...
    async def get_balances(self, address: str) -> list[Balance]: ...
    async def get_token(self, mint: str) -> TokenMeta: ...
    async def build_transfer(self, req: TransferRequest) -> UnsignedTx: ...
    async def estimate_fee(self, tx: UnsignedTx) -> FeeEstimate: ...
    async def simulate(self, tx: UnsignedTx) -> SimulationResult: ...
    async def broadcast(self, signed: SignedTx) -> TxHash: ...
    async def get_receipt(self, tx_hash: str) -> Receipt: ...
    async def subscribe(self, filter: EventFilter) -> AsyncIterator[ChainEvent]: ...
```

`UnsignedTx` is an opaque, versioned envelope: the executor signs it without interpreting
chain internals; the adapter verifies it. Signing never happens in the adapter.

```python
@dataclass(frozen=True)
class Capabilities:
    native_transfer: bool
    token_transfer: bool
    swap: bool
    limit_order: bool  # native or via venue adapter
    gasless: bool
    mempool: bool
    reorg_depth: int
    finality_seconds: int
    address_formats: tuple[str, ...]
    token_standards: tuple[str, ...]
    event_streams: tuple[str, ...]
    rent: bool  # account rent / storage deposits
    simulation: bool
```

Core code reads capabilities, never `chain.id`. A test lints chain-name literals outside
`packages/adapters/`.

## 2. Venue adapters (execution)

| Interface | Methods |
|---|---|
| `VenueAdapter` | `quote(request)`, `build_swap(quote)`, `simulate(tx)`, `submit(tx)`, `status(order)` |
| `QuoteRequest` | chain, pair, side, amount, slippage_cap, ttl, wallet |
| `Quote` | buy quote, **full-size sell quote**, route identity, expected/min out, price impact, fees, account rent, source, observed_at, ttl |

### 2.1 Chain-native transaction support (built, not live)

Five families now have real unsigned-transaction decoders and builders under
`packages/adapters/src/ezchain_adapters/chains/`, selected by the data registry in
`chains/registry.py`. A decoder reads chain-native bytes — Solana's compact-u16 message,
EVM's RLP legacy transaction, a TON single-cell BoC, Sui's BCS programmable transaction or
a Cosmos protobuf `TxBody` — and produces the field map consumed by
`ezchain_core.trust.decode_and_bind`. The neutral JSON envelope is no longer the only
decoder. Every builder implements the shared `TransactionAdapter.prepare(approved)` protocol
and takes only the immutable approval; quote adapters are not duplicated.

| Family | Chains with a decoder + builder | Broadcast request shape | Signing / executor `Venue.submit` | Confirmation → `Fill` |
|---|---|---|---|---|
| svm (Solana) | `solana` | JSON-RPC `sendTransaction`, idempotent, time-bounded | **not wired** | **not built** |
| evm | `ethereum`, `base`, `arbitrum`, `polygon`, `bsc`, `optimism`, `avalanche`, `blast`, `mantle`, `linea`, `scroll`, `zksync`, `fantom`, `cronos`, `celo`, `moonbeam`, `metis` | JSON-RPC `eth_sendRawTransaction`, idempotent, time-bounded; EIP-155 `v` uses each chain's registered id | **not wired** | **not built** |
| tvm (TON) | `ton` | TON v2 REST POST `sendBocReturnHash` (`{"boc": …}`); plain `sendBoc` has no hash and is not used | **not wired** | **not built** |
| move (Sui) | `sui` | JSON-RPC `sui_executeTransactionBlock`, **signed only**: 4 params `[txBytes, [flag‖sig‖pk], options, requestType]`; unsigned or keyless input refused | **not wired** | **not built** |
| cosmos | `osmosis`, `cosmos` | JSON-RPC `broadcast_tx_sync` with `0x`-hex bytes; CheckTx `code != 0` refuses | **not wired** | **not built** |

The EVM builder takes each chain's real EIP-155 id from the registry and writes it to `v`; the decoder refuses a transaction whose `v` names another chain than the approved one. Sui's decoder and builder are unchanged, but its broadcast now uses the reviewed signed request shape. Official Sui docs define the serialized signature as `flag || sig || pk` (Ed25519 flag `0x00`, 64-byte signature, 32-byte public key). Because the executor's signer output carried only the bare signature, `SignedTransaction` gained one optional, non-secret `public_key` field; the adapter concatenates the two public values and never derives, stores or logs key material. The older `sui_executeTransaction` had a separate public-key array; the modern method does not, so no `publicKey` param is invented. An absent public key, an unknown signature encoding, or an unsigned submission fails closed with a typed error (`BROADCAST_PUBLIC_KEY_REQUIRED` or `BROADCAST_UNSUPPORTED`) before any request is sent.

**Sui live readiness remains gated.** Sui Foundation disabled legacy JSON-RPC on mainnet in
July 2026 and announced full decommission for October 2026. The implemented shape is proved
against the documented schema and the SDK's serialized-signature format with mocked
transports only; no claim is made that a Sui mainnet node accepts it. A live Sui path needs
the mapped `TransactionExecutionService.ExecuteTransaction` (gRPC) or `Mutation.executeTransaction`
(GraphQL) surface, reviewed separately.

A successful RPC call returns a **`submitted` receipt only**: it carries the transaction
hash returned by the RPC, not a fill, not a balance delta and not a journal entry. There is
no conversion from a submission receipt to a `Fill`, and no `Venue.submit` implementation in
this change. That wiring stays undone until each chain's execution amounts can be proven from
chain evidence; an RPC accepting bytes is not evidence of execution. Ambiguous submissions
(timeout, 5xx, transport failure, missing hash) raise a typed broadcast error and leave no
receipt a caller could book. Tests use real public byte fixtures for wire-layer evidence and
constructed byte vectors for negative cases, all answered by mocked HTTP transports — no
funded address, real key, live venue or real transaction. `live_execute` remains `false` for
every chain; none of this is live trading.

### 2.2 Real public byte fixtures (provenance)

`tests/adapters/fixtures/chains/*.json` holds one real public byte fixture per family. Each
record carries the source URL, retrieval date, SHA-256 of the exact bytes, a no-secret
rationale and the wire layer it proves; the hash is rechecked on every test run.

| Family | Source (retrieved 2026-11-27) | SHA-256 of bytes | What it proves |
|---|---|---|---|
| Solana | [Solana Foundation `sendTransaction` docs](https://solana.com/developers/docs/rpc/http/sendtransaction) | `47fe51f4b4f3b4a3a8ae097acde3b85950711c1c80915d2681998744b55db784` | A real signed legacy message: signature count, 64-byte signature, message header/keys/blockhash |
| EVM | [Ethereum Execution APIs `eth_sendRawTransaction`](https://ethereum.github.io/execution-apis/api/methods/eth_sendRawTransaction/) | `f3d563ea8d82ce1873a3a5a631ad3e3688f35d7dac5ac70a56b7a7cf8093a172` | A real EIP-155 legacy RLP transaction with `v = 27` and 32-byte `r`/`s` |
| TON | [TON community TL-B test corpus](https://github.com/ton-community/tlb-test-corpus/blob/main/tlb-corpus.ts) | `cf80466e3313a26edb8d9ef0f635e3bc69b73a9a5326f5e360d32c8ac074817d` | A real single-cell BoC (`b5ee9c72`, one cell, uint32 42, CRC32) — framing only, not a signed external message |
| Sui | [Mysten Labs SDK raw-signer test `TX_BYTES`](https://github.com/MystenLabs/sui/blob/f3b5fdd73da64a0df65fb4323471512b0f57ec4d/sdk/typescript/test/e2e/raw-signer.test.ts) | `1456d85424a3f627b992986083958d0e23f7e0bc73e01518d7d5ed91d0bf30a0` | Real BCS `TransactionData`; its published digest is reproduced as blake2b(`intent ‖ txBytes`). The test file's illustrative seed phrases and keys are deliberately not copied |
| Cosmos | [CometBFT RPC spec `tx`](https://docs.cosmos.network/cometbft/latest/spec/rpc/Rpc-Spe) | `d70952032620cc4e2737eb8ac379806359d8e0b17b0488f627997a0b043abded` | A real committed `TxRaw`; `sha256(bytes)` equals the documented `txhash` |

These fixtures are public transaction/test data, not captures of user funds, and none
contains a private key, seed, passphrase or credential. They are deliberately narrow: the
decoders parse documented subsets, and a real signed transaction is not a binding payload.
The constructed negative vectors in `tests/adapters/test_chain_decoders.py` remain labelled
as constructed and are not presented as captured chain evidence.

### 2.3 Reviewed parsing libraries

Candidate libraries were reviewed against the licence inventory
(`tools/check-licences.py`) and the signing/custody tripwire
([DEPENDENCIES §4](DEPENDENCIES.md#4-packages-that-sign-or-custody-funds)):

| Library | Licence | Verdict |
|---|---|---|
| `rlp` (pyrlp 5.0.0), `eth-utils` 6.0.0 | MIT | Pure serialization/hashing; safe under the rule, but adding them changes both hashed Python locks, needs an installed metadata declaration for the licence gate and a new advisory verdict. Not added in this field; the hand-rolled strict RLP reader is pinned by the real Execution-APIs fixture above |
| `solders` 0.29.0, `eth-account` 0.14.0, `web3` 8.0.0, `pytoniq` 0.1.43, `pysui` 1.5.1, `cosmpy` 0.12.2 | Apache-2.0 / MIT | Each exposes keypairs, wallets or transaction signing; refused by the custody tripwire regardless of permissive licence |
| `py-solana-xray` | unverifiable | No stable PyPI artefact or licence declaration; cannot be reviewed |

A library will be added only when it removes wire-format risk without touching custody and
its pin, licence and advisory evidence can be updated in one scoped change. No signing or
custody library was added, and no gate was bypassed.

### 2.4 Read-only confirmation/status and the reconciliation seam

`chains/status.py` declares one state set — `pending`, `confirmed`, `failed`, `unknown` —
with one validator (`normalize_confirmation`), one writer (`Confirmation`) and an explicit
next action per state in `CONFIRMATION_ACTIONS`. Legacy spellings (`success`, `finalized`,
`reverted`, `processed`, blank) are read through `normalize()`; new writes use the canonical
token only. This is a read-only observation vocabulary, not a second lifecycle: the outbox
owns transitions of a claim. Readers are read-only: a null receipt or an unindexed hash is
`pending`, a definite on-chain error is `failed`, an RPC error or an unreadable schema is
`unknown`, and no outcome carries a quantity, price, fee or `Fill`. Request shapes follow
the documented Solana `getSignatureStatuses`, EVM `eth_getTransactionReceipt`, TON v2
`getTransactions`, Sui `sui_getTransactionBlock` and CometBFT `tx` schemas; TON needs the
account address as public context and only proves inclusion by a hash match (its JSON
schema has no failure flag, so a non-match is never reported as `failed`). All tests use
recorded public response shapes through `httpx.MockTransport`.

`services/executor` exposes `ChainConfirmationVenue`, the seam the existing outbox can call.
It maps a failed chain transaction to `not_found` (never filled), and maps `confirmed` to
`unknown`: a chain hash proves settlement, not the executed amount the ledger needs. The
claim therefore stays pending until a receipt's transfers are bound to the order.
`Venue.submit` and any `Fill` conversion remain undone, so live readiness is still gated.

The **sell-side quote is mandatory** even when buying: an entry with no executable exit
is not an entry. The router enforces this with its full-size exit check
(`QuoteRouter._has_exit` in `packages/adapters/src/ezchain_adapters/quotes.py`), applied
to every executable rung before a buy is accepted.

Paper mode uses the same venue adapter with a simulated broadcaster that fills at the
quoted effective price. If a venue cannot quote, paper rejects too. No synthetic price.

## 3. Target support matrix

Tier = verification level, not ambition.

**Verified today:** the `paper quote` column is filled per chain from the keyless quote
adapters in `packages/adapters` (ADR-0025). Quotes are verified against the live provider
and recorded as fixtures in `tests/adapters/fixtures/`; nothing here is a live-execution
tier. Balance, transfer, receipt and reorg handling are still targets — no
`services/guard` or `services/executor` signing path exists. A row may only be promoted
when its adapter contract suite passes against live mainnet and the recorded fixtures (§5).
Until then the UI must not present it as tradeable beyond paper mode.

- **T1** — adapter contract tests pass against live mainnet and recorded fixtures; swaps,
  balances, transfers, confirmations, reorg handling; used in paper and live.
- **T2** — read + watch-only + transfers verified; swaps via an aggregator in beta.
- **T3** — read-only balances/positions; no execution.
- **Planned** — interface designed, no adapter.

| Chain | Family | Target tier | Verified today | Target execution | Notes |
|---|---|---|---|---|---|
| Solana | SVM | T1 | paper quote + paper fill | Jupiter, Raydium, PumpSwap | first-class; rent/ATA accounting |
| Ethereum | EVM | T1 | paper quote + paper fill | Uniswap v3/v4, 1inch | KyberSwap primary; L1 gas model |
| Base | EVM | T1 | paper quote + paper fill | Uniswap, Aerodrome | OP-stack L1 data fee in `fee_usd` |
| Arbitrum One | EVM | T1 | paper quote + paper fill | Uniswap, Camelot | L2 sequencer feed |
| Polygon PoS | EVM | T1 | paper quote + paper fill | Uniswap, QuickSwap | |
| BNB Chain | EVM | T1 | paper quote + paper fill | PancakeSwap | USDC is 18-decimal here |
| Optimism | EVM | T2 | paper quote + paper fill | Uniswap, Velodrome | `l1FeeUsd` folded into `fee_usd` |
| Avalanche C | EVM | T2 | paper quote + paper fill | Trader Joe | native USDC `0xB97E…48a6E` |
| Linea | EVM | T2 | paper quote + paper fill | Uniswap/1inch | KyberSwap primary, LI.FI fallback |
| Scroll | EVM | T2 | paper quote + paper fill | Uniswap/1inch | LI.FI only (75 req / 2 h) |
| zkSync Era | EVM | T2 | paper quote + paper fill | Uniswap/1inch | LI.FI only |
| Mantle | EVM | T2 | paper quote + paper fill | Uniswap/1inch | LI.FI only |
| Blast | EVM | T2 | `paper_mark` only | — | no keyless quote source |
| Cronos | EVM | T2 | paper quote + paper fill | VVS/Uniswap | LI.FI only |
| Celo | EVM | T2 | paper quote + paper fill | Uniswap | LI.FI only |
| Metis | EVM | T2 | paper quote + paper fill | Netswap/Uniswap | LI.FI only, m.USDC stable |
| Fantom | EVM | T2 | `paper_mark` only | — | chain sunset in favour of Sonic |
| Berachain / Sonic | EVM | Planned | — | — | EVM adapter; KyberSwap quotes Sonic live |
| Sui | Move | T2 | paper quote + paper fill | Cetus, Bluefin | Cetus `deviation_ratio` is the impact |
| Aptos | Move | T2 | `paper_mark` only | Pancake/Aptos | Panora is key-gated |
| TON | TVM | T2 | paper quote + paper fill | STON.fi, DeDust | USDT jetton stable; wallet v5 pending |
| Tron | TVM | T2 | `paper_mark` only | SunSwap | no HTTP quote endpoint exists |
| Bitcoin | UTXO | T2 read/transfer | — | — (no native swap) | xpub watch, PSBT via hardware |
| Osmosis | Cosmos SDK | T3 | paper quote + paper fill | Osmosis | SQS router; denom decimals are a table |
| Cosmos Hub / Injective / Neutron | Cosmos SDK | T3 | — | Osmosis (T2 planned) | IBC awareness |
| NEAR | NEAR | T3 | `paper_mark` only | Ref Finance (planned) | no HTTP quote endpoint exists |
| Moonbeam | EVM | T3 | `paper_mark` only | — | no keyless quote source |
| Starknet | Cairo | T3 | — | JediSwap (planned) | |
| Cardano | UTXO/eUTXO | Planned | — | — | |
| Lightning | LN | Planned | — | — | |

**Honesty rule:** once adapters exist, the UI reads the matrix from the registry. A chain
shown as T2 says "beta" next to execution; T3 disables the trade button with the reason.
A chain with no adapter says "not implemented", not "T1".

`paper quote + paper fill` means the API quotes the chain through its ladder against the
live provider and books the fill in the paper ledger; it is **not** a live-execution tier.
A `paper_mark` row keeps the ADR-0020 behaviour: live browsing plus a capped ($250 buy)
paper fill at the best DexScreener pair price, with unknown impact. `live_execute` is
`false` everywhere, and no chain is T1 until the executor and live contract suite exist.

Each chain's ladder, its pricing stable and its primary provider are data in
`packages/adapters/.../registry.py` and are served at `/api/v1/chains`
(`capabilities.quote_kind`, `capabilities.quote_providers`). The badge is the head of the
ladder, so it cannot claim a provider the chain does not use.

### 3.1 T1 plan — executable depth, not listings

The reason a chain gets a real venue adapter first is depth that a quote can actually exit,
not the presence of a registry row. The registry has 25 entries; that number is browsing
coverage. Seven concrete keyless quote clients are built — `JupiterClient`, `KyberSwapClient`,
`VeloraClient`, `LifiClient`, `CetusClient`, `StonfiClient` and `OsmosisSqsClient`, all wired
in `apps/api/src/ezchain_api/main.py:116-127`. Eighteen registered chains have an executable
ladder; seven have only the capped `paper_mark` (§3.2). No concrete adapter implements the
build/sign/submit `VenueAdapter` surface of §2 or the executor's `Venue` protocol: the only
`Venue` implementation is `FakeVenue` in
`services/executor/src/ezchain_executor/venue.py:43`, an in-memory test double. Real venue
depth is therefore a plan, not a shipped capability.

The first real adapters go to the six registry T1 rows, in this order of justification:
**Solana, Ethereum, Base, Arbitrum One, Polygon PoS, BNB Chain**. These are the chains whose
ladder head already has to survive the router's mandatory full-size sell quote
(`packages/adapters/src/ezchain_adapters/quotes.py:379-399`) against a live provider, with the
answer recorded in `tests/adapters/fixtures/` (`jupiter_quote_buy.json`,
`jupiter_quote_sell.json`, `kyberswap_base_buy.json`, `velora_base_buy.json`). That exit test is
the closest thing this repository has to a liquidity measurement, and a venue adapter that
cannot clear it would be an adapter to a market the platform must refuse to enter. A registry
listing, a seed token or a market row proves none of that.

No measured cross-chain liquidity ranking exists in `docs/evidence`: the recorded discovery
pass in §4.2 counts returned rows per chain but does not rank depth. The order above is a
plan built on the six chains with recorded executable quotes, not a claim that one is deeper
than another. A depth ranking across all 25 chains must be measured before any adapter beyond
these six is committed; producing it is a code and measurement task, not a docs change.

Each first adapter is gated, in order: a contract suite for `quote`, `build_swap`, `simulate`,
`submit` and `status` against live mainnet plus the recorded fixture; a paper fill through the
same surface that agrees with the adapter's quote; the fail-closed `NO_SELL_ROUTE` behaviour on
a token with no exit; and only then a §3 matrix promotion backed by the passing evidence. A
a docs-only promotion is rejected: the proposed ADR-0014 rule is that a row may be promoted
only on a passing contract suite. Until such a suite exists, no chain is T1 in the sense §2
defines.

### 3.2 Per-chain capability matrix (registry truth)

This table is derived from `packages/adapters/src/ezchain_adapters/registry.py`; the `Tier`
column is the registry's own `ChainMeta.tier`, not the aspirational target in §3. It must be
updated in the same change as the registry. `Quote head` is `chain.quote_kind`: a head of
`paper_mark` means no keyless executable quote exists for that chain. `Paper fill` is `yes`
for every registered chain because `apps/api/src/ezchain_api/services/paper.py:1936,1961`
prices through the one `QuoteRouter` built in `apps/api/src/ezchain_api/main.py:135-148` and
handed to `PaperOrderExecutor` at line 156 — the capped mark is the router's own last rung,
not a second paper pricing path. That is the same *quote* adapter surface as live quotes; the
`build_swap`/`submit` surface in §2 is not used by paper fills and has no concrete
implementation. `Live execute` is `no` for every row because `ChainMeta.live_execute` defaults
to `False` (`registry.py:71`) and nothing overrides it.

| Chain (registry id) | Tier | Quote head | Paper fill | Live execute | Native decode/build; broadcast | Venue tests (read-only confirmation) |
|---|---|---|---|---|---|---|
| Solana (`solana`) | T1 | jupiter | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Ethereum (`ethereum`) | T1 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Base (`base`) | T1 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Arbitrum One (`arbitrum`) | T1 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Polygon PoS (`polygon`) | T1 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| BNB Chain (`bsc`) | T1 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Optimism (`optimism`) | T2 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Avalanche C (`avalanche`) | T2 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Blast (`blast`) | T2 | paper_mark | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Mantle (`mantle`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Linea (`linea`) | T2 | kyberswap | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Scroll (`scroll`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| zkSync Era (`zksync`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Sui (`sui`) | T2 | cetus | yes | no | decode+build; signed-shape broadcast, gated on mainnet | binding/decode; keyless-broadcast refusal; legacy status read |
| Aptos (`aptos`) | T2 | paper_mark | yes | no | none | per-provider tests only |
| TON (`ton`) | T2 | stonfi | yes | no | decode+build; `sendBocReturnHash` submit receipt | binding/decode/broadcast; inclusion-only status test |
| Tron (`tron`) | T2 | paper_mark | yes | no | none | per-provider tests only |
| NEAR (`near`) | T2 | paper_mark | yes | no | none | per-provider tests only |
| Osmosis (`osmosis`) | T2 | osmosis_sqs | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Cosmos Hub (`cosmos`) | T2 | paper_mark | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Fantom (`fantom`) | T2 | paper_mark | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Cronos (`cronos`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Celo (`celo`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Moonbeam (`moonbeam`) | T2 | paper_mark | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |
| Metis (`metis`) | T2 | lifi | yes | no | decode+build; submit receipt only | binding/decode/broadcast tests |

The last column names what exists today, not a passing gate: per-provider tests are
`tests/adapters/test_jupiter.py`, `test_kyberswap.py`, `test_lifi.py`, `test_velora.py` and
`test_svm_move_cosmos_quotes.py`, with one recorded response per quote path in
`tests/adapters/fixtures/`. The binding/decode/broadcast tests added for §2.1 cover every
one of the five families, and §2.4 adds typed read-only status readers, but they are not an
execution contract suite: a status observation and a `submitted` receipt prove neither an
executed amount, a `Fill`, nor a journal entry. There is still no shared suite that every
venue adapter must pass; adding one that binds a real receipt to an order is a gap. Note also that
the §3 target tiers and the registry tiers differ for Osmosis, NEAR, Cosmos Hub and Moonbeam
(T3 as a target, T2 in `ChainMeta`'s default); the target column is aspirational and a change
to either column is a product decision, not a docs edit.

## 4. Per-chain data requirements

| Need | Source of truth |
|---|---|
| Token metadata (symbol, decimals, supply) | chain registry + on-chain, cached with TTL |
| Safety (mint/freeze authority, honeypot, taxes) | chain-specific scanners + simulation |
| Liquidity / price | venue adapter quotes + DEX indexers, always with source + age |
| Holders / concentration | indexer; `unknown` is allowed and displayed |
| Finality / confirmations | adapter `finality_seconds` + receipt polling |
| Fees | adapter estimate before signing; actual fee from receipt |

Mixed-unit hazards are rejected at the boundary (`docs/DOMAIN-MODEL.md` §2). A token with
unknown decimals is not tradeable.

### 4.1 New-pairs discovery (ADR-0047)

`GET /api/v1/discovery/new-pairs?chain=&limit=` is the Launches feed. It is a read model
over the same keyless DexScreener discovery pass the market monitor uses — no new table, no
migration, no ingestion.

- **Source and ToS posture:** the keyless public DexScreener API, the same source and terms
  already recorded for the markets feed (§7). No key, no scraping, no second provider.
- **One pass, bounded:** at most 200 candidate rows are read (`sort=new`), ranked, and cut
  at the requested `limit` (≤ 50). Every response echoes its bounds under `applied`.
- **One row per token:** the read is deduplicated on `(chain, base_token)`, keeping the
  deepest pool that can reach the table. A second pool of the same token never appears. The
  address is case-folded only on EVM, where hex has no case; SVM base58 mints are compared
  exactly, because `EPjFWdd5…` and `epjfwdd5…` are different tokens.
- **Floors:** a row is dropped, and counted under its own reason, when its mark is older
  than the mark budget or dated ahead of the clock, when its pair age is unknown, younger
  than `min_pair_age_minutes`, or older than the seven-day feed window, or when its
  liquidity is unknown or below `min_liquidity_usd`. The seven-day window is a module
  constant in `ezchain_api/services/discovery.py`; a `Settings` field for it is the
  follow-up. `coverage.dropped` carries the per-reason counts and `coverage.truncated`
  says the page was cut rather than the population ending. `coverage.chains` carries the
  adapter's own per-chain coverage (`pairs`, `no_pairs`, `no_seeds`, `error`, `timeout`),
  so a provider outage reads as `error` and not as an empty feed; it is `null` when no
  per-chain census was made.
- **Ranking:** newest pair first, deepest pool second; the pair address makes the order
  total, so the same read always renders the same table.
- **Provenance:** every displayed number carries the source it came from and the age of
  that observation. An unknown value is `null` with `known: false`, never a zero.
- **Fail-closed safety:** a row is `tradeable` only with *positive* safety evidence
  (`safety.status == "verified"`). No safety source exists in this build — the DexScreener
  adapter returns `SAFETY_UNKNOWN` — so **with the real adapter no row is ever tradeable**,
  and every row carries `safety: {status: "unknown", reason: "SAFETY_NOT_IMPLEMENTED",
  checked_at: null}` plus a `not_tradeable_reason`. Missing evidence is unknown, never a
  pass; the UI must not render a Trade action from this feed until a safety check exists.
- **Empty is a reason:** an empty read returns an empty list with a reason code
  (`NO_MARKET_ROWS` when nothing was returned, `NO_QUALIFIED_MOVERS` when every row was
  dropped), never a filler row.


### 4.2 Discovery coverage (measured 2026-10-22)

`/api/v1/markets?chain=all` fans out one token lookup per registered chain because
`/latest/dex/tokens` caps each response at 30 **pairs**, not 30 addresses. Seeds are
requested first, at most 12 hint addresses are added per chain, and four chains are in
flight at a time. Rows are ranked within each chain, then round-robined in registry order
before `limit`, so a low-liquidity chain is not dropped by a global ranking. The response
carries `coverage: [{chain, requested, returned, reason}]`; `reason` is `pairs`,
`no_pairs` (the provider was asked and returned nothing), `no_seeds` (no seed or hint
address existed, so nothing was asked and the population is unknown — never an empty
observation), `error` (the probe failed) or `timeout` (the pass hit its deadline), and
coverage counts are pre-slice. When `chain=all`, `ordered_by` is `chain_round_robin`; a
feed-wide search is `global_sort`; a single-chain read is `per_chain_sort` because it
ranks only inside that chain.

Every registered chain has seed tokens; the list is the single source in
`packages/adapters/src/ezchain_adapters/seeds.py` and this table names them:

| Chain | Seeds |
|---|---|
| Solana | wrapped SOL, USDC, BONK, JUP |
| Ethereum, Base, Arbitrum, Optimism | WETH, USDC |
| Polygon | WETH, USDC |
| BNB Chain | WBNB, USDT |
| Avalanche C | WAVAX, USDC |
| Blast | WETH, USDB |
| Mantle | WETH, WMNT |
| Linea, Scroll, zkSync | WETH, USDC |
| Fantom | WFTM, USDC |
| Cronos | WCRO, USDC |
| Celo | CELO, USDC |
| Sui | SUI, USDC |
| Aptos | APT, USDC |
| TON | native TON, USDT jetton |
| Tron | WTRX, USDT (TRC-20) |
| NEAR | wNEAR, USDC |
| Metis | METIS, m.USDC |
| Osmosis, Cosmos Hub, Moonbeam | `uosmo`, `uatom`, WGLMR contract |

Live read-only smoke (`chain=all&limit=100`, 2026-10-22): **22 of 25** registered chains
returned rows. Osmosis, Cosmos Hub and Moonbeam returned no indexed pairs and were reported
`no_pairs`, never zero. The Blast, Mantle, Linea, Scroll and zkSync seeds were verified in
the same pass. The Solana seeds are the canonical SPL mints, so Solana discovery no longer
depends on the promotional feeds; the measurement above predates that change and is kept
as the recorded pass.

## 5. Adding a chain (checklist)

1. New package `packages/adapters/chain_<name>` implementing `ChainAdapter`.
2. Registry entry with `ChainMeta`, capabilities and explorer templates.
3. Contract tests: address validation, balances, transfer build/simulate, receipt,
   reorg/late-event handling, unit declaration.
4. One venue adapter (or mark swaps unsupported).
5. Fixtures recorded from mainnet, checked in with provenance.
6. Matrix row + docs + UI chain picker (data-driven; no code change).
7. Paper-mode soak: 100 intents against recorded quotes.
8. ADR if the chain violates an assumption (e.g. account rent, object model).

No core PR may add a `if chain == ...` branch. If the abstraction does not fit, fix the
abstraction in its own PR first.

## 6. Chain registry (data, not code)

```yaml
- id: solana
  family: svm
  native_asset: SOL
  decimals: 9
  explorer: https://solscan.io/tx/{tx}
  tier: 1
  capabilities: { swap: true, rent: true, simulation: true, reorg_depth: 0 }
  rpc_providers: [helius, triton, public]
  venues: [jupiter, raydium, pumpswap]
  quote_providers: [jupiter]          # ordered fallback ladder
  quote_asset: { address: EPjF…Dt1v, symbol: USDC, decimals: 6 }
```

The registry is served at `/api/v1/chains` and is the only source for the UI, docs and
adapter selection. Providers are health-checked; a degraded provider moves traffic, it
does not fail the chain.

## 7. Quote ladders (keyless sources, verified 2026-10-22)

Each rung was exercised with `curl` before the adapter was written; one real response per
provider is recorded in `tests/adapters/fixtures/`. A chain's ladder is ordered: the router
takes the first rung that answers **and** can also sell the size it just bought.

| Chain | Ladder | Pricing stable | Limits and failure modes |
|---|---|---|---|
| Solana | jupiter | USDC mint | keyless 0.5 rps; `priceImpactPct` is a ratio |
| Ethereum, Base, Arbitrum, Polygon, BNB, Optimism, Avalanche | kyberswap → velora → lifi | USDC (USDC 18-dec on BNB) | KyberSwap 3 rps public and no impact field (derived from USD legs); Velora zeroed 1 bp partner fee; LI.FI 75 req / 2 h |
| Linea | kyberswap → lifi | USDC | Velora no longer lists Linea |
| Sonic (not registered) | kyberswap → velora → lifi | USDC | verified live; no registry entry yet |
| Mantle, Scroll, zkSync, Cronos, Celo, Metis | lifi | USDC (m.USDC on Metis) | LI.FI bakes a 0.25% fee into `toAmount`, declared in `fee_usd` |
| Sui | cetus | USDC | no documented rate limit; paced at 1 rps; gas in MIST, no USD price |
| TON | stonfi | USDT jetton | POST with query-string args; native TON is the pTON zero address |
| Osmosis | osmosis_sqs | USDC (IBC denom) | `tokenIn` is `amount+denom`; a denom outside the decimals table fails closed |
| Blast, Fantom, Moonbeam, Aptos, Tron, NEAR, Cosmos Hub | `paper_mark` | — | no keyless executable quote; ADR-0020 cap applies |

Provider guard rails, all per instance: token bucket at the documented rate, 10 s response
cache, and a 60 s circuit breaker opened by 5xx/530/HTML-WAF and by an attempt deadline the
router owns (a host that hangs instead of erroring), not by a `no route` answer. A call
cancelled for any other reason releases its half-open probe without counting a failure.
When every rung is down the order fails closed with `NO_ROUTE`; an executable chain never
silently falls back to a mark.
