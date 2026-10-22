# Evidence — full PAPER stress run (2026-10-23)

Scope: disposable PostgreSQL 16, real API with bearer auth, live DexScreener and
GeckoTerminal reads. Paper only; no keys, signing or live funds.

## Command

```bash
EZCHAIN_STRESS_API_TOKEN=... \
  tools/stress/run.sh --mode all --duration 5 --concurrency 4 \
  --base-url http://127.0.0.1:8001 \
  --chain base --token 0x4200000000000000000000000000000000000006 --token-symbol WETH
```

## Observed result

```text
stress: PASS — 0 unique finding(s)
```

The run exercised, in order: smoke (full flow), idempotency, fuzz (27 invalid-input
cases), race (4 concurrent full-size sells), kill switch, and the chain matrix.

| Area | Observed |
|---|---|
| Requests | no unexpected 5xx, no connection errors |
| Fuzz | every invalid case returned a 4xx JSON error; no order/fill created |
| Oversell race | sold quantity exactly equalled held quantity; no negative position/cash |
| Kill switch | strategy run blocked (`KILL_SWITCH_ACTIVE`), manual paper order allowed, previous state restored |
| Ledger recompute | skipped with `concurrent_writer_suspected` because the matrix writer overlapped the window; the ledger itself reconciled and `reconciliation.ok` passed |
| Matrix | one buy → sell round trip per paper chain; documented `PAIR_AGE_UNKNOWN` / `no_quote` / `no_data` rows are reported as such, not failures |

The matrix result is population-dependent: 25 paper-capable chains, some with no keyless
quote route or no market rows at that moment. Those are labelled `no_quote`/`no_data`;
they are not counted as a pass for execution.

## Regression covered by this run

- The previous run found an oversell race and a raw-JSON `1e400` 500. Both are fixed:
  fills serialize per paper account, stale oversized sells are rejected
  `POSITION_CHANGED`, and validation errors are JSON 422 responses.
