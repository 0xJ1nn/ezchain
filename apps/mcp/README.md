# ezCHAIN MCP server

Model Context Protocol server for the ezCHAIN API. It exposes the full read surface and a
small set of paper-only mutation tools. It is the plug-in point for Hermes Agent, OpenClaw
and any other MCP-capable client.

```bash
pip install -e apps/mcp
EZCHAIN_API_URL=http://127.0.0.1:8000 ezchain-mcp
```

Read-only is the default. Writes (paper orders, strategy changes, backtests) require both
a scoped agent key and an explicit opt-in:

```bash
export EZCHAIN_API_TOKEN="ezk_…"   # a scoped read/trade key, not the admin token
export EZCHAIN_MCP_ALLOW_WRITES=1
```

The server never opens writes for the deployment token or any `admin` key. At startup it
reads the admin-only `GET /api/v1/agent/keys` route; a credential that answers there
keeps the server read-only and prints how to mint a scoped key. A revoked key or an
unreachable API fails closed.

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/agent/keys \
  -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"subject":"hermes","scopes":["read","trade"]}'
```

Kill-switch control and plugin reload have no agent tool: they need the `admin` scope, so
the operator performs them over REST. An agent's halt is `ezchain_pause_strategy`.

There is no live order tool. The API refuses to boot with `EZCHAIN_EXECUTION_MODE=live`
until the executor exists, and the MCP server never accepts a client-supplied mode.

Hermes (`~/.hermes/config.yaml`):

```yaml
mcp_servers:
  ezchain:
    command: ezchain-mcp
    env:
      EZCHAIN_API_URL: http://127.0.0.1:8000
      EZCHAIN_API_TOKEN: ${EZCHAIN_API_TOKEN}
      EZCHAIN_MCP_ALLOW_WRITES: "1"
```

OpenClaw:

```bash
openclaw mcp add ezchain \
  --command ezchain-mcp \
  --env EZCHAIN_API_URL=http://127.0.0.1:8000 \
  --env EZCHAIN_API_TOKEN="$EZCHAIN_API_TOKEN" \
  --env EZCHAIN_MCP_ALLOW_WRITES=1
openclaw mcp doctor ezchain --probe
```

Resources: `ezchain://state`, `ezchain://capabilities`, `ezchain://markets`,
`ezchain://positions`, `ezchain://account`, `ezchain://strategies`, `ezchain://plugins`,
`ezchain://system`, `ezchain://ledger`, `ezchain://reconciliation`, plus per-strategy and
per-backtest URIs. Tools are listed by the server; read-only mode hides every mutation.
