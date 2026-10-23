# LEARNINGS — what the four bots taught us

Sources: `0xJ1nn/solbot` (v1→v9), `solbot_v2`, `solbot_v3` (+ `solbot_v3_live`),
`pumpbot`, plus `solbot-tile-lab`. Static analysis of the repositories, October 2026.
Line references are to those repos, not to ezCHAIN.

---

## 1. The short version

| | solbot v1 | solbot v2 | solbot v3 | pumpbot |
|---|---|---|---|---|
| Shape | Flask monolith, ~19.5k LOC + 6.4k-line template | FastAPI "9 services" in one process, ~60–80k LOC, React | Same lineage, ~200–300k LOC, vanilla JS, systemd | FastAPI + engine, ~40k LOC, 100 root modules, 322 MB repo |
| Store | SQLite (Postgres only decorative) | Postgres, 100 migrations, ~30 destructive | Postgres, 254 migrations, no fresh-DB path | SQLite WAL, 12 silent ALTERs |
| Chains | Solana only | Solana only | Solana only | Solana only |
| Wallets | 1 keypair, env | 1 keypair, imported in routers | 1 keypair; plaintext `wallets.json` scripts | paper wallet + burner |
| Strategy | hard-wired, 8-phase rules | 7 exit engines, ML advisory | 40 lanes, ML must approve | ML-only lane + lab scripts |
| Real money | unproven | unproven (paper) | unproven (paper) | paper only |
| Honest result | discovery broken (invalid mints) | −7.83 SOL/week paper | 497 closed, 18.7% WR, −2.76%/trade | exit-law ceiling: 36.6% WR, payoff 0.63 |
| Best asset | auth stack, rate limiter, tile data | ledger, repos, event bus, audits | exact-quote contract, tile, entry-moment model | ledger identity, paper mirror, measurement discipline |
| Worst tell | one 320 KB HTML file, no tests | 5.8k-line service, wipe migrations | 346 KB config, dead lanes, docs/code drift | research tree shipped as product |

**They all failed at the same place:** entry selection on a distribution where ~80% of
candidates are dead, with an exit law that cannot pay for the losses. The platform was
never the bottleneck; the *absence of a platform* made every strategy iteration expensive.
ezCHAIN is that platform. It deliberately contains no edge.

---

## 2. What worked — keep, with attribution

### 2.1 Money and execution

1. **Ledger identity + boot healing** — pumpbot `store.py:474-486`, `wallet.py:78-160`.
   `balance == SUM(rows)`; derived columns recomputed from cash rows at boot. → CONTRACT I1.
2. **Paper mirrors the real route** — pumpbot `engine.py:2290-2345`, `executor.py`;
   `_basis_ok` proves the sell before the buy. No fake fills. → I2.
3. **Ledger + repository protocols** — v2 `services/trading/ledger.py`,
   `shared/repositories/protocols.py`. SUM-derived balance and typed repos are right;
   fire-and-forget writes are not. → I1.
4. **Exact-quote contract** — v3 `trading_core/contracts.py`: fresh buy *and* full-size
   sell quote, route identity, divergence bounds, rent/ATA accounting. The strongest
   abstraction in any of the repos. → I2, I3.
5. **Exit basis clamp** — v3 `hermes_lane/service.py:1568`: if fill vs mark is outside a
   sane band, the fill governs, so stops can fire. Prevents "PnL flat while reality is
   −79%".
6. **Slippage escalation + all-tiers-exhausted accounting** — v2
   `execution/slippage_engine.py`, `execution/service.py:1041-1240`.
7. **One entry writer + anti-stampede** — pumpbot `wallet.py:246-262` (duplicate-mint
   claim, single choke point).

### 2.2 Risk and measurement

8. **Fail-closed safety with positive evidence only** — pumpbot `safety.py:36-72`;
   v3 fail-closed picker names the failed dimension. → I3.
9. **Bounded, self-re-arming adaptivity** — pumpbot `cell_policy.py` (empirical Bayes,
   0.8–1.25× allocation, max one paused cell) and regime-stamped breakers. Adaptation is
   allocation/pausing, never prediction in the critical path.
10. **Causal observation book + fill-honest labels** — pumpbot
    `store.py:263-296`, `opportunity.py:1126-1306`, `policy_sim.py`. Sample before the
    decision, including controls; label with the canonical exit's cash, not win/loss.
    → I9.
11. **Champion/challenger with a no-degradation guard** — pumpbot `ml_train.py:859-948`.
    Keep the incumbent unless out-of-time ranking improves.
12. **Bucket/hazard statistics** — v2 `services/rug_watcher/*`, `age_buckets.py`,
    `bucket_stats.py`: empirical priors per regime instead of global thresholds.
13. **Honest audits as specs** — v2 `SYSTEM_AUDIT_FULL.md`, `PIPELINE_AUDIT_REPORT.md`,
    `EXIT_ENGINE_REDESIGN.md`; v3 `docs/E2E_AUDIT…`, `NIGHT_RUN…`; pumpbot
    `docs/FAILURE_LEDGER.md` F1–F9. Reuse the metric definitions and failure taxonomy.

### 2.3 Product and UI

14. **Position tile as a decision surface** — solbot v3 `app.js:841-1178` and tile-lab
    `shared/core.js`: ladder on the chart, live-vs-entry provenance per cell, honest
    `NO SELL ROUTE`, hold budget, per-tile equity. Winner skin: Neon Arcade. → I10.
15. **Server-side view model** — v1 sent presentation-ready values; clients did no math.
    → I10.
16. **Realtime without a socket server** — v1 SSE + 10 s poll; v2 WS fanout from the DB
    event bus. ezCHAIN uses one WS/SSE stream with snapshot + deltas.
17. **Rate limiter with split budgets and 429 backoff** — v1
    `scanner.py:148-215`; soft vs hard blacklist with TTL `db.py:430-470`.
18. **Explicit rejection strings** — v1 `server.py:1996-2260` (`liquidity_too_low`,
    `token_too_new(3min<5min)`). The funnel is the product.
19. **Auth stack** — v1 `server.py:77-330` (scrypt, TOTP, lockout, fingerprint,
    `__Host-` cookie); v3 SBP-001…007 fixes. Port the defaults, not the hardcoding.
20. **Notification taxonomy** — v3 `notifications/service.py` types/severities/sticky
    errors are right even though delivery was dead-ended.

### 2.4 Engineering process

21. **Failure ledger with "never repeat" laws** — pumpbot `docs/FAILURE_LEDGER.md`.
22. **Paired delta discipline** — absolute dollars are an upper bound; only paired deltas
    count when coverage is outcome-correlated (pumpbot `research/TUNE_ROUND…`).
23. **Patch archive with rejected designs marked "must not deploy"** — v3
    `ops/hermes_research/README.md`.
24. **Deploy divergence guards** — v3 `scripts/deploy.sh:29-47`.
25. **Self-verifying backups + retention** — v3 `scripts/ops/vps_learning_memory_backup.sh`.
26. **Single gate script** — pumpbot `deploy/battery.sh`; klikey `tools/ci.sh` in the
    sibling repos. → AGENTS.md.

---

## 3. What failed — anti-patterns, with the incident

| # | Anti-pattern | Evidence | ezCHAIN rule |
|---|---|---|---|
| 1 | **Mega-modules** | v3 `hermes_lane/service.py` 697 KB, `config.py` 347 KB; v2 `trading/service.py` 5.9k lines, `monitor.py` 5.3k; pumpbot `engine.py` 3390 lines | module budget ≤ 800 LOC, one bounded context per package; CI size check |
| 2 | **Strategies inside the engine** | v3 ~40 lanes still compiled though "all old lanes disabled"; v1 two strategies/two ledgers | I5; plugin-only |
| 3 | **Two sources of truth for one law** | pumpbot `LaneParams` vs `exits.LANE`; v1 vault 10/20/30 vs 70/70/60; v2 ML doc vs evaluator | I7 |
| 4 | **Destructive migrations** | v2 ~30 `clean_slate`/wipe; v3 fresh DB needs an out-of-repo shim | I8 |
| 5 | **Fabricated or coerced data** | pumpbot `mcap or 0`; v3 holders column avg 2.0 + 1000× unit bug; v1 fake smart-money priors; v2 mock fallback returning empty stats | I3, I9, I12 |
| 6 | **Keys in the wrong layer** | v2 imports keypair in routers; v3 plaintext `wallets.json`; v1 commits `.secret_key`; pumpbot hardcodes NATS creds + RPC key | I4 |
| 7 | **Fire-and-forget accounting** | v2 `paper_engine.py` cache-first writes; v3 ledger drift detected in UI instead of prevented | I1 |
| 8 | **Duplicate pipelines per mode/strategy** | v2 seven exit engines, 26 exit reasons; v1 paper vs live fee math | I2, I10 |
| 9 | **Research in the product tree** | pumpbot 44 `live_*.py`, 214 MB pickles, 322 MB repo; v2 ~100 `_tmp_*.py`; v3 `scripts/` junk drawer | lab code in separate repo/package with data lake; CI blocks `_tmp_*` |
| 10 | **Doc/code drift** | every repo; v3 doc says 9 microservices, one process; pumpbot docs describe deleted lanes | same-PR doc updates; maintainer-local status notes |
| 11 | **"WebSocket" that polls / dead notification transport** | v1 `ws_feeds.py`; v3 `/ws` returns 404 while UI has no client | one transport, tested end-to-end |
| 12 | **Config sprawl / clobbering** | v1 five `set_config` replacers; pumpbot 200 constants; v3 346 KB settings | I7 |
| 13 | **Unbounded blocking work in the event loop** | pumpbot SQLite "database is locked", 6.6 GB WAL; v1 threads sharing one state dict | queues + budgets (I11) |
| 14 | **UI monoliths** | v1 6,441-line template; pumpbot 81 KB index.html | components + tokens; one tile model |
| 15 | **Decoration infra** | v1 Postgres/Redis with zero references; v3 vestigial Docker; v2 Grafana with in-file admin creds | infra blueprint tested in CI; no unused service |
| 16 | **Unmeasured brakes** | pumpbot `liquidity_pull` cannot be replayed; v2 no false-negative tracking | I9 |
| 17 | **AI swarm without ownership** | sibling repos show lane collisions and drift | one branch = one owner; CODEOWNERS |
| 18 | **No fresh-clone path** | v1 Dockerfile copies gitignored `config.json`; v3 private shim | CI builds from scratch |

---

## 4. The numbers that shaped the product

- Solana-only, memecoin-only: the four bots spent ~18 months and ~400k LOC on one chain
  and one asset class, with no shared wallet/portfolio layer. **Multi-chain is not a
  feature; it is the reason to rebuild.**
- v3: 497 closed, 18.7% WR, −2.76%/trade, 80% of entries in dead bins. **The platform
  must make strategy iteration cheap and paper gates mandatory**, because the strategy is
  the user's job.
- pumpbot: payoff 0.63, 36.6% WR, needs 61% to break even. **Exits and sizing are
  first-class**, not afterthoughts.
- tile-lab: 5 designs, 1 winner, 3 implementations of the same tile. **One view model,
  one component.**

---

## 5. Decisions inherited (see ADRs)

| ADR | Decision | From |
|---|---|---|
| 0001 | Modular monolith + isolated executor | v2/v3 fake microservices; pumpbot monolith |
| 0002 | Postgres source of truth, outbox, NATS | v1 decorative Postgres; pumpbot SQLite contention |
| 0003 | Append-only ledger, no cached balances | pumpbot best idea; v2 fire-and-forget |
| 0004 | Intent-based strategy SDK, no strategies in-tree | v3 lanes; pumpbot hard-wired ML |
| 0005 | Capability-based chain adapters | all four are Solana-only |
| 0006 | One execution pipeline incl. paper | pumpbot mirror; v1/v2 dual math |
| 0007 | Forward-only migrations | v2/v3 wipe history |
| 0008 | Wireframe-first design, tokens downstream | tile-lab / klikey design sync |
| 0009 | Paper gate before live (≥14 days / ≥100 trades / no invariant breach) | v3/v2 losses on unvalidated strategies |
| 0010 | AGPL-3.0 app, Apache-2.0 SDK | ezcore/klikey licensing review |
