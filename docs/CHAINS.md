# CHAINS — adapter model and support matrix

"All chains" is not a list of RPCs. It is a capability contract, a set of adapters and an
honest matrix of what is actually verified. This document defines all three.

## 1. Adapter contract

```python
class ChainAdapter(Protocol):
    chain: ChainMeta          # id, family, native_asset, decimals, finality, explorer
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
    limit_order: bool          # native or via venue adapter
    gasless: bool
    mempool: bool
    reorg_depth: int
    finality_seconds: int
    address_formats: tuple[str, ...]
    token_standards: tuple[str, ...]
    event_streams: tuple[str, ...]
    rent: bool                 # account rent / storage deposits
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

The **sell-side quote is mandatory** even when buying: an entry with no executable exit
is not an entry. This is the v3 lesson (`_ENTRY_MIN_SELL_BUY_RATIO`,
`_validate_executable_entry_quotes`), generalized to every venue.

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

### 4.1 Discovery coverage and browse population (measured 2026-10-23)

`/api/v1/markets?chain=all` fans out one token lookup per registered chain because
`/latest/dex/tokens` caps each response at 30 **pairs**, not 30 addresses. Seeds are
requested first, at most 12 hint addresses are added per chain, and four chains are in
flight at a time. Solana's seeds are its four canonical majors — wrapped SOL, USDC, BONK
and JUP — not a promotional-feed hint, so an empty profile/boost/takeover response cannot
erase the chain from an all-chain browse.

Rows are ranked within each chain before the registry-order round robin. The five sort
modes are `trending` (24h volume), `liquidity`, `new` (newest pair), `change_h24` and
`pair_age` (oldest listing first). Unknown values always sort last and stay `null`; the
pair address is the final tie-breaker, so two pages of one snapshot cannot swap equal
rows. When `chain=all` has no search query, `sort_semantics` is
`per_chain_round_robin`: each chain is ranked independently and the chains are
interleaved, which is not one global ranking. A search or a single-chain request is
`global_sort`. `ordered_by` remains as the older label for clients that do not read
`sort_semantics`.

The API asks the adapter for the whole bounded population (up to 1000 rows) and applies
`q`, `dex`, liquidity, volume, 24h-change and pair-age filters before `offset`/`limit`.
`total` is the filtered population, not the page, and `next_offset` is present only while
another page exists. Every response carries `population`:

| Field | Meaning |
|---|---|
| `chains_scanned` | chains probed in this pass; `null` for a global search |
| `addresses_requested` | seed + hint addresses requested; `null` for a global search |
| `rows_discovered` | adapter population before API filters |
| `rows_after_filters` | filtered population used for `total` |
| `rows_returned` | rows on this page |
| `truncated` | true only when the adapter's own row cap cut the population |
| `sort`, `sort_semantics` | the applied order, including the round-robin caveat |

An active filter never invents a value: a row whose field is unknown is excluded and
counted under `applied_filters.unknown_excluded`. The supported decimal filters are
bounded to `±1e18` and at least `1e-18` (zero is allowed); a larger exponent is rejected
with `422 INVALID_FILTER` before any provider work, so a filter cannot be echoed as a
megabyte of digits.

`/api/v1/markets/stored` is the persisted counterpart. It now returns an object
(`{rows, as_of, offset, limit, total, has_more, next_offset}`) rather than a bare array.
Rows are ordered by `liquidity_usd DESC NULLS LAST, observed_at DESC, token_address`, and
the same offset/`next_offset` contract applies. Migration `0009_market_snapshot_sort`
adds the matching unprefixed (all-chain) and chain-prefixed indexes.

Live read-only discovery smoke (`DexScreenerClient.discover_markets(chain="all",
limit=1000)`, 2026-10-23): **22 of 25** registered chains returned rows, with **629**
discovered rows from **68** requested addresses and **0** probe errors. Osmosis, Cosmos
Hub and Moonbeam returned no indexed pairs and were reported `no_pairs`, never zero.
Solana returned 30 rows, led by wrapped SOL and USDC by liquidity. The first 100
round-robined rows covered all 22 chains, and the measured population stayed below the
1000-row discovery cap (`truncated: false`). The Blast, Mantle, Linea, Scroll and zkSync
seeds were verified in the same pass.


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
cache, and a 60 s circuit breaker opened by 5xx/530/HTML-WAF, not by a `no route` answer.
When every rung is down the order fails closed with `NO_ROUTE`; an executable chain never
silently falls back to a mark.
