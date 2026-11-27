# HARNESS TOOLS — verification report

Field: harness tools for market analysis and protection profiles
Branch: `feat/harness-tools` · Base: `origin/main` at `13095f8`
Implementation commit (exact SHA): `0763daabb71242c19e3c3d6c39693eb84663df9b`
This report is committed on the same branch in a docs-only follow-up commit; the
implementation SHA above is the commit that carries all code and tests.

## Test counts

| Suite | Before | After | New cases |
|---|---:|---:|---:|
| `tests/mcp tests/agent_runtime -q` (brief command) | 226 passed | **262 passed** | **36** |
| `tests/mcp/test_market_tools.py` + `tests/agent_runtime/test_harness_tools_trace.py` | 0 | **36 passed** | 36 |
| `tests/mcp tests/agent_runtime tests/api -q` (wider check) | — | **938 passed, 8 skipped** | — |

All runs show one pre-existing `PytestDeprecationWarning` about asyncio fixture loop scope
and one pre-existing Starlette/httpx deprecation warning. No failures, no errors.

The 36 new cases cover:

- 4 declaration cases (one per tool): input schema, output schema, `scope`, `traceEvent`,
  closed input object and the read-only hint.
- 1 case for read-visibility of the three read tools without a write credential.
- 1 SMA trend case (points, latest, direction up, source, age, stale, arithmetic note).
- 1 EMA trend case (float-only indicator points and timestamps).
- 2 RSI cases (down turn and flat/neutral 50).
- 4 window-mapping cases (`1h→1m/60`, `4h→5m/48`, `1d→1h/24`, `7d→4h/42`).
- 3 bad-window cases, 1 bad-series case and 1 unknown-token (`NO_POOL`) case.
- 1 chart-spec case with aligned SMA/EMA overlays and decimal-string closes; 1 case with no
  overlays; 1 bad-overlay case.
- 1 list-profiles case (exact API route), 1 create-profile case (exact POST body, no order or
  arm path), 1 decimal-precision case, 1 dry-run case (no HTTP), 5 invalid-percentage cases,
  1 missing-percentage case, 1 empty-name case, 1 API-range-error propagation case and
  1 read-only-refusal case.
- 2 agent-runtime trace cases: one event per tool call with `arguments_digest` equal to
  `digest_json(inputs)` and no credential in the trace; and a refused call traced with its
  `INVALID_WINDOW` code and arguments digest.

## Commands and observed results

```text
P="$(pwd):$(pwd)/packages/adapters/src:$(pwd)/packages/core-py/src:$(pwd)/packages/sdk-py/src:$(pwd)/apps/api/src:$(pwd)/apps/mcp/src:$(pwd)/services/engine/src:$(pwd)/services/executor/src:$(pwd)/services/ingest/src:$(pwd)/services/agent-runtime/src:$(pwd)/services/harness/src"
env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/mcp tests/agent_runtime -q
-> 262 passed, 1 warning in 21.22s   [exit 0]

env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/mcp/test_market_tools.py tests/agent_runtime/test_harness_tools_trace.py -q
-> 36 passed, 1 warning in 1.77s     [exit 0]

env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/mcp tests/agent_runtime tests/api -q
-> 938 passed, 8 skipped, 1 warning in 110.87s   [exit 0]
```

Additional gates (not required by the brief, run because docs and the API catalogue changed):

```text
ruff check <all changed Python files>            -> All checks passed!          [exit 0]
mypy <all changed app Python files>              -> Success: no issues found    [exit 0]
node tools/check-links.mjs                       -> links: ok                   [exit 0]
node tools/check-secrets.mjs                     -> secrets: ok                 [exit 0]
node tools/check-wireframe.mjs                   -> wireframe: ok (32 pages, 32 files) [exit 0]
git status                                      -> clean after both commits
git log -1                                      -> docs-only report commit on feat/harness-tools
```

`bash tools/ci.sh` was **not run** (not verified); the brief's test command plus the wider
API run and the three repository checks above are the evidence in this worktree.

## Scope check

In scope: the four tools, their registration and capability advertisement, the shared typed
error, the tool catalogue documentation, the tests, and the two reports.

Deliberate adjustments: `tests/agent_runtime/test_prompt_injection.py` changed its hint check
from substring to token-boundary matching so the word `profile` no longer looks like a file
tool; the check still rejects file/shell/admin tool names.

Out of scope and absent: no changes to `packages/core-py` money code, no new dependency in
any `pyproject.toml`, no new API route or service, no web UI code, no generated asset, no
debug code, no push. Money is never computed by these tools; percentages are forwarded as
exact decimal strings and the API remains the validator.

## Known limitations

- There is no API dry-run endpoint for protection profiles. `dry_run: true` validates locally
  and previews the exact request; it does not ask the API to validate a hypothetical write.
- Indicator arithmetic lives in the MCP module because that package declares only `httpx`
  and the core indicator kernel is Decimal-based. Floats are confined to indicator values;
  candle prices and profile percentages remain decimal strings.
