# Hermes Agent integration — measured 2026-10-23

Environment: Hermes Agent **v0.22.2+2978.g369cc6b**, Python 3.14.7, Linux. ezCHAIN API
started from this branch on `127.0.0.1:8099` with SQLite, `EZCHAIN_ENGINE_INLINE=false`,
`EZCHAIN_MARKET_SCAN_INLINE=false`, `EZCHAIN_DATA_MODE=fixture`,
`EZCHAIN_API_TOKEN=test-admin-token`, `EZCHAIN_REQUIRE_AUTH_FOR_READS=true`,
`EZCHAIN_API_KEY_PEPPER=test-pepper`.

## Scoped key

```
POST /api/v1/agent/keys
{"subject":"hermes","scopes":["read","trade"],"label":"hermes"}
-> 201, token ezk_… (shown once)
```

| Request | Result |
|---|---|
| `GET /api/v1/agent/state` without a token | 401 `UNAUTHORIZED` |
| `GET /api/v1/agent/capabilities` with the scoped key | 200; principal `hermes`, scopes `read,trade` |
| `POST /api/v1/system/kill-switch` with the trade key | 403 `FORBIDDEN` (`admin` required) |

## Hermes MCP

```
HERMES_HOME=/tmp/hermes-ez hermes mcp add ezchain \
  --command .venv/bin/ezchain-mcp \
  --env EZCHAIN_API_URL=http://127.0.0.1:8099 \
        EZCHAIN_API_TOKEN=ezk_… \
        EZCHAIN_MCP_ALLOW_WRITES=1
```

Observed: `Saved 'ezchain' to /tmp/hermes-ez/config.yaml (36/36 tools enabled)`.

```
HERMES_HOME=/tmp/hermes-ez hermes mcp test ezchain
```

Observed:

```
✓ Connected (964ms)
✓ Tools discovered: 36
```

The list included `ezchain_get_mode`, `ezchain_get_agent_state`, the read surface,
`ezchain_place_paper_order`, `ezchain_run_backtest`, `ezchain_arm_kill_switch` and
`ezchain_reload_plugins`. There is no live order tool.

## Skill

```
HERMES_HOME=/tmp/hermes-ez bash integrations/hermes/install.sh
HERMES_HOME=/tmp/hermes-ez hermes skills list
```

Observed:

```
ezchain-trading   local   enabled
0 hub-installed, 0 builtin, 1 local — 1 enabled, 0 disabled
```

## Reproduction

Start the API with the env above, mint a key, then run the three commands. The token and
config are in `/tmp` only; they are not part of the repository. Hermes redacts secrets in
its own output by default; the config evidence above redacts the token manually.
