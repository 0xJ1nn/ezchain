# CURRENT TASK

**Objective:** M1 — stand up the ezCHAIN web UI from the wireframe, starting with the
global shell and dashboard.

**Scope in:** `apps/web` shell + dashboard, generated design tokens, fixture view models,
Playwright smoke/screenshot checks, docs that describe the new state.
**Scope out:** API, engine, executor, ingest, auth, real market data, order execution,
remaining wireframe pages (tracked as separate M1 issues).

**Acceptance:** dashboard renders at 1440 and 390 px without external requests; the
shell is navigable; every unimplemented route is explicit; `bash tools/ci.sh` is green;
screenshot comparison is attached to the PR.

**Verification:** `pnpm -r lint`, `pnpm -r typecheck`, `pnpm -r test`,
`node tools/build-tokens.mjs --check`, `bash tools/ci.sh`.

**State:** in progress on `feat/web-shell-dashboard`.
