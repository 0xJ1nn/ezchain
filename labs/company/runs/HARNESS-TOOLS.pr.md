# HARNESS TOOLS — pull request

Branch: `feat/harness-tools` (base `origin/main` at `13095f8`)
Implementation commit: `0763daabb71242c19e3c3d6c39693eb84663df9b`
Report commit: the HEAD of this branch (docs only, after the implementation commit)

## What changed

Four typed tools join the existing MCP registration catalogue. All of them go through the
existing `EzchainClient` and the API the platform already runs; none adds a route, a service
or a dependency.

| Tool | Input | Output / scope | Path used |
|---|---|---|---|
| `ezchain_market_trend` | `chain, token, window (1h\|4h\|1d\|7d), series (sma\|ema\|rsi)` | points, latest, `up\|down\|flat`, source/age; scope `read` | `GET /api/v1/tokens/{chain}/{token}/candles` |
| `ezchain_chart_spec` | `chain, token, window, overlays (sma\|ema)` | declarative axes/series/overlays/source/freshness; scope `read` | the same stored-candle read |
| `ezchain_list_protection_profiles` | none | the API's profile list; scope `read` | `GET /api/v1/protection/profiles` |
| `ezchain_create_protection_profile` | `name, stop_loss_pct?, trailing_pct?, take_profit_pct?, dry_run?` | created profile or preview; scope `trade` | `POST /api/v1/protection/profiles` |

Each `tools/list` entry for these four declares `inputSchema`, `outputSchema`, `scope` and
`traceEvent: "tool_call"`. Refusals are typed: `INVALID_WINDOW`, `INVALID_SERIES`,
`INVALID_OVERLAY`, `UNKNOWN_TOKEN`, `INSUFFICIENT_CANDLES`, `CANDLES_MALFORMED`,
`INVALID_PERCENTAGE`, `INVALID_NAME`, plus the API's own codes passed through unchanged.

`market_trend` and `chart_spec` compute indicators from the API's stored closed bars. Candle
prices and profile percentages stay exact decimal strings; floats are confined to indicator
arithmetic and its points, documented in the tool description and `apps/mcp/README.md`.
The indicator kernels are kept in the MCP module deliberately: `apps/mcp/pyproject.toml`
declares only `httpx`, so importing `ezchain_core.graph.indicators` would add a dependency,
and that kernel is Decimal-based. No business logic was re-implemented in the API or service
layer, and no money path was touched.

`create_protection_profile` validates and creates only through the existing profile endpoint.
It never arms an arm and never places an order. The API has no profile dry-run route, so
`dry_run: true` is an honest local preview: it parses the same input and returns the exact
request without any HTTP call. The API's `validate_profile` remains authoritative on ranges.

`apps/api/src/ezchain_api/routers/agent.py` advertises the four tool names in its capability
document. One existing security test was narrowed: its forbidden-hint check used substring
matching, which saw the `file` in `profile`; it now matches a token boundary and still
rejects `ezchain_read_file`-style names.

## Invariants

No platform invariant changed. Protection profiles are reusable presets, not arms, orders or
ledger entries; I1 (ledger is the only accountant), I2 (one execution path) and I3 (fail closed
on money) are untouched. The write gate and API key scopes remain the enforcement points; the
declared `scope` is client metadata, not a second permission check.

## Verification

Exact commands and results are in `labs/company/runs/HARNESS-TOOLS.report.md`. Summary:

- `pytest tests/mcp tests/agent_runtime -q`: **262 passed**, 1 pre-existing warning.
- Baseline before the change: **226 passed**; **36 new cases** in the MCP and agent-runtime
  suites, including all four tools through `tools/call`, unknown-token / bad-window /
  invalid-percentage refusals and a trace assertion over a real agent loop.
- `pytest tests/mcp tests/agent_runtime tests/api -q`: **938 passed, 8 skipped**.
- `ruff check` on all changed Python files: clean. `mypy` on all changed app files: clean.
- `node tools/check-links.mjs`, `node tools/check-secrets.mjs`,
  `node tools/check-wireframe.mjs`: all exit 0.

Not pushed. No secrets, keys or `.env` files were added.
