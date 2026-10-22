# PR log

| Date | PR | Scope | Gates |
|---|---|---|---|
| 2026-10-20 | #1 | runbook + design state close; local gate replaces unavailable Actions | `tools/ci.sh` green |
| 2026-10-20 | #2 | performance and attribution screen | `tools/ci.sh` green |
| 2026-10-20 | #3 | design-drop closeout | `tools/ci.sh` green |
| 2026-10-22 | #5 | ezCORE governance port: canonical `project.md`, agent stubs, OpenCode config | `tools/ci.sh` green |
| 2026-10-22 | #20 | shared display formatters for fixture view models | `tools/ci.sh` green |
| 2026-10-22 | #16 | M1 web shell + dashboard, tokens, Playwright render gate | `tools/ci.sh` green (lint, typecheck, build, 4 tests) |
| 2026-10-22 | #18 | positions page + 9-zone tile in four states | `tools/ci.sh` green (lint, typecheck, build, 6 tests) |
| 2026-10-22 | #21 | markets screener with fail-closed fixture states | `tools/ci.sh` green (9 tests) |
| 2026-10-22 | #22 | token page with quote panel and fail-closed states | `tools/ci.sh` green (14 tests) |
| 2026-10-22 | #23 | mobile overflow fix for the position tile | `tools/ci.sh` green |
| 2026-10-22 | #24 | markets render evidence (wireframe comparison + mobile) | docs-only; links gate green |
| 2026-10-22 | #37 | docs: target capability matrix, roadmap and ADR draft; M1 status claims | `tools/ci.sh` green (14 tests) |
| 2026-10-22 | #38 | cross-page BONK constants and overdue PR log | `tools/ci.sh` green (15 tests) |
| 2026-10-22 | #40 | M1 state close-out: token evidence list, next-work pointer | `tools/ci.sh` green (15 tests) |
| 2026-10-22 | #41 | `pnpm shots` now runs the full Playwright suite | `pnpm shots` 15 passed |
| 2026-10-22 | #53 | P0.1 DexScreener multi-chain discovery + Jupiter contract tests | 125 pytest, ruff/mypy green |
| 2026-10-22 | #54 | P0.2 paper ledger, orders, positions, risk | 31 pytest, ruff/mypy, full `tools/ci.sh` green |
| 2026-10-22 | #52 | P0.3 declarative strategy evaluator + scheduler | 44 pytest, ruff/mypy green |
| 2026-10-22 | #55 | P0.4 live markets/token web + paper ticket | 23 Playwright, typecheck/lint/build green |
| 2026-10-22 | #56 | P0.5 paper positions + strategy builder web | 31 Playwright, typecheck/lint/build green |
| 2026-10-22 | #51 | P0 live paper slice: API, adapters, ledger, engine, live web | `tools/ci.sh` green (203 pytest, 31 Playwright); live Jupiter/paper smoke |
| 2026-10-22 | #66 | risk: sells never blocked by size/paper-mark/daily-loss limits | `pytest` 212, ruff/mypy green; live buy/sell smoke |
| 2026-10-22 | #68 | market eyes: fan-out/round-robin, 22/25 chains, overview/stored, monitor | 267 pytest; live Postgres scan and 22-chain browse |
| 2026-10-22 | #69 | paper hardening: delete cascade, reconciliation, kill switch, UTC, honest run log | 239 pytest; Postgres smoke |
| 2026-10-22 | #70 | stress harness: smoke/soak/idempotency/chaos, dedupe issue filing | 48 stress tests; live runs PASS; filed #71 |
| 2026-10-22 | #72 | engine: rejected order logs `hold`/`ORDER_REJECTED`, not a fake trade | 295 pytest; stress reproduction passes |
| 2026-10-22 | #74 | web: DCA/dip vocabulary matches the validator | 33 Playwright; lint/typecheck/build |
| 2026-10-22 | #75 | risk: documented 10-minute pair-age gate enforced for buys | 304 pytest; live Base/Solana fills |
| 2026-10-22 | #77 | web: every dashboard fixture panel badged | 34 Playwright; wireframe/delta updated |
| 2026-10-22 | #78 | ops: Alembic, bearer auth + CSRF proxy, health/readiness/metrics, genesis single-writer | 393 pytest, 35 Playwright, fresh/legacy DB check |
| 2026-10-22 | #81 | infra: Dockerfiles + runnable PAPER Compose/Caddy/Prometheus | static YAML + full `tools/ci.sh`; Docker build/up not verified |
| 2026-10-22 | #83 | migrations: legacy fill-order unique constraint | 394 pytest; fresh/legacy `alembic check` |
| 2026-10-22 | #80 | stress: bearer token support for auth-enabled API | 51 stress tests; auth-enabled live PASS, token redacted |
| 2026-10-22 | #84/#85 | web: Playwright build guard (reverted `.next-test`) | 36 Playwright with/without dev server |
| 2026-10-22 | #87 | money docs + dead cents/fee helpers removed | 397 pytest; no code-path change |
| 2026-10-22 | #89 | monitor: refresh marks for open positions | 398 pytest; quote-side mark test |
