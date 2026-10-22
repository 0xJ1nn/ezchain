# CURRENT TASK

**Objective:** a trader-complete paper stack, then a safe live mode. No placeholders, no
dead controls, no unverified claims.

**Working branch:** `integ/trader-superpower` (integration; `main` is the release branch).

## State (2026-10-23)

Merged into integration, gate green before the new slices:

- Multi-chain quote ladders (KyberSwap, Velora, LI.FI, Jupiter, Cetus, STON.fi, Osmosis
  SQS) with impact/fee/route truth and per-chain fail-closed ladders.
- Graph strategy core: schema, catalogue, validation, indicators, evaluator, backtester.
- Visual strategy builder (fixture + live client) in the web app.
- Stress harness v2: ledger truth, matrix, fuzz, race, kill-switch and report tests.
- Public repo hygiene: internal orchestration moved out of the repository; generated
  stubs and link/secret scanners operate on the public file set only.

In progress / not merged yet:

- Graph HTTP API (`/graph/catalogue`, `/graph/validate`, `/graph/dry-run`, `/backtests`,
  strategy performance) and graph scheduler wiring.
- Paper read models (ledger, performance, attribution, limits, alerts, CSV exports).
- Real operator pages for every sidebar route; removal of the placeholder catch-all.
- Live executor/vault, live intents and live UI: **not built**. `live_execute` is false
  everywhere.

## Next, in order

1. Merge graph API and read models; run the full Python + Playwright gate.
2. Merge operator pages; no placeholder route, no dead button; run the live paper E2E.
3. Rewrite the dashboard against live paper/account/strategy reads (remove fixture panels
   and inert controls).
4. Implement the executor/vault/live-intent foundation with fake-venue tests, then the
   Solana/EVM venue adapters and simulation. Real-fund broadcast remains an explicit
   owner-only action and must be labelled unverified until a funded pilot is recorded.
5. Run the compulsory reviewer PASS and full stress (`bash tools/verify.sh --live`) before
   merging to `main`.

## Known limits

- One deployment bearer token; no per-user auth/RBAC.
- `/metrics` is unauthenticated; keep it off the public internet.
- Market coverage is 22/25; chains without DexScreener pairs stay `no_data`, never zero.
- Non-Solana paper fills use the chain quote ladder, or a capped `paper_mark` where no
  keyless provider exists.
- No strategy PnL attribution until the read-model endpoint lands.
- No live keys, signing, broadcast or live ledger.
- Maintainer-local notes are not part of this repository.
