# Evidence — Docker build + compose up (PAPER stack, 2026-10-22)

Environment: Linux (Omarchy), Docker 29.9.2 (build a82e528901), Compose v5.6.1.
Project name used throughout: `ezchain-verify` (isolated from any other containers).
All trading is **PAPER**. No secrets are printed below.

## 1. Prepare env

```bash
cp infra/.env.example infra/.env
# POSTGRES_PASSWORD and EZCHAIN_API_TOKEN set from `openssl rand -hex 32`
```

`infra/.env` is gitignored and was not committed.

## 2. Build and start

```bash
docker compose -p ezchain-verify -f infra/compose.yaml up --build -d
```

Observed: build completed for `ezchain/api:local` (315 MB) and `ezchain/web:local`
(1.12 GB); Postgres healthy; `migrate` exited 0; api/web/caddy became healthy.

Images built:

| Image | ID | Size |
|---|---|---|
| `ezchain/api:local` | `bc7c3509d4c6` | 315 MB |
| `ezchain/web:local` | `aa8a322768f8` | 1.12 GB |
| `postgres:16-alpine` | `deb28ee47118` | 420 MB |
| `caddy:2.8-alpine` | `af32e97399fe` | 71.3 MB |

## 3. Service status

```bash
docker compose -p ezchain-verify -f infra/compose.yaml ps
```

```
NAME                        IMAGE                STATUS
ezchain-verify-api-1        ezchain/api:local    Up (healthy)
ezchain-verify-caddy-1      caddy:2.8-alpine     Up (healthy)
ezchain-verify-migrate-1    ezchain/api:local    Exited (0)
ezchain-verify-postgres-1   postgres:16-alpine   Up (healthy)
ezchain-verify-web-1        ezchain/web:local    Up (healthy)
```

```bash
docker compose -p ezchain-verify -f infra/compose.yaml ps -a migrate
# migrate: Exited (0)
```

## 4. Health and readiness

```bash
curl -sS -i http://localhost:8080/healthz
# HTTP/1.1 200 OK  {"status":"ok"}

curl -sS http://localhost:8080/readyz
# {"status":"ready","db":"ok","migration":"head","revision":"0003_paper_fill_order","head":"0003_paper_fill_order"}
```

## 5. Reads through the edge

```bash
curl -sS http://localhost:8080/api/v1/chains | head -c 120
# [{"id":"solana",...},{"id":"ethereum",...},{"id":"base",...}, ...]

curl -sS 'http://localhost:8080/api/v1/markets?chain=base&limit=5' | head -c 120
# {"rows":[{"chain":"base","pair_address":"0x6c56...","symbol":"WETH",...}, ...], ...}

curl -sS -o /dev/null -w '%{http_code}\n' http://localhost:8080/
# 200
```

Web root returned `200` with `<!DOCTYPE html>...`.

## 6. Paper BUY through the edge

```bash
curl -X POST http://localhost:8080/api/v1/paper/orders \
  -H 'content-type: application/json' -H 'origin: http://localhost:8080' \
  -d '{"chain":"base","token_address":"0x4200000000000000000000000000000000000006","side":"buy","amount_usd":"25","idempotency_key":"docker-smoke-1"}'
```

```
{"id":"a889bd03-cdfc-4372-99fa-7554719363fb","chain":"base",...,"status":"filled",
 "quote_kind":"paper_mark",...}
```

The web proxy injected the bearer token server-side; the browser never held it.

## 7. Direct API mutation without token (401)

The API port is not published to the host. From inside the api container:

```bash
docker compose -p ezchain-verify -f infra/compose.yaml exec -T api python - <<'PY'
import urllib.request, urllib.error
data = b'{"chain":"base","token_address":"0x4200000000000000000000000000000000000006","side":"buy","amount_usd":"1","idempotency_key":"docker-direct-no-token"}'
req = urllib.request.Request("http://127.0.0.1:8000/api/v1/paper/orders", data=data,
                            headers={"content-type":"application/json"}, method="POST")
try:
    urllib.request.urlopen(req)
except urllib.error.HTTPError as e:
    print(e.code, e.read().decode()[:120])
PY
```

```
401 {"detail":{"code":"UNAUTHORIZED","message":"missing or invalid bearer token","retryable":false}}
```

`curl http://localhost:8000/...` from the host is not reachable (port not published), as designed.

## 8. Restart drill

```bash
docker compose -p ezchain-verify -f infra/compose.yaml restart api
# Container ... Restarting / Started
```

After restart, api healthy. Position and ledger persisted:

```bash
curl -sS http://localhost:8080/api/v1/paper/account
# cash_usd "9974.92500000", 1 position WETH (0.01043976...)

curl -sS http://localhost:8080/api/v1/paper/reconciliation
# {"ok":true, ...}
```

## 9. Backup drill

Dump (INFRA.md §7):

```bash
docker compose -p ezchain-verify -f infra/compose.yaml exec -T postgres sh -c \
  'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > /tmp/ezc/ezchain-2026-10-22.dump
# 26004 bytes; sha256 76184ebc251eb20b09ab8e69197c7c5f3fd87a28c132054dc002dc14029e188d
```

Non-destructive restore into a scratch database:

```bash
docker compose -p ezchain-verify -f infra/compose.yaml exec -T postgres sh -c \
  'createdb -U "$POSTGRES_USER" ezchain_restore'
docker compose -p ezchain-verify -f infra/compose.yaml exec -T postgres sh -c \
  'pg_restore -U "$POSTGRES_USER" -d ezchain_restore --no-owner' < /tmp/ezc/ezchain-2026-10-22.dump
# RESTORE OK
# ledger_entries: 6
```

Scratch database `ezchain_restore` was dropped after verification.

## 10. Defects fixed during this run

1. `apps/web/tsconfig.json` included `**/*.ts`, so `next build` type-checked
   `playwright.config.ts`, which imports `./e2e/proxy-env`. The `apps/web/e2e` tree is
   correctly excluded from the production image (`.dockerignore`), so the import failed
   at build time. Fix: exclude `e2e` and `playwright.config.ts` from tsconfig; also
   add `apps/web/playwright.config.ts` to `.dockerignore`.

No other Dockerfile/compose/Caddyfile defects were hit; the stack built and ran as
authored.

## 11. Teardown

```bash
docker compose -p ezchain-verify -f infra/compose.yaml down -v
```

Volumes `pgdata`, `caddydata`, `caddyconfig` (project-scoped) were removed.
The lead's `ezchain-pg` container was not touched.