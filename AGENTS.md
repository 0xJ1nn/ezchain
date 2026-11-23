# AGENTS.md — contributor guide

ezCHAIN is an open-source, self-hostable multi-chain trading workstation. It runs in
PAPER mode today: live market browsing, an append-only paper ledger, positions, automations,
backtests and external strategy plugins. Live execution is not built. The platform is the
product; trading strategies are external plugins, never repository code.

Read [`docs/CONTRACT.md`](docs/CONTRACT.md) before changing a money path. The code is
truth: where a document and the code disagree, fix the document in the same change.

## Languages by layer

| Layer | Language | Where |
|---|---|---|
| Core engine, P2P networking, validation, RPC, transaction execution, shipped CLI | Rust | `rust/` |
| Application API, engine, indexer/analytics, automation, plugins, tooling | Python | `apps/api/`, `services/`, `packages/` |
| Dashboard and all product UI | TypeScript | `apps/web/` |

Rust owns performance-critical, security-critical and trust-boundary logic. Python owns
business logic and integrations. Use typed, versioned interfaces across the boundary, with
independent tests and benchmarks. Preserve working code; migrate bottlenecks behind
recorded parity instead of rewriting everything.

## Path → responsibility

| Path | Owns | Never contains |
|---|---|---|
| `apps/web/` | UI, routing, presentation | business rules, key material, PnL math |
| `apps/api/` | HTTP/WS edge, auth, validation, read models | order execution, accounting math |
| `apps/mcp/` | agent ingress: MCP tools and resources for a scoped key | admin operations, database access, money logic |
| `services/engine/` | strategy runtime, automation scheduler | signing, direct database writes |
| `services/agent-runtime/` | agent session loop, provider clients, budgets, session lifecycle | money code, key material, platform database writes, admin scope, strategy code |
| `services/harness/` | terminal entry point: one session, a printed trace, typed exit codes | its own loop decisions, approval logic, platform imports |
| `services/executor/` | signing, broadcast, fill confirmation | strategy decisions, UI data |
| `services/ingest/` | chain/market data acquisition, normalization | trading decisions |
| `packages/core-py/` | domain types, money, ledger, risk policy | I/O, HTTP, framework code |
| `packages/sdk-py/` | strategy and automation plugin contract | platform internals |
| `packages/adapters/` | one chain or venue per module | cross-adapter conditionals |
| `design/` | wireframe, tokens, design source of truth | runtime code |
| `infra/` | deployment contract, config schema | secrets |

## Platform invariants

The complete, normative list is [`docs/CONTRACT.md`](docs/CONTRACT.md).

1. The ledger is the only accountant: cash, size, PnL and fees live only in append-only
   `ledger_entries`; journal and position commit atomically.
2. One execution path: paper, testnet and live share
   `Intent → RiskDecision → Quote → Order → Fill → Journal`; only the venue adapter differs.
3. Fail closed on money: unknown risk, missing quote, stale oracle or unreachable risk
   service means no new order. Unknown is never zero.
4. Keys live only in `services/executor/`; never in web, API responses, logs, fixtures,
   traces or plugins.
5. No strategy code in this repository; strategies are external plugins.
6. Chains are capabilities, not branches: never branch on a chain name or `chain_id`.
7. Every number has one home: config is schema-validated and the UI reads it.
8. Migrations are forward-only and never destructive.
9. Replay or it didn't happen: any rule that can move money is replayable from persisted inputs.
10. One position model: the server computes `PositionView`; the UI only formats it.

State the invariant a money-path change touches. Changing an invariant needs a recorded
decision in [`docs/DECISIONS.md`](docs/DECISIONS.md) and explicit approval.

## Checks

```bash
node tools/check-links.mjs        # relative links in public Markdown
node tools/check-secrets.mjs      # secret-shaped values
node tools/check-wireframe.mjs    # wireframe manifest, pages and links
bash tools/ci.sh                  # full local gate
```

A red gate is not merged. Quote each command and its exit status in the pull request; if a
command was not run, say "not verified". Continuous integration is not available here, so
the local gate is the evidence.

## Commits and pull requests

- Branch from current `main`; use `feat/`, `fix/`, `docs/`, `design/`, `refactor/` or `test/`.
- Conventional Commits, signed off with `git commit -s`; one topic per change.
- One problem, one branch, one pull request. Keep the diff scoped.
- Describe what changed and why, list verification commands with observed results, add
  screenshots for UI changes, and update docs in the same change.
- Money-path changes need two approvals; never merge your own change.
- Never commit secrets, keys, seed phrases, API tokens or `.env` files. Test constants must
  be obviously invalid.
