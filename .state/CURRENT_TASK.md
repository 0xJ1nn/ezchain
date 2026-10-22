# CURRENT TASK

**Objective:** PAPER stack with production operations. No keys, no live money.

**Scope in:** `apps/api`, `services/engine` (inline), `packages/core-py`,
`packages/adapters`, `apps/web`, Docker/Caddy/Compose, Alembic, auth, observability,
`tools/stress`, tests and docs.
**Scope out:** live signing, executor/vault, wallet/automation/system pages beyond
labelled fixtures, fiat, copy-trading, pump.fun live trading.

**State (2026-10-22):** merged to `main` through PR #89.

- Market eyes: continuous inline scan, 22/25 chains covered live, overview/stored,
  all-chain fan-out/round-robin, position marks refreshed.
- Paper: buy/sell, idempotent orders, append-only ledger, reconciliation, kill switch,
  strategy CRUD/scheduler, `ORDER_REJECTED` truth, pair-age and sell-side risk fixes.
- Ops: Alembic 0001–0003 (fresh + legacy), bearer auth with Next proxy and CSRF guard,
  `/healthz`/`/readyz`/`/metrics`, JSON access logs, runtime-only API image deps.
- Deployment: Dockerfiles, runnable PAPER Compose stack, Caddy edge, Prometheus profile.
  Docker build/up is **not verified** (no container runtime on the authoring machine).
- Stress: smoke/soak/idempotency/chaos pass; `--file-issues` filed #71, closed by #72.
- Web: dashboard fixtures badged, DCA/dip vocabularies aligned, Playwright guard.

**Verification:** `bash tools/ci.sh` green (398 Python + 36 Playwright);
`EZCHAIN_TEST_DATABASE_URL=<test db> bash tools/check-migrations.sh` green;
auth-enabled live smoke on Postgres passed. Full matrix and gaps:
[`docs/PRODUCTION-READINESS.md`](../docs/PRODUCTION-READINESS.md).

**Next:** supervised Docker `up --build` on a host with Docker; per-user auth/RBAC;
dashboard live history/attribution/wallets (#57); EVM venue (#59); executor/vault only
after ADRs and the paper gate. Do not claim live execution works.

**Known limits:** one deployment bearer token (not per-user), unauthenticated `/metrics`,
22/25 chain coverage (Osmosis/Cosmos/Moonbeam have no DexScreener pairs), dashboard
fixtures, no Timescale/tick retention, non-Solana fills are `paper_mark`.
