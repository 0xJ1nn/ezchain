# PAPER stress harness

Repeatable load and invariant checks for the paper slice. It exercises the public API,
verifies the ledger-derived read models, writes evidence, and can file **one deduplicated
GitHub issue per failure fingerprint**.

All runs are **PAPER**. The harness never signs, never handles keys, never calls a live
venue and refuses to start unless the API reports `mode: paper`.

- Normative invariants: [`docs/CONTRACT.md`](../../docs/CONTRACT.md)
- Paper slice plan: [`docs/PLAN-LIVE-PAPER.md`](../../docs/PLAN-LIVE-PAPER.md)
- Operator procedures: [`docs/RUNBOOK.md`](../../docs/RUNBOOK.md)

## Run it

The API must be up. The harness reads `GET /health` and `GET /api/v1/paper/account`; the
latter must return `mode: "paper"` or the process exits `2` with
`REFUSING_NON_PAPER`. Nothing is mutated before that check.

```bash
tools/stress/run.sh --help
tools/stress/run.sh --mode smoke --duration 30
tools/stress/run.sh --mode soak --duration 300 --concurrency 4
tools/stress/run.sh --mode idempotency --duration 60
tools/stress/run.sh --mode chaos --duration 30
tools/stress/run.sh --mode fuzz
tools/stress/run.sh --mode race --concurrency 8
tools/stress/run.sh --mode killswitch
tools/stress/run.sh --mode matrix
tools/stress/run.sh --mode all --duration 30
```

`run.sh` finds the shared venv (`EZCHAIN_PYTHON`, `./.venv`, the main worktree's venv, or
the git common dir) and sets `PYTHONPATH`. You can also run the module directly:

```bash
PYTHONPATH=packages/core-py/src:packages/adapters/src:apps/api/src:services/engine/src \
  .venv/bin/python -m tools.stress --mode smoke --duration 5
```

### Options

| option | default | meaning |
|---|---|---|
| `--base-url` | `http://127.0.0.1:8000` | API origin; credentials and paths are refused |
| `--mode` | `smoke` | `smoke`, `soak`, `idempotency`, `chaos`, `fuzz`, `race`, `killswitch`, `matrix`, `all` |
| `--duration` | 30 smoke/fuzz/race/killswitch/matrix/all, 60 idempotency, 300 soak | seconds of load per timed mode |
| `--concurrency` | `4` | worker threads (the race uses up to 8 concurrent sells) |
| `--seed` | `0` | deterministic ids and jitter |
| `--chain` | discover | chain id |
| `--token` | discover | token address; requires `--chain` |
| `--token-symbol` | row symbol or `STRESS` | display only |
| `--order-usd` | `1` | paper notional per order |
| `--strategy-order-usd` | `1` | notional used by the strategy lifecycle; set above `max_order_usd` to exercise a rejection |
| `--strategy-kind` | `dca` | `momentum`, `dip`, `dca`, `threshold`, `all` |
| `--report-dir` | `tools/stress/reports` | JSON + Markdown evidence |
| `--file-issues` | off | create GitHub issues for failures |
| `--repo` | none | `owner/name`, required for filing/dry-run |
| `--dry-run-issues` | off | print payloads, never call `gh` |
| `--json` | off | print the report JSON to stdout |
| `--self-test` | — | run the pure checks without a network |
| `--read-p95-ms` | `2000` | read budget |
| `--mutation-p95-ms` | `1500` | paper mutation p95 budget |
| `--mutation-p99-ms` | `5000` | paper mutation p99 budget |
| `--timeout` | `10` | per-request timeout in seconds |

Exit codes: `0` pass, `1` invariant/latency/HTTP failure, `2` config error or refused
preflight, `3` issue filing failed (the report is still kept).

### Auth-enabled API

When `EZCHAIN_AUTH_ENABLED=true`, export the deployment token rather than putting it on
the command line:

```bash
EZCHAIN_STRESS_API_TOKEN="$(cat /run/secrets/ezchain_api_token)" \
  tools/stress/run.sh --mode smoke --duration 30
```

The harness sends it as `Authorization: Bearer ...` on every request, never prints it and
redacts it from report/issue evidence. It refuses to send the token over plain HTTP to a
non-loopback host; use HTTPS or a loopback address. There is deliberately no
`--api-token` flag, because tokens in shell history or process lists are a leak.

## What each mode does

- **smoke** — one full flow (markets, chains, token, buy/sell quotes, one paper buy, a sell
  when a position exists, strategy create → pause → run-now refusal → resume → run → runs
  → delete), then low-rate reads until `--duration` expires. `--strategy-kind all` walks
  momentum, dip, dca and threshold in the same run.
- **soak** — concurrent workers alternate paper buys and sells, with periodic quote and
  market reads, for the full duration. Order creation is capped at 180 so the invariant
  window stays inside the API's 200-row history cap; after the cap workers continue reads.
- **idempotency** — the same idempotency key is posted twice, first concurrently from two
  clients and then sequentially. The same order id must come back and at most one fill may
  exist.
- **chaos** — retry/backoff against a deliberately invalid path (`404`) and an unreachable
  loopback port (`127.0.0.1:1`), then repeated valid `/health` and paper-account reads. It
  proves the failure was recorded and paper state still reconciles.
- **fuzz** — every mutation on the invalid-input list must be refused with a `4xx` and a
  JSON error body, must never be a `5xx` and must never create a fill. Cases cover
  negative/zero/NaN/`Infinity`/`1e400`/empty `amount_usd`, `side: "hold"`, an unknown chain,
  a 10 kB token address, missing/overlong/non-ASCII idempotency keys, extra unknown fields,
  a wrong content type, a non-JSON body, invalid strategy kinds and params (typo'd keys,
  `interval_minutes: 0`, `sell_above_usd <= buy_below_usd`), `PATCH` on an unknown strategy
  id and `DELETE` twice (204 then a clean 404).
- **race** — with a position open, `min(--concurrency, 8)` workers open their connections,
  wait on a barrier and fire distinct full-size sells at the same instant. At most the held
  quantity may be sold, the position may never go negative, cash may never go negative and
  reconciliation must stay ok. The report records what was held, what was attempted and
  what actually filled.
- **killswitch** — reads the current switch state, arms it, runs a strategy whose
  thresholds are derived from the live mark so it *wants* to trade (sell while holding,
  buy while flat), and asserts the run logs `hold` / `KILL_SWITCH_ACTIVE` (or the API's
  defence-in-depth `hold` / `ORDER_REJECTED` with `order_reason: KILL_SWITCH_ACTIVE`) and
  places no order. A manual paper order must still be allowed. The exact previous state is
  restored in `finally`, then read back to prove it.
- **matrix** — reads `GET /api/v1/chains`, and for every chain with
  `capabilities.paper_execute: true` discovers the most liquid token from
  `GET /api/v1/markets?chain=X&limit=5`, reads token + buy quote, places one paper buy of
  `--order-usd`, reads a sell quote, sells the full position, and requires the position to
  close to zero. Chains with no market rows are `no_data` (never a failure). A rejection
  whose reason is entirely documented risk codes (`LIQUIDITY_TOO_LOW`, `PAIR_TOO_YOUNG`,
  `PAPER_MARK_LIMIT`, `QUOTE_STALE`, ...) is recorded as a `rejected:<reason>` skip row; a
  `5xx` or an undocumented rejection is a failure. The Markdown report has one row per
  chain: chain, token, quote kind, buy status, sell status, status and reason.
- **all** — `smoke`, `idempotency`, `fuzz`, `race`, `killswitch`, `matrix` in that order.
  `soak` and `chaos` stay explicit because they are long and deliberately degrade.

If a scenario discovers that paper state can no longer be read consistently (an oversold
position makes every later replay a `5xx`), later scenarios in the same run are skipped with
that reason instead of burying the finding under cascade errors.

Order budget: the load modes may create at most 180 orders per run so the order/fill window
stays inside the API's 200-row cap. The matrix has a separate allowance of 60 so it can still
walk every chain when it runs last in `--mode all`.

## What is checked

Pure functions in `tools/stress/checks.py`, covered by `tests/stress/test_checks.py`:

- order statuses and sides are allowlisted; every fill references an existing order; every
  filled order has exactly one fill.
- idempotency: same key → same order id and at most one fill.
- positions: `quantity > 0`; `market_value` is null or `quantity * mark`; `unrealized` is
  null or `market_value - quantity * avg_cost`; tolerance `$0.01`.
- account: cash/equity/realized/unrealized are finite decimals; cash is non-negative;
  equity equals cash plus known position market values; no NaN or infinity.
- a created strategy has a non-empty run log after `POST /run`.
- a run that claims `buy`/`sell` must point at a **filled** order; a rejected order must log `hold` / `ORDER_REJECTED` (`strategy.run_order_filled`). Set `--strategy-order-usd 1500` to exercise this.
- `GET /paper/reconciliation` is **mandatory**: a `404` fails with `reconciliation.missing`;
  otherwise it must report `ok` (`reconciliation.ok`).
- **independent ledger recompute** (`tools/stress/ledger.py`, pure): from this run's own
  fills only, per token `sum(buy quantity) - sum(sell quantity)` must equal the position
  quantity change between the before/after snapshots, and
  `sum(sell notional) - sum(buy notional) - sum(fees)` must equal the cash change
  (tolerance `$0.01`). The window is scoped to the tokens and order ids this run created; a
  fill from another writer inside the window becomes a `concurrent_writer_suspected` skip,
  and a fill missing from the 200-row window becomes `fills_window_incomplete`. With the API's
  inline engine enabled, strategies created by this run can place their own orders; the
  harness cannot attribute those through the read API, so it reports the skip instead of
  arithmetic it cannot prove. Disable the inline scheduler for an isolated recompute.
- auth negatives when `EZCHAIN_STRESS_API_TOKEN` is set: the same valid mutation without an
  `Authorization` header must be `401`, with a wrong token `401`/`403`, neither may create an
  order, and the same body with the configured token must succeed (`auth.*`). Without a
  token these invariants are skipped with an explicit reason.
- fuzz: `4xx` + JSON error body, no `5xx`, no fill, and `DELETE` twice ends in `204` then `404`.
- oversell race: `race.no_oversell`, `race.quantity_non_negative`, `race.cash_non_negative`.
- kill switch: `killswitch.blocks_strategy`, `killswitch.allows_manual_order`,
  `killswitch.restored`.
- chain matrix: `matrix.chain_flow` (buy filled, sell filled, position closed, or a
  documented risk rejection / `no_data`).
- no unexpected 5xx and no connection errors outside chaos.
- p50/p95/p99 per endpoint group against the configured budgets.

Order/fill checks are scoped to ids this run created, because the API exposes the newest
200 rows and may have older paper history. Account and position checks use the live
snapshot.

## Detected in practice

On 2026-10-22 the harness ran with `--strategy-order-usd 1500` against a clean paper account:
the order was rejected (`ORDER_LIMIT,PAPER_MARK_LIMIT`) while the run logged `action=buy`.
The `strategy.run_order_filled` invariant failed and filed issue #71; PR #72 changed the
engine to log `hold` / `ORDER_REJECTED`, and the same scenario then passed. Re-running
with `--file-issues` filed nothing because the open fingerprint matched.

## Reading a report

Each run writes two files under `--report-dir` (gitignored):

```text
tools/stress/reports/stress-smoke-20261022T113933Z-seed0-12345.json
tools/stress/reports/stress-smoke-20261022T113933Z-seed0-12345.md
```

The Markdown is the human summary. The JSON is the comparison artifact: command, seed,
mode, duration, concurrency, commit, base URL, request counts by status/group, latency
percentiles, invariant pass/fail/skip rows, created ids, skips and at most five failure
samples. Request headers are never captured; the report redacts credential-shaped header
names if that ever changes. `--json` prints the same object to stdout.

## Issue filing and dedupe

Filing is opt-in and never happens for a passing run.

```bash
tools/stress/run.sh --mode soak --duration 300 \
  --file-issues --repo owner/name

tools/stress/run.sh --mode smoke --dry-run-issues --repo owner/name
```

Exact behavior:

1. `fingerprint = sha256(code + "\\x1f" + scenario + "\\x1f" + invariant)`. Timings,
   summaries and response bodies are excluded, so the same defect keeps one fingerprint.
2. Before creating anything, `gh issue list --repo REPO --label stress --state open
   --limit 200 --json number,title,body` is read. Any body containing
   `<!-- stress-fingerprint: HASH -->` suppresses creation.
3. Duplicate findings inside one run collapse to one payload.
4. Remaining payloads are created with
   `gh issue create --repo REPO --label stress --label bug --title ... --body-file -`
   (body on stdin). The body includes the marker, run context, expected/actual, evidence
   and the reproduction command.
5. If `gh` is missing, unauthenticated or the list/create call fails, the command exits
   `3` with the report path; no partial silent filing.
6. Dedupe considers **open** issues. If an issue is closed, a later recurrence files a new
   one.

Typical output:

```text
[stress] UNEXPECTED_5XX: DELETE /api/v1/strategies/... returned 500
```

## Scheduled execution

GitHub Actions is unavailable in this repository. Use cron or `launchd` on the machine
that runs the paper API, with `gh auth status` green for the same user.

cron:

```cron
17 3 * * * cd /path/to/ezchain && tools/stress/run.sh --mode soak --duration 300 --file-issues --repo owner/name >> "$HOME/Library/Logs/ezchain-stress.log" 2>&1
```

`launchd` (`~/Library/LaunchAgents/com.ezchain.stress.plist`):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.ezchain.stress</string>
  <key>WorkingDirectory</key><string>/path/to/ezchain</string>
  <key>ProgramArguments</key>
  <array>
    <string>/path/to/ezchain/tools/stress/run.sh</string>
    <string>--mode</string><string>soak</string>
    <string>--duration</string><string>300</string>
    <string>--file-issues</string>
    <string>--repo</string><string>owner/name</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>17</integer></dict>
  <key>StandardOutPath</key><string>/tmp/ezchain-stress.log</string>
  <key>StandardErrorPath</key><string>/tmp/ezchain-stress.err</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.ezchain.stress.plist
```

Keep the report directory on disk long enough to compare a failing run with the issue
body.

## Safety properties

- **Paper only.** `GET /api/v1/paper/account` must report `mode == "paper"`; otherwise
  exit `2` with `REFUSING_NON_PAPER`, before any mutation.
- **No credentials on the command line.** There is deliberately no `--api-token`, key or
  secret flag (tokens in shell history or a process list are a leak). When the API requires
  auth, the bearer token is read from `EZCHAIN_STRESS_API_TOKEN` and sent as
  `Authorization: Bearer …`; see [Auth-enabled API](#auth-enabled-api). It is never printed,
  written to a report, or included in an issue body, and it is refused over plain HTTP to a
  non-loopback host. Every `--token` flag in this document is a token **address**, not an
  API token.
- **No signing, no live venue.** Only the paper API is called. There is no executor,
  custodian or venue adapter in this tool.
- **Allowlisted paths.** Reads are limited to `/health`, chains, markets, tokens, quotes,
  paper reads and strategies; mutations are limited to paper orders, strategy CRUD/run and
  the single `/api/v1/system/kill-switch` path. The chaos scenario is the only caller that
  bypasses the path allowlist, and only for a fixed invalid path.
- **No process or fault injection.** The harness never kills or restarts a service, never
  writes to the database and never makes the API return a 5xx. It cannot exhaust memory or
  disk.
- **Cleanup.** A created strategy is deleted in `finally`, even after a failed run. The kill
  switch is restored to the exact state observed before the run, in `finally`, and read back
  to prove it. Paper orders are append-only by design and are not deleted; they are paper
  evidence, not real money.
- **No issue spam.** Filing is opt-in, one issue per fingerprint, open issues are checked
  first, and a passing run files nothing.

## Limitations

- The API is trusted to tell the truth about `mode`. The harness cannot detect a
  mislabelled deployment; point it only at a paper API.
- There is no server-side fault injection. Chaos covers retry/backoff and failure
  recording, not a killed executor or a 500 injection.
- Upstream market data can be slow or rate limited. A `429` on an order or quote is retried
  with backoff (the same idempotency key, so a retry cannot duplicate an order); a throttle
  that outlives the backoff, a `5xx` or a read over budget is a failure. Raise
  `--read-p95-ms` only with a documented reason.
- An idempotent `GET` that hits a reset keep-alive connection is retried once on a fresh
  connection (`safety.transport_retries` in the report). A real outage fails both attempts.
- Order/fill checks use the newest 200 rows and this run's ids. The load modes cap
  themselves at 180 orders; the race and matrix have small allowances of their own. When a
  run does exceed the window, a fill whose timestamp predates the oldest order in the page
  is treated as off-window rather than as a missing order, and the ledger recompute reports
  `fills_window_incomplete`.
- The race scenario is a real concurrency test, not a timing trick: it aligns the sells on a
  barrier, but whether a given product build has an oversell window still depends on where
  its request handling yields. An oversell, once observed, is reported with the quantities
  held, attempted and filled. When the paper ledger is left inconsistent, the remaining
  scenarios in the run are skipped with that reason instead of cascading.
- `--file-issues` dedupes against open issues only and needs `gh` auth on the machine that
  runs the schedule.

## Tests

```bash
PYTHONPATH=packages/core-py/src:packages/adapters/src:apps/api/src:services/engine/src \
  .venv/bin/pytest -q tests/stress
.venv/bin/ruff check tools tests
.venv/bin/ruff format --check tools tests
.venv/bin/mypy --strict tools/stress
.venv/bin/python -m tools.stress --self-test
```
