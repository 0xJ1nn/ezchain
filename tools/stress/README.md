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
| `--mode` | `smoke` | `smoke`, `soak`, `idempotency`, `chaos` |
| `--duration` | 30 smoke/chaos, 60 idempotency, 300 soak | seconds |
| `--concurrency` | `4` | worker threads |
| `--seed` | `0` | deterministic ids and jitter |
| `--chain` | discover | chain id |
| `--token` | discover | token address; requires `--chain` |
| `--token-symbol` | row symbol or `STRESS` | display only |
| `--order-usd` | `1` | paper notional per order |
| `--strategy-kind` | `dca` | `momentum`, `dip`, `dca`, `threshold` |
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

## What each mode does

- **smoke** — one full flow (markets, chains, token, buy/sell quotes, one paper buy, a sell
  when a position exists, strategy create → pause → run-now refusal → resume → run → runs
  → delete), then low-rate reads until `--duration` expires.
- **soak** — concurrent workers alternate paper buys and sells, with periodic quote and
  market reads, for the full duration. Order creation is capped at 180 so the invariant
  window stays inside the API's 200-row history cap; after the cap workers continue reads.
- **idempotency** — the same idempotency key is posted twice, first concurrently from two
  clients and then sequentially. The same order id must come back and at most one fill may
  exist.
- **chaos** — retry/backoff against a deliberately invalid path (`404`) and an unreachable
  loopback port (`127.0.0.1:1`), then repeated valid `/health` and paper-account reads. It
  proves the failure was recorded and paper state still reconciles.

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
- `GET /paper/reconciliation` is optional: `404` is a skip, otherwise it must report ok.
- no unexpected 5xx and no connection errors outside chaos.
- p50/p95/p99 per endpoint group against the configured budgets.

Order/fill checks are scoped to ids this run created, because the API exposes the newest
200 rows and may have older paper history. Account and position checks use the live
snapshot.

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
- **No credentials.** The CLI has no token/auth/key option. The HTTP client sends only
  `User-Agent` and `Accept`; it never sends `Authorization`. `--token` is a token
  **address**, not an API token.
- **No signing, no live venue.** Only the paper API is called. There is no executor,
  custodian or venue adapter in this tool.
- **Allowlisted paths.** Reads are limited to `/health`, chains, markets, tokens, quotes,
  paper reads and strategies; mutations are limited to paper orders and strategy CRUD/run.
  The chaos scenario is the only caller that bypasses the path allowlist, and only for a
  fixed invalid path.
- **No process or fault injection.** The harness never kills or restarts a service, never
  writes to the database and never makes the API return a 5xx. It cannot exhaust memory or
  disk.
- **Cleanup.** A created strategy is deleted in `finally`, even after a failed run. Paper
  orders are append-only by design and are not deleted; they are paper evidence, not real
  money.
- **No issue spam.** Filing is opt-in, one issue per fingerprint, open issues are checked
  first, and a passing run files nothing.

## Limitations

- The API is trusted to tell the truth about `mode`. The harness cannot detect a
  mislabelled deployment; point it only at a paper API.
- There is no server-side fault injection. Chaos covers retry/backoff and failure
  recording, not a killed executor or a 500 injection.
- Upstream market data can be slow or rate limited. `429` is recorded as a skip; a `5xx` or
  a read over budget is a failure. Raise `--read-p95-ms` only with a documented reason.
- Order/fill checks use the newest 200 rows and this run's ids. The 180-order cap keeps a
  run inside that window.
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
