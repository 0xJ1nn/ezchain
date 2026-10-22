# CURRENT TASK

**Objective:** P0 — live paper slice: multi-chain live browsing, paper execution and
user-defined strategies. No keys, no live money.

**Scope in:** `apps/api`, `services/engine` (inline), `packages/core-py`,
`packages/adapters`, `apps/web` markets/token/positions/strategies, Postgres schema,
Playwright and pytest, docs.
**Scope out:** live signing, executor/vault, wallet/automation/system pages, fiat,
copy-trading, pump.fun live trading.

**Acceptance:** a user can browse live markets across the registry, place a paper buy/sell,
see the ledger/position/PnL, create a strategy and watch it place paper orders; unknown,
stale and no-route states fail closed; `bash tools/ci.sh` is green.

**Verification:** `bash tools/ci.sh`, `pnpm --filter @ezchain/web test`, `.venv/bin/pytest`,
`.venv/bin/ruff check`, `.venv/bin/mypy --strict`, live smoke in the PR.

**State:** P0 is merged to `main` (PR #51): adapters #53, paper #54, engine #52, live web
#55, paper/strategy web #56. Live smoke passes: markets, token, paper buy/sell, positions
and a DCA strategy order on Solana; Base USDC paper-mark buy also passes. `bash tools/ci.sh`
is green on `main` (203 Python + 31 Playwright tests). Dashboard KPI band now reads the
paper account; its equity chart and open-position tiles remain fixture-preview.

**Next:** P0.7 dashboard history/tiles (#57), engine boundary (#58), EVM venue (#59) and
the evidence pack (#60). Then M1 wallets (#10) on a new branch. Do not start live execution
or key custody without an ADR and the paper gate.
