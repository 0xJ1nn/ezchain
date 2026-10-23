#!/usr/bin/env bash
# Install the ezCHAIN Hermes skill and print the MCP add command.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HERMES_HOME="${HERMES_HOME:-$HOME/.hermes}"
DEST="$HERMES_HOME/skills/ezchain-trading"

mkdir -p "$DEST"
cp "$HERE/SKILL.md" "$DEST/SKILL.md"

if ! command -v ezchain-mcp >/dev/null 2>&1; then
  echo "warning: ezchain-mcp is not on PATH; run: pip install -e apps/mcp" >&2
fi

cat <<'EOF'
Skill installed. Next:

1. Mint a scoped key (operator, deployment admin token):
   curl -sS -X POST http://127.0.0.1:8000/api/v1/agent/keys \
     -H "Authorization: Bearer $EZCHAIN_API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"subject":"hermes","scopes":["read","trade"],"label":"hermes desk"}'

2. Add the server (replace the token with the one-time key):
   hermes mcp add ezchain \
     --command ezchain-mcp \
     --env EZCHAIN_API_URL=http://127.0.0.1:8000 \
           EZCHAIN_API_TOKEN=ezk_replace_me \
           EZCHAIN_MCP_ALLOW_WRITES=1

3. Verify:
   hermes mcp test ezchain
   hermes chat --toolsets skills -q "Use the ezchain-trading skill. Read ezchain://state."
EOF
