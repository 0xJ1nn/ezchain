# ezchain-scheduler

Pure orchestration of one engine scheduler tick (`StrategyScheduler.run_once`), ported
from Python under recorded behavioural parity (ADR-0049/0050).

## What is ported

`TickInput::plan()` decides, from inputs only:

- which strategies are evaluated, in the order their results are appended;
- which strategies are skipped, and why (`PAUSED`, `INTERVAL_NOT_ELAPSED`, crashed
  evaluation);
- whether the whole strategy pass is skipped when the kill switch is armed at pass
  start — no market read, no run row, no interval consumed;
- that the protection pass always follows strategies, **including while the switch is
  armed**: a switch refuses new entries, never a reduce-only exit (ADR-0031/0034);
- the ordered, opaque side effects the Python control plane will perform, with a typed
  reason for each plan and skip.

`tests/run_once_parity.rs` records 12 cases from the Python oracle and asserts the whole
plan, including the armed-switch regression case.

## Reused kernels

- `ezchain_core::due::should_run_exact` — the interval gate. This crate only supplies
  instants and the resolved cooldown; it does not reimplement the boundary comparison.
- `ezchain_protection::entry_gate_allows` — the reduce-only gate, proving that an armed
  switch never vetoes an armed arm's exit.
- `freshness_policy` is intentionally not used here: staleness is decided inside the
  strategy/arm evaluators, not by the scheduler pass. No mark crosses this boundary.

## What stays in Python

The clock, session and ledger reads, market snapshots, the strategy and arm evaluators,
order placement, optimistic arm writes, event journaling, logging and the per-order
re-read of the switch. `PlannedAction` values are opaque: this crate performs none of
them, holds no keys, writes no ledger rows and does no I/O.

## Oracle note

The armed-switch regression case comes from `fix/protection-runs-when-armed` (d9e4926),
which is not merged into this branch's base (`bf64eab`). base Python still returns before
its protection pass while armed; the fix and ADR-0031/0034 require the exit to run. The
recorded table asserts the fixed behaviour, as explicitly required for this port. The
other cases match the base commit's scheduler tests.
