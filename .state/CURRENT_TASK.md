# CURRENT TASK

**Objective:** M1 — stand up the ezCHAIN web UI from the wireframe: shell, dashboard and
the signature position tile.

**Scope in:** `apps/web` shell, dashboard, positions page + tile states, generated design
tokens, fixture view models, Playwright smoke/screenshot checks, docs.
**Scope out:** API, engine, executor, ingest, auth, real market data, order execution,
remaining wireframe pages (tracked as separate M1 issues).

**Acceptance:** dashboard and positions render at 1440 px without external requests;
the shell is navigable; every unimplemented route is explicit; `bash tools/ci.sh` is
green; screenshot comparisons are attached to the PR.

**Verification:** `pnpm -r lint`, `pnpm -r typecheck`, `pnpm -r test`,
`node tools/build-tokens.mjs --check`, `bash tools/ci.sh`.

**State:** shell + dashboard merged in #16; positions in progress on `feat/web-positions`.
