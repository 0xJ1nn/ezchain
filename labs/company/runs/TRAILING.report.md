# Trailing-protection field report

## Work item and where it landed

- Field: stop-loss and trailing-profit protection profile, evaluated by a pure kernel.
- Branch: `feat/trailing-protection`, based on `origin/main` at `13095f8`.
- Implementation commit: `26cb13d72a355a88aa5f814356e5191dcf361eb9` —
  `feat(core): evaluate combined protection profiles with a pure kernel`, signed off,
  body ends with `Assisted-by: hy4-flash-free`.
- Worktree: `/Users/0xj1n/Projects/ezchain/.worktrees/wave2/trailing`. No push, pull request,
  promotion or merge was made.

## Existing model found and reused

- `packages/core-py/src/ezchain_core/protection.py`: `ProtectionProfile` already carries the
  three optional `Decimal` percentages, `validate_profile` validates them, and `evaluate_arm`
  is the pure per-arm kernel with a trailing peak ratchet. Reused all three; no second
  protection system was created.
- `services/engine/src/ezchain_engine/protection.py`: `evaluate_arm_row` does the one market
  read, the ledger replay and the intent; `evaluate_all_arms` is driven by the scheduler tick.
- `apps/api/src/ezchain_api/services/protection.py:115` `_profile_defaults` only fills one arm
  kind from a profile, which is the gap this field closes at the kernel level.
- There is no short position model: `types.py` has `Side = Literal["buy", "sell"]` where sell
  is a long reduction, `PositionState` has no direction, and `ledger.py` `replay_position`
  rejects negative quantity. The kernel is therefore buy-only and says so in its docstring.
- A separate `guard.trailing_stop` graph node uses activation/giveback and is strategy-graph
  state, not persisted protection. It was left untouched.

## What changed

- `ezchain_core.protection`: `ProfileAction`, frozen `ProfileDecision`, and `evaluate(profile,
  entry, peak, current)`. Thresholds are computed as `price * (1 +/- pct/100)` inside a
  `localcontext` sized to the operand digits, so no float and no premature rounding enters a
  decision. The returned `inputs` carry caller and computed prices as strings for replay.
- Bounds are shared by `validate_profile` and `evaluate`: stop `0 < pct <= 100`, trailing
  `0 < pct < 100`, take profit finite and `> 0`. The kernel additionally requires `Decimal`
  prices and profile percentages (floats, bools and strings are refused).
- Decision order is stop, trailing, take profit; comparisons are inclusive at the boundary.
  `effective_peak = max(peak or entry, current)` means a lower mark never lowers the trigger;
  callers chain `decision.peak_usd` into the next call to preserve the ratchet. Trailing is
  ignored while the peak is at or below entry.
- `ezchain_engine.protection`: read-only `evaluate_profile_paper` reuses `TokenSource` and
  `position_state`, then calls the kernel. It returns `ProfilePaperEvaluation` and writes no
  arm, event, ledger row or order. Exported from both package `__init__` files.

## Seam decision

The engine seam already exists, so the kernel was wired into it at the read/derive layer:
`evaluate_profile_paper` uses the same one-mark read and ledger replay as `evaluate_arm_row`.
It is deliberately not called from the scheduler tick. Making a combined profile place an
order would require a persisted profile-backed arm kind, a forward-only migration and a
change to the existing intent path; that is outside this field and needs its own review.
The paper seam is therefore read-only and the scheduler, order placement and ledger are
unchanged.

## Exact test counts

- New core vectors, `tests/core/test_protection_profile.py`: `49 passed in 0.32s`, 0 failed.
  Coverage: three boundary/tick pairs (stop, trailing, take profit), exact-decimal threshold,
  peak ratchet and lower-mark stability, first tick without peak, no trailing underwater,
  stop-over-trailing and trailing-over-take-profit precedence, three single-threshold
  profiles, empty profile, 14 percentage refusals, 7 price-input refusals, 5 construction
  bound refusals, exact string parsing and a no-float-in-decision scan.
- New engine paper vectors, `tests/engine/test_protection_profile_paper.py`:
  `7 passed in 0.73s`, 0 failed. Coverage: a stop decision with no arm/event/ledger writes,
  mark-age propagation, peak ratchet through the seam, and refusals for absent position,
  unknown mark, stale mark and a failing market read.
- New cases total: 56.
- Required command quoted verbatim from the brief, with `P` set to the worktree source list:
  `env -u PYTHONPATH PYTHONPATH="$P" /Users/0xj1n/Projects/ezchain/.venv/bin/pytest tests/core tests/engine tests/api -q`
  → `921 passed, 8 skipped, 1 warning in 137.82s (0:02:17)`, exit status 0.
  Baseline before the change on the same command: `865 passed, 8 skipped, 1 warning in 76.34s`,
  exit status 0 — exactly 56 more passes, no new skips or failures.
- `ruff format --check` on the six touched Python files: `6 files already formatted`, status 0.
- `ruff check` on the same files: `All checks passed!`, status 0.
- `mypy --strict packages/core-py/src/ezchain_core/protection.py
  services/engine/src/ezchain_engine/protection.py`: `Success: no issues found in 2 source files`,
  status 0.
- `bash tools/ci.sh`: not run; the brief specifies the scoped pytest command, which was run
  with worktree-pinned imports.

## Not done

- No push, pull request or merge.
- No scheduler change, order placement, ledger change, migration, API schema, UI, strategy
  code or `docs/DECISIONS.md` edit.
- No Rust parity port. The new function is additive; the existing `evaluate_arm` and Rust
  parity vectors are untouched.
- No short-side mirror; the position model does not support shorts, so the profile is
  buy-only and documented as such.
