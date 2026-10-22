# CURRENT TASK

**Objective:** M1 — stand up the ezCHAIN web UI from the wireframe: shell, dashboard, the
signature position tile, markets screener and token page.

**Scope in:** `apps/web` shell, dashboard, positions page + tile states, generated design
tokens, fixture view models, Playwright smoke/screenshot checks, docs.
**Scope out:** API, engine, executor, ingest, auth, real market data, order execution,
remaining wireframe pages (tracked as separate M1 issues).

**Acceptance:** dashboard and positions render at 1440 px without external requests;
the shell is navigable; every unimplemented route is explicit; `bash tools/ci.sh` is
green; screenshot comparisons are attached to the PR.

**Verification:** `pnpm -r lint`, `pnpm -r typecheck`, `pnpm -r test`,
`node tools/build-tokens.mjs --check`, `bash tools/ci.sh`.

**State:** shell + dashboard #16; positions #18; markets #21; token #22; mobile overflow
#23; evidence #24; docs capability audit #37; cross-page constants #38. Issue #9 is
closed; #8 stays open until all tile states exist. The M1 gate is green (15 tests).

**Next:** M1 close-out — wallets (#10), automations (#11), strategies/signals (#12),
risk/alerts (#13), system pages (#14), remaining tile states (#8), then a deterministic
gate (#36). M2 starts with `services/ingest`, the `MarketEvent` contract and persistence
(#25–#28). Do not start M3 execution before M2 data and reason codes exist.
