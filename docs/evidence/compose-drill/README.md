# Evidence — Compose drill at migration head `0023_strategy_risk_profile` (2026-11-14)

Branch `ops/docker-drill`, based on `origin/main` at `36c21359d47882a48b6f93abdc62aad3c11fc542`.
Every result below is the output of the command shown next to it, run in that worktree.
Nothing is inferred, and no container run is claimed: **the authoring host has no Docker
CLI and no Docker daemon**, so the Compose phases were not executed.

This covers issue #231 ("Ops: re-drill the Docker stack at the current migration head").
The last recorded Compose drill was at `0003_paper_fill_order`
([p0-production/docker-up.md](../p0-production/docker-up.md)); the head is now
`0023_strategy_risk_profile`, twenty-three revisions later.

## Environment

macOS 27.2.1 (Darwin, arm64), Apple M3, 8 GB RAM. Python 3.11.18 in the project venv,
PostgreSQL 16.15 (Homebrew) on `127.0.0.1:5432` with a trust-local role.

```bash
command -v docker; command -v docker-compose; ls /var/run/docker.sock
# docker: NOT FOUND
# docker-compose: NOT FOUND
# docker socket: absent
```

## What was executed here

### 1. Daemon-free phase: compose structure and migration chain

```bash
bash tools/ops/drill.sh --static-only
#   passed  static: compose valid (7 services); 23 migrations, head 0023_strategy_risk_profile
# drill: ok (static-only) at 0023_strategy_risk_profile
# exit=0
```

`--static-only` parses `infra/compose.yaml`, then asserts the properties the other phases
depend on: `postgres`, `migrate`, `api`, `web`, `caddy` exist; the four long-running
services have a healthcheck; only `caddy` publishes a host port; `migrate` runs
`alembic … upgrade head` with `restart: "no"`; `api` waits on
`migrate: service_completed_successfully`; the `data` network is `internal` and Postgres
is attached to it alone. It then walks the Alembic chain from the scripts and requires
exactly one head with no branch point.

```bash
python -m tools.ops chain --json
# { "head": "0023_strategy_risk_profile", "problems": [], "revisions": 23 }
# exit=0
```

### 2. The default drill refuses, because there is no daemon

```bash
bash tools/ops/drill.sh
#   passed  static: compose valid (7 services); 23 migrations, head 0023_strategy_risk_profile
#   skipped preflight: docker is not on PATH: install Docker (or Podman with a compose
#           plugin), or run `python -m tools.ops drill --static-only`
# drill: NOT RUN — docker is not on PATH: …
# drill: this is a refusal, not a pass
# exit=2
```

Exit `2` is the contract for "refused to start". This is the honest outcome on this host
and it is what the test suite asserts (`tests/ops/test_drill.py`).

### 3. The stack's phases, replayed on the host without containers

The Compose phases cannot run, so the same *application-level* claims were exercised
directly against a natively started Postgres 16 and a natively started API with the
stack's own production settings. This is **not** a container run: no image was built, no
container started, and the Compose-only parts (Caddy edge, `web` image, `migrate` job's
container exit code) remain unverified.

Setup, on the host:

```bash
createdb ezchain_drill_<stamp>_scratch_test
EZCHAIN_DATABASE_URL="postgresql+psycopg://<local role>@127.0.0.1:5432/ezchain_drill_<stamp>_scratch_test" \
  alembic -c apps/api/alembic.ini upgrade head
# 0023_strategy_risk_profile (head)

EZCHAIN_ENVIRONMENT=prod EZCHAIN_AUTO_CREATE_SCHEMA=false EZCHAIN_AUTH_ENABLED=true \
EZCHAIN_DATA_MODE=live EZCHAIN_EXECUTION_MODE=paper EZCHAIN_LOG_LEVEL=INFO \
EZCHAIN_DATABASE_URL=… EZCHAIN_API_TOKEN=<32-byte token> \
  uvicorn ezchain_api.main:app --host 127.0.0.1 --port <port>
```

Readiness reported the head of this checkout:

```bash
curl -sS http://127.0.0.1:<port>/readyz
# {"status":"ready","db":"ok","migration":"head",
#  "revision":"0023_strategy_risk_profile","head":"0023_strategy_risk_profile"}
```

Paper buy and its replay, on a chain whose ladder is the capped `paper_mark` (the only
quote path that needs no venue credential) — the same order in both requests:

```bash
curl -sS -X POST http://127.0.0.1:<port>/api/v1/paper/orders \
  -H 'content-type: application/json' -H 'authorization: Bearer <token>' \
  -d '{"chain":"aptos","token_address":"0x1::aptos_coin::AptosCoin","side":"buy",
       "amount_usd":"25","idempotency_key":"ez214-host-drill-2"}'
# {"id":"5c44e3be-…","status":"filled","quote_kind":"paper_mark",
#  "actor":{"kind":"human","id":"deployment"}, …}
# replayed with the same key: same id, status filled, one fill row
```

```bash
curl -sS http://127.0.0.1:<port>/api/v1/paper/reconciliation -H 'authorization: Bearer <token>'
# ok: True  checks: 11  cash: 9974.925  fills: 1
```

Restart: the API process was stopped and started again against the same database.

| | before restart | after restart |
|---|---|---|
| `cash_usd` | `9974.92500000` | `9974.92500000` |
| `realized_usd` | `-0.07500000` | `-0.07500000` |
| open positions | 1 | 1 |
| `equity_usd` | `9999.924999999999999999846200` | `9999.924999999999999999846200` |
| `/readyz` | `migration: head` | `migration: head` |
| order history | 1 filled order, `quote_kind=paper_mark` | identical row |

Backup and restore, using the repository's own tooling against a real Postgres:

```bash
python -m tools.ops backup --database "postgresql+psycopg://<role>@127.0.0.1:5432/ezchain_drill_<stamp>_scratch_test" --dest <dir>
# bytes: 60197
# sha256: cca3a91dfc4f372e3caaa8a430680dc6b0ef59aba2cb66e35dae414d532c59f6
# alembic_version: 0023_strategy_risk_profile
# counts: {"alembic_version":1,"ledger_entries":6,"market_snapshots":59,"paper_fills":1,
#          "paper_orders":1,"paper_wallets":26,"system_flags":1}
# backup: ok (written)

python -m tools.ops restore --database "postgresql+psycopg://<role>@127.0.0.1:5432/ezchain_drill_restore_<stamp>_scratch_test" \
  --manifest <dir>/ezchain_drill_<stamp>_scratch_test.manifest.json
# created: True  loaded: …  counts: (identical to the counts above)
# reconciliation: {"detail":"11 checks pass","ok":true, "totals": {… "cash_usd":"9974.925", …}}
# differences: []
# restore: ok — 6 ledger entries reproduced
```

The restore target is a different database, created by the tool, and it is compared to
the manifest row-for-row (per-table digest), not by counts alone.

Both scratch databases were dropped afterwards.

## What was skipped, and why

| Phase | State | Reason |
|---|---|---|
| `preflight` (`docker compose config`) | not run | no Docker CLI on this host |
| `up` (`compose up --build -d`) | not run | no daemon |
| `ready` through the Caddy edge | not run | the edge only exists inside the stack |
| `migrate` container exit code | not run | the one-shot container did not run |
| `paper` through the edge / Next proxy | not run | replayed against the API directly instead (above) |
| `restart` via `compose restart api` | not run | process restart replayed instead (above) |
| `backup` / `restore` inside the Postgres container | not run | the same tools ran against a host Postgres instead |
| `teardown` (`down -v`) | not run | nothing was started |

Consequences, stated plainly:

- The image builds (`apps/api/Dockerfile`, `apps/web/Dockerfile`) are **not** verified by
  this run.
- The Caddy edge, the Next proxy's token injection, and the `caddy` → `web` → `api` path are
  **not** verified by this run.
- Nothing here supersedes the 2026-10-22 container run for those surfaces; it only says
  the application layer still works at `0023_strategy_risk_profile`.

## Defects observed (out of scope, recorded for the next drill)

1. **`alembic check` reports drift at head on a fresh database.** On a freshly created
   Postgres database upgraded to `0023_strategy_risk_profile`, `alembic check` exits non-zero:

   ```
   FAILED: New upgrade operations detected: [[('modify_nullable', …, 'automation_consents',
   'acknowledged_at', …, None, True, False)], [('modify_nullable', …, 'automation_settings',
   'updated_at', …)], [('modify_nullable', …, 'order_approvals', 'intent', …)],
   [('modify_nullable', …, 'order_approvals', 'risk_decision', …)],
   [('modify_nullable', …, 'order_approvals', 'created_at', …)]]
   ```

   This is **pre-existing**: a pristine `origin/main` worktree reproduces the identical
   five diffs. `tools/check-migrations.sh` therefore fails on this host, and so does
   `tests/api/test_market_events_migration.py::test_a_fresh_postgres_database_has_no_pending_migration`
   when `EZCHAIN_TEST_DATABASE_URL` is set. Five `nullable` server defaults are missing
   from the migrations relative to the models. Not fixed here — it is a model/migration
   change, not a drill change.
2. **Two market-snapshot writes were rejected by column width** during the inline market
   scan (`DataError: value too long for type character varying(160)`, and one at
   `(40)`). The scan continued and no order was affected; not investigated.

## How a maintainer runs the real drill

On a host with a working Docker daemon:

```bash
cp infra/.env.example infra/.env
# set POSTGRES_PASSWORD (>= 16 chars) and EZCHAIN_API_TOKEN (>= 32 chars)
bash tools/ops/drill.sh
#   passed  static:      compose valid (7 services); 23 migrations, head <head>
#   passed  preflight:   docker <version>, compose <version>, config ok
#   passed  up:          stack started in <n>s (ezchain-verify)
#   passed  ready:       ready at <head>
#   passed  migrate:     migrate exited 0; schema at <head>
#   passed  paper:       paper buy <id> filled (paper_mark); replay identical; reconciliation ok
#   passed  restart:     api restarted; cash … and 1 position(s) unchanged; reconciliation ok
#   passed  backup:      dump … bytes sha256 …; restored N ledger entries into ezchain_scratch_drill
#   passed  teardown:    ezchain-verify removed (volumes included)
# drill: ok (compose) at <head>
```

- The project name is `ezchain-verify` (`--project` to change it), so the drill never
  touches another project's volumes.
- `--keep` leaves the stack up and prints the `down -v` command that removes it.
- `--dest <dir>` keeps the dump; otherwise it goes to a temporary directory.
- `--chain`/`--token` change the token the paper order buys. The default is
  `aptos`/`0x1::aptos_coin::AptosCoin`: a chain whose quote ladder is the capped
  `paper_mark`, so the order needs no venue credential. An executable-ladder chain
  (`base`, `solana`, …) needs its provider to answer, and fails the phase with
  `NO_ROUTE` when nothing does.
- Exit codes: `0` every phase passed, `1` a phase failed (the project is still torn
  down), `2` refused to start (no CLI/daemon, unusable `infra/.env`, rejected
  `compose config`).

The drill is also reachable from the gate, only when asked:

```bash
EZCHAIN_GATE_DRILL=static bash tools/ci.sh   # compose + chain checks, no daemon
EZCHAIN_GATE_DRILL=run    bash tools/ci.sh   # the full drill; needs a daemon
```

`tools/ci.sh` defaults to `EZCHAIN_GATE_DRILL=off` and prints that it skipped the drill,
so the default gate stays green on a host without Docker.

## Tests and gate

This worktree has no `.venv` of its own, and the checkout's editable installs point at the
main tree, so both channels were declared explicitly: `.venv` symlinked to the checkout's
virtualenv, and `EZCHAIN_GATE_PYTHONPATH` set to this worktree (the gate's own documented
channel for that case; a stray `PYTHONPATH` is still unset by the gate).

```bash
env -u PYTHONPATH PYTHONPATH="$PWD:$PWD/packages/core-py/src:$PWD/packages/adapters/src:\
$PWD/packages/sdk-py/src:$PWD/apps/api/src:$PWD/services/engine/src:\
$PWD/services/executor/src:$PWD/services/ingest/src" \
  .venv/bin/pytest tests -q -k "ops or drill or compose"
# 114 passed, 8 skipped, 1891 deselected

env -u PYTHONPATH PYTHONPATH="…the same list…" .venv/bin/pytest -q
# 1993 passed, 19 skipped, 1 warning in 63.12s

EZCHAIN_GATE_PYTHONPATH="…the same list…" EZCHAIN_GATE_DRILL=static bash tools/ci.sh
# 1994 passed, 19 skipped, 1 warning in 109.80s
#   passed  static: compose valid (7 services); 23 migrations, head 0023_strategy_risk_profile
# drill: ok (static-only) at 0023_strategy_risk_profile
# ci: ok
# exit=0
```

(The gate run reports one more test than the first direct run, 1994 against 1993, because
one drill test was added between them; the final direct run also reports
`1994 passed, 19 skipped` in 189.32s. The default invocation, `bash tools/ci.sh` with
`EZCHAIN_GATE_DRILL` unset, prints `drill: skipped (…)` and stays green.)

`ruff check` and `ruff format --check` pass over `packages apps services` and over the new
`tools/ops`/`tests/ops` files; `mypy --strict packages apps/api services` reports
`Success: no issues found in 148 source files`.

## Reproduction summary

```bash
bash tools/ops/drill.sh --static-only          # exit 0 here
bash tools/ops/drill.sh                        # exit 2 here: no daemon
python -m tools.ops chain --json               # 22 revisions, one head
env -u PYTHONPATH PYTHONPATH="$PWD:$PWD/packages/core-py/src:$PWD/packages/adapters/src:\
$PWD/packages/sdk-py/src:$PWD/apps/api/src:$PWD/services/engine/src" \
  .venv/bin/pytest tests -q -k "ops or drill or compose"
```
