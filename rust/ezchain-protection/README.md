# ezchain-protection

Pure protection-arm trigger evaluation, ported from
`packages/core-py/src/ezchain_core/protection.py` under recorded parity
(ADR-0049/0050 gate: `record → diff → gate → bench`).

## In scope

- `validate_arm`: all typed rejection codes and messages, including the
  ladder-specific ones.
- `evaluate_arm`: the full branch order (`NOT_ARMED`, `EXPIRED`,
  `PRICE_UNKNOWN`, `MARK_STALE`, `NO_POSITION`, `INVALID_ARM`, `ARMED`,
  `STOP_LOSS`, `TAKE_PROFIT`, `TAKE_PROFIT_LEVEL`, `TRAILING_STOP`), the fired
  ladder level index, the above-water trailing peak, and the replay `inputs`
  map.
- Entry-only kill-switch gating: a triggered arm produces a reduce-only exit;
  an armed global kill switch refuses entries only (ADR-0031/0034).

## Decision table (ported from the Python oracle)

| Precondition / arm kind | Condition | Action · reason | Sell fraction | Level |
|---|---|---|---|---|
| `status != armed` | — | hold · `NOT_ARMED` | 0 | — |
| `expires_at <= now` | — | hold · `EXPIRED` | 0 | — |
| mark missing | — | hold · `PRICE_UNKNOWN` | 0 | — |
| mark stale | — | hold · `MARK_STALE` | 0 | — |
| position quantity <= 0 | — | hold · `NO_POSITION` | 0 | — |
| `stop_loss` price | `mark <= price_usd` | trigger · `STOP_LOSS` | `sell_fraction` | — |
| `stop_loss` loss % | cost <= 0 → no threshold | hold · `INVALID_ARM` | 0 | — |
| `stop_loss` loss % | `mark <= cost*(1-loss/100)` | trigger · `STOP_LOSS` | `sell_fraction` | — |
| `take_profit` price | `mark >= price_usd` | trigger · `TAKE_PROFIT` | `sell_fraction` | — |
| `take_profit` gain % | cost <= 0 → no threshold | hold · `INVALID_ARM` | 0 | — |
| `take_profit` gain % | `mark >= cost*(1+gain/100)` | trigger · `TAKE_PROFIT` | `sell_fraction` | — |
| ladder | lowest untriggered rung reached | trigger · `TAKE_PROFIT_LEVEL` | rung `fraction` | rung index |
| ladder | no untriggered rung reached / all fired | hold · `ARMED` | 0 | — |
| ladder gain rung | cost <= 0 → no threshold | hold · `INVALID_ARM` | 0 | — |
| `trailing_stop` | never above water → no peak | hold · `ARMED` | 0 | — |
| `trailing_stop` | peak tracked, `mark <= threshold` | trigger · `TRAILING_STOP` | `sell_fraction` | — |
| `trailing_stop` | peak tracked, above threshold | hold · `ARMED` | 0 | updated peak |

Thresholds are computed in Python (the opaque operands `computed_trigger_price`
and `computed_level_triggers`) because this crate performs no decimal
arithmetic. A missing operand maps to `INVALID_ARM` exactly where Python would
decline to compute a threshold.

## Out of scope (stays in Python)

- Decimal arithmetic: percent thresholds, ladder fraction sums and all
  `Decimal` context behaviour. `ezchain-decimal` now has division under its own
  parity gate, but this crate's field is the decision table, not arithmetic.
- `validate_profile` / `resolve_profile`: application-level business logic.
- Market read, ledger replay, arm writes, event journal and order placement
  (`services/engine/src/ezchain_engine/protection.py`).
- The persisted kill switch, `OrderRequest` and the executor's `active` wrapper.
