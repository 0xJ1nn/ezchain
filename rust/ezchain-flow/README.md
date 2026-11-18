# ezchain-flow

Pure composition of one engine tick into the one executor execution path, ported from
Python under recorded behavioural parity (ADR-0049/0050, ADR-0052).

## The flow contract

`run(tick_plan: &TickPlan, executor_inputs: &ExecutorInputs) -> FlowResult` takes each
planned strategy evaluation and protection exit from the tick plan in order and feeds it
through the executor state machine:

1. strategy evaluations first, in the plan's result order;
2. protection exits after them, in the plan's arm-id order;
3. one typed trace step per item — plan source, produced stage path, terminal and typed
   refusal — with the pre-recorded switch reads kept for replay (CONTRACT I9);
4. a misalignment between plan and supplied inputs is a typed `FlowError` and drives
   nothing (fail closed, never a guess about which input belongs to which item).

The composition adds no money rule. "No new entry while armed, exits still settle" is the
combined behaviour of the two kernels: the scheduler plans no strategy entries while the
switch is armed (its protection pass is still planned, `PROTECTION_PASS_REDUCE_ONLY`), and
the executor's entry-only gates refuse a buy that reaches them while armed or unreadable.
A protection arm is a reduce-only sell — the state machine never reads the switch for a
sell — so an armed tick still journals the exit (ADR-0031/0034/0045).

## Reused kernels (path dependencies, never reimplemented)

- `ezchain-scheduler::TickPlan` — the ordered work list and the armed-pass decision.
- `ezchain-executor::state_machine::drive` — the `Intent → RiskDecision → Quote → Order →
  Fill → Journal` stage order, its typed refusals and its entry-only kill-switch reads.

## What stays in Python

Everything that touches the world: the clock, session and market reads, the ledger replay,
the strategy and arm evaluators, order placement, optimistic arm writes, event journaling,
logging, the real kill-switch read, venue I/O, keys, signing and the ledger write. The
inputs here are opaque typed records: a `KillRead` is a pre-recorded switch read, a
`RunInputs` carries Python's verdicts and fingerprints, and a journal group id is observed,
never produced. No I/O, clock, DB, venue, keys, signing or money arithmetic happen here.

## Parity oracle

`tests/flow_parity.rs` records six cases from the Python call relationship
(`StrategyScheduler.run_once` → `evaluate_strategy` / `evaluate_all_arms` →
`ExecutionPipeline.run`), including the armed-switch protection case fixed on
`fix/protection-runs-when-armed` (d9e4926), and asserts the whole ordered trace.
