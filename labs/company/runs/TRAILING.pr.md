# Stop-loss and trailing-profit protection profile

## Problem

`ProtectionProfile` already stores `stop_loss_pct`, `trailing_pct` and `take_profit_pct`,
but the only consumer is `_profile_defaults`: it fills one arm with one percentage, and
`evaluate_arm` decides a single arm kind. A position could not be guarded by a stop and a
trailing profit in one decision, and there was no pure kernel that took a whole profile
plus an entry, peak and current price and returned one typed decision with the exact
trigger price.

## Change

- Reuse the existing `ProtectionProfile`, `ProtectionValidationError` and reason-code
  vocabulary in `ezchain_core.protection`; no second protection system is introduced.
- Add `ProfileDecision` and the pure `evaluate(profile, entry, peak, current)`. Each
  threshold is an exact `Decimal`; the result is `hold`, `exit_stop`, `exit_trailing` or
  `exit_take_profit` plus the trigger price (or `None` on a hold) and a ratcheted peak.
- Ratcheting uses `max(peak, current)`: a lower mark cannot lower the trigger, and
  feeding `decision.peak_usd` back as the next peak carries the ratchet across ticks.
  Trailing only arms above water; a peak at or below entry never fires a trailing exit.
- Precedence is stop loss, then trailing profit, then take profit: downside exits win.
  Boundary cases trigger inclusively (`<=` for stops/trailing, `>=` for take profit).
- Bound the three percentages for both construction and evaluation: stop `0 < pct <= 100`,
  trailing `0 < pct < 100`, take profit finite and `> 0`. Floats, non-finite values and
  invalid prices are refused with typed errors.
- Wire the kernel into the engine's existing protection seam with read-only
  `evaluate_profile_paper`: the same `TokenSource.get_token` mark read and `position_state`
  ledger replay feed the kernel, and the result is a `ProfilePaperEvaluation`.

## Scope

In: the combined profile kernel, its validation, exports and the read-only paper seam,
with tests in `tests/core` and `tests/engine`. Out: order placement, arm state, events,
ledger writes, scheduler tick, API schemas, migrations, UI, strategy code and
`docs/DECISIONS.md`. No new arm kind, flag or database field was added.

## Seam decision

The engine seam exists: `evaluate_arm_row` in `services/engine/src/ezchain_engine/protection.py`
is driven by `evaluate_all_arms` from the scheduler tick and already performs one market
read plus one ledger replay before calling the pure core kernel. The new whole-profile
kernel is wired into that read/derive layer through `evaluate_profile_paper`, which returns
a decision without placing an order or writing a row. Making a combined profile fire a
paper order would require a persisted profile-backed arm kind and a migration, and would
change the intent/order path; that is deliberately left for a separately reviewed change.

The profile is buy-only by design: the position model and `replay_position` refuse negative
quantities, so there is no short side to mirror. This is documented in `evaluate`'s docstring.

## Risk

Money path, but no money moves: the kernel is pure and the engine seam is read-only. All
arithmetic is `Decimal` under a precision sized to the operands; no float enters a decision.
Failures (empty profile, out-of-range percentage, non-Decimal or non-finite price, stale or
unknown mark, absent position) raise typed refusals instead of inventing a zero or firing
an exit. Existing arm evaluation and `validate_profile` callers were checked by the full
suite; the only behaviour change to existing code is that profile percentages outside the
documented bounds are now rejected at construction.

## Verification

- Implementation commit: `26cb13d72a355a88aa5f814356e5191dcf361eb9` on `feat/trailing-protection`
  (base `origin/main` at `13095f8`), signed off, body ends with `Assisted-by: hy4-flash-free`.
- New vectors: `49 passed in 0.32s` in `tests/core/test_protection_profile.py` and
  `7 passed in 0.73s` in `tests/engine/test_protection_profile_paper.py` — 56 cases total.
- Required command, with imports pinned to this worktree:
  `env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/core tests/engine tests/api -q`
  → `921 passed, 8 skipped, 1 warning in 137.82s (0:02:17)`, exit status 0. The same command
  on the base before the change reported `865 passed, 8 skipped, 1 warning in 76.34s`.
- `ruff format --check` on the six touched Python files: `6 files already formatted`, status 0.
- `ruff check` on the same six files: `All checks passed!`, status 0.
- `mypy --strict` on the two changed source modules: `Success: no issues found in 2 source files`,
  status 0.
- `bash tools/ci.sh` was not run for this field; the brief pins the scoped pytest command above.
