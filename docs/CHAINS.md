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

**Verified today: none.** Every row below is a target. No adapter package exists yet;
`packages/` contains only `ui`, and there is no `services/ingest` or `services/executor`.
A row may only be promoted when its adapter contract suite passes against live mainnet
and the recorded fixtures (§5). Until then the UI must not present it as tradeable.

- **T1** — adapter contract tests pass against live mainnet and recorded fixtures; swaps,
  balances, transfers, confirmations, reorg handling; used in paper and live.
- **T2** — read + watch-only + transfers verified; swaps via an aggregator in beta.
- **T3** — read-only balances/positions; no execution.
- **Planned** — interface designed, no adapter.

| Chain | Family | Target tier | Verified today | Target execution | Notes |
|---|---|---|---|---|---|
| Solana | SVM | T1 | paper quote + paper fill | Jupiter, Raydium, PumpSwap | first-class; rent/ATA accounting |
| Ethereum | EVM | T1 | — | Uniswap v3/v4, 1inch | L1 gas model, EIP-1559 |
| Base | EVM | T1 | — | Uniswap, Aerodrome | OP-stack finality |
| Arbitrum One | EVM | T1 | — | Uniswap, Camelot | L2 sequencer feed |
| Polygon PoS | EVM | T1 | — | Uniswap, QuickSwap | |
| BNB Chain | EVM | T1 | — | PancakeSwap | |
| Optimism | EVM | T2 | — | Uniswap, Velodrome | |
| Avalanche C | EVM | T2 | — | Trader Joe | |
| Linea / Scroll / zkSync / Mantle / Blast | EVM | T2 | — | Uniswap/1inch | shared EVM adapter, per-chain config |
| Berachain / Sonic | EVM | Planned | — | — | EVM adapter |
| Sui | Move | T2 | — | Cetus, Bluefin | object model, sponsored tx |
| Aptos | Move | T2 | — | Pancake/Aptos | |
| TON | TVM | T2 | — | STON.fi, DeDust | wallet v5, jetton |
| Tron | TVM | T2 | — | SunSwap | energy/bandwidth fees |
| Bitcoin | UTXO | T2 read/transfer | — | — (no native swap) | xpub watch, PSBT via hardware |
| Cosmos Hub / Osmosis / Injective / Neutron | Cosmos SDK | T3 | — | Osmosis (T2 planned) | IBC awareness |
| NEAR | NEAR | T3 | — | Ref Finance (planned) | |
| Starknet | Cairo | T3 | — | JediSwap (planned) | |
| Cardano | UTXO/eUTXO | Planned | — | — | |
| Lightning | LN | Planned | — | — | |

**Honesty rule:** once adapters exist, the UI reads the matrix from the registry. A chain
shown as T2 says "beta" next to execution; T3 disables the trade button with the reason.
A chain with no adapter says "not implemented", not "T1".

`paper quote + paper fill` means the P0 API has been exercised against live mainnet
quotes and the paper ledger; it is **not** a live-execution tier. Every other chain is
`paper_mark` only: live browsing plus a capped paper fill at the best DexScreener pair
price, with `live_execute: false`. No chain is T1 until the executor and live contract
suite exist.

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

### 4.1 Discovery coverage (measured 2026-10-22)

`/api/v1/markets?chain=all` fans out one token lookup per registered chain because
`/latest/dex/tokens` caps each response at 30 **pairs**, not 30 addresses. Seeds are
requested first, at most 12 hint addresses are added per chain, and four chains are in
flight at a time. Rows are ranked within each chain, then round-robined in registry order
before `limit`, so a low-liquidity chain is not dropped by a global ranking. The response
carries `coverage: [{chain, requested, returned, reason}]`; `reason` is `pairs` or
`no_pairs`, and coverage counts are pre-slice.

Live read-only smoke (`chain=all&limit=100`, 2026-10-22): **22 of 25** registered chains
returned rows. Osmosis, Cosmos Hub and Moonbeam returned no indexed pairs and were reported
`no_pairs`, never zero. The Blast, Mantle, Linea, Scroll and zkSync seeds were verified in
the same pass.

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
```

The registry is served at `/api/v1/chains` and is the only source for the UI, docs and
adapter selection. Providers are health-checked; a degraded provider moves traffic, it
does not fail the chain.
