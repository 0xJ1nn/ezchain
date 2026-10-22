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

**State:** shell + dashboard merged in #16; positions in #18; markets in #21; token page
in #22; mobile overflow fix in #23; markets evidence in #24. Issue #8 stays open until all
tile states exist. #9 stays open until the deferred watchlist and real filters land (#33).

**Next:** M1 close-out (remaining tile states and system pages, deterministic local gate).
M2 starts with `services/ingest`, the `MarketEvent` contract and persistence; see #25–#28.
Do not start M3 execution before M2 data and reason codes exist.
