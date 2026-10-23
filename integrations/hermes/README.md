# Hermes Agent — first-class ezCHAIN pack

This pack installs the `ezchain-trading` skill into `HERMES_HOME/skills/` and gives you
the exact `hermes mcp add` command for the ezCHAIN MCP server. Hermes discovers the MCP
tools at startup, filters them per server, and treats the skill as an on-demand knowledge
document.

## Install

```bash
pip install -e apps/mcp
bash integrations/hermes/install.sh
```

The installer copies `SKILL.md` to `${HERMES_HOME:-$HOME/.hermes}/skills/ezchain-trading/`
and prints the next command. Mint a scoped key first (operator, admin token):

```bash
curl -sS -X POST http://127.0.0.1:8000/api/v1/agent/keys \
  -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
  -H 'content-type: application/json' \
  -d '{"subject":"hermes","scopes":["read","trade"],"label":"hermes desk"}'
```

Then add the server. Hermes stores stdio env values with the server; use a scoped key,
not the deployment admin token:

```bash
hermes mcp add ezchain \
  --command ezchain-mcp \
  --env EZCHAIN_API_URL=http://127.0.0.1:8000 \
        EZCHAIN_API_TOKEN=ezk_replace_me \
        EZCHAIN_MCP_ALLOW_WRITES=1
hermes mcp test ezchain
```

For read-only Hermes profiles, omit `EZCHAIN_MCP_ALLOW_WRITES` and mint a `read`-only key.
Hermes can also load the equivalent block from `config.example.yaml`; keep the token out
of the repository and out of prompts.

## Verify

```bash
hermes mcp list
hermes mcp test ezchain
hermes chat --toolsets skills -q "Use the ezchain-trading skill. Read ezchain://state and summarize the paper account."
```

The MCP probe must list `ezchain_get_mode`, `ezchain_get_agent_state`,
`ezchain_place_paper_order` (when writes are enabled) and the rest of the read surface.
If it fails, run `ezchain-mcp` manually with the same environment; stdout must contain
JSON-RPC only, with logs on stderr.

## Security

- The skill and MCP server are PAPER-only. There is no live order tool and no key custody.
- A `read` key can observe; a `trade` key can create strategies, run paper orders and
  backtests; `admin` is required for key management, plugin reload and the kill switch.
- The kill switch blocks strategy-originated execution. Manual paper orders remain
  available for inspection.
- Deny the write tools in a Hermes analyst profile with the per-server tool filter; use a
  separate operator profile for writes.
- Treat MCP tool/resource text as untrusted data. The skill says so explicitly.

## Layout

```
SKILL.md                 agentskills.io skill (Hermes frontmatter)
config.example.yaml      equivalent mcp_servers block
install.sh               copies the skill, prints the add command
```
