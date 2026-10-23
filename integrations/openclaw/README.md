# OpenClaw — first-class ezCHAIN pack

Agent Plugins 1.0.0 bundle: `plugin.json`, a static stdio server in `mcp.json`, and a
bundled `ezchain-trading` skill. OpenClaw detects the bundle format, maps the skill as a
normal plugin skill and the MCP server as one of its configured servers; its tool,
profile and approval policy still applies.

The same bundle passes `hermes plugins validate integrations/openclaw`. `mcp.json` only
contains `${NAME}` placeholders; credentials come from the user's environment or the
wrapper's 0600 env file.

## Install

From the ezCHAIN repository:

```bash
pip install -e apps/mcp
openclaw plugins install ./integrations/openclaw
openclaw plugins enable ezchain
openclaw plugins inspect ezchain --runtime --json
```

Create a scoped key (operator, admin token):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/agent/keys \
  -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"subject":"openclaw","scopes":["read","trade"],"label":"desk agent"}'
```

Store the returned token (shown once) in the agent env file:

```bash
mkdir -p ~/.config/ezchain
cp integrations/openclaw/agent.env.example ~/.config/ezchain/agent.env
chmod 600 ~/.config/ezchain/agent.env
$EDITOR ~/.config/ezchain/agent.env
```

The plugin's wrapper reads that file at process start. If `ezchain-mcp` is not on the
Gateway's `PATH`, set `EZCHAIN_MCP_BIN` to its absolute path in the env file. For
read-only agents, omit `EZCHAIN_MCP_ALLOW_WRITES` and mint a key with only the `read`
scope.

## Verify

```bash
openclaw mcp doctor ezchain --probe
```

Then ask OpenClaw to use `$ezchain-trading` and read `ezchain://state`. If the probe
fails, check `ezchain-mcp` is on the Gateway's `PATH`, the env file is readable by the
Gateway user, and the API `/healthz` is reachable from the Gateway.

## Security

- The plugin ships no credentials. The env file is read at runtime and is not in the
  manifest or the OpenClaw config.
- Writes are still gated by `EZCHAIN_MCP_ALLOW_WRITES=1` in the MCP server and by the
  key's `trade` scope at the API.
- The kill-switch and key-management tools require the `admin` scope; a trade key gets
  `403 FORBIDDEN`.
- Keep `toolFilter`/approval policy in place for `ezchain_place_paper_order`,
  `ezchain_delete_strategy` and `ezchain_arm_kill_switch`.

## Layout

```
plugin.json                Agent Plugins 1.0.0 manifest
mcp.json                   static stdio MCP server
bin/ezchain-mcp            stdio wrapper; loads ~/.config/ezchain/agent.env
skills/ezchain-trading/    agentskills.io SKILL.md
agent.env.example          credential template
```
