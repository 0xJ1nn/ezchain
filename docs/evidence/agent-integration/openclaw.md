# OpenClaw integration — measured 2026-10-23

Environment: OpenClaw **2026.10.6 (fe06181)** on Node 26.12.1, isolated profile
`--profile eztest` (`~/.openclaw-eztest`). ezCHAIN API on `127.0.0.1:8099` with
`EZCHAIN_REQUIRE_AUTH_FOR_READS=true` and a scoped `read,trade` key.

## Bundle install

```
openclaw --profile eztest plugins install /home/jin95/Projects/ezchain/integrations/openclaw \
  --force --accept-capabilities
```

Observed:

```
WARNING - Installing plugin from local path: …/integrations/openclaw
Installing to /home/jin95/.openclaw-eztest/extensions/ezchain…
Installed plugin: ezchain
Saved for the next Gateway start.
```

## Inspect

```
openclaw --profile eztest plugins inspect ezchain --json
```

Observed key fields:

```json
{
  "id": "ezchain",
  "format": "bundle",
  "bundleFormat": "agent",
  "enabled": true,
  "status": "loaded",
  "bundleCapabilities": ["skills", "mcpServers"],
  "mcpServers": [{"name": "ezchain", "hasStdioTransport": true}],
  "install": {
    "acceptedSurface": {"skills": ["skills"]}
  }
}
```

## Skill

```
openclaw --profile eztest skills list
```

Observed:

```
✓ ready   ezchain-trading   Operate an ezCHAIN PAPER workspace with its MCP tools and resources.   openclaw-extra
```

## Notes

- The pack is Agent Plugins 1.0.0 (`plugin.json` + `mcp.json` + `skills/`), not a native
  in-process plugin. OpenClaw detects the bundle and maps both components.
- The MCP server is a stdio wrapper that reads `~/.config/ezchain/agent.env` (0600); no
  token is stored in the bundle or `openclaw.json`.
- `openclaw mcp doctor ezchain` does not list plugin-provided servers; use
  `plugins inspect` for bundle capability evidence and start the Gateway to exercise the
  server. The same MCP server was separately verified through Hermes and the stdio smoke.

## Wrapper + MCP smoke

The bundle's stdio wrapper was run directly with a 0600 env file (scoped key,
`EZCHAIN_MCP_BIN` absolute path):

```
printf '{"jsonrpc":"2.0","id":1,"method":"initialize",…}\n{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"ezchain_get_mode","arguments":{}}}\n' \
  | EZCHAIN_AGENT_ENV=/tmp/ezagentcfg/agent.env integrations/openclaw/bin/ezchain-mcp
```

Observed: initialize returned protocol `2025-11-25`; `ezchain_get_mode` returned
`structuredContent.mode.execution == "paper"`. The wrapper loaded the scoped key from the
env file; no token is in the bundle.

## Portable validation with Hermes

The same bundle is Agent Plugins v1, not OpenClaw-specific:

```
hermes plugins validate integrations/openclaw
```

Observed:

```
✓ portable manifest — plugin.json parses (Agent Plugins v1)
✓ manifest fields — name present
✓ config schema — not declared
✓ security scan — safe
◆ Isolation — portable plugin (no Python): runs anywhere
Validation passed.
```

`mcp.json` forwards `EZCHAIN_API_URL`, `EZCHAIN_API_TOKEN`, `EZCHAIN_MCP_ALLOW_WRITES`
and `EZCHAIN_MCP_BIN` via `${NAME}` placeholders; the values come from the user's
environment/profile, never from the package.
