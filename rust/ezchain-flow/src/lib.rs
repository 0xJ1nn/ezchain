//! Compose one scheduler tick into the executor state machine, in pure Rust.
//!
//! # The field
//!
//! Python drives a tick as `StrategyScheduler.run_once` (`services/engine/.../scheduler.py`):
//! it evaluates each active, due strategy in `created_at` order, then runs the protection
//! pass (`evaluate_all_arms`) so an armed arm shares the same tick and the same intent
//! path. Each evaluator ultimately calls `ExecutionPipeline.run` (`services/executor/...
//! /pipeline.py`), the one `Intent → RiskDecision → Quote → Order → Fill → Journal` path
//! (CONTRACT I2). A protection arm fires that path with a `side="sell", reduce_only=True`
//! request, so the kill switch — which gates entries only — can never trap the exit
//! (ADR-0031/0034).
//!
//! This crate is the *composition* kernel between those two already-ported kernels:
//!
//! * [`ezchain_scheduler::TickPlan`] supplies the ordered work — strategy evaluations
//!   first, in result order, then the armed protection arms, in load order. When the
//!   switch is armed the plan contains no strategy entries, but the protection pass is
//!   still planned (`PROTECTION_PASS_REDUCE_ONLY`).
//! * [`ezchain_executor::state_machine::drive`] supplies each intent's typed stage path
//!   and refusals, including the four entry-only kill-switch reads of a signed buy and
//!   the zero reads of any sell.
//!
//! [`run`] walks the plan in that order, drives each paired executor input, and returns a
//! deterministic [`FlowResult`] trace. It adds no money rule: "no new entry while armed,
//! exits still settle" is the composed behaviour of the two kernels, not a third copy of
//! the switch rule (ADR-0050 rule 3, ADR-0052 integration contract 3).
//!
//! # What stays in Python
//!
//! Every side effect and every measured value: the clock, session and market reads, the
//! ledger replay, the strategy/arm evaluators, order placement, optimistic arm writes,
//! event journaling, logging, the real kill-switch read, venue I/O, keys, signing and the
//! ledger write. They arrive here as opaque typed records — a [`KillRead`] is a
//! pre-recorded switch read, a [`RunInputs`] carries Python's verdicts and fingerprints,
//! and a journal group id is observed, never produced. No I/O, no clock, no DB, no venue,
//! no key material, no signing and no money arithmetic happen in this crate.
//!
//! # Parity oracle
//!
//! `tests/flow_parity.rs` records the Python ordering/refusal cases (including the
//! armed-switch protection case fixed on `fix/protection-runs-when-armed`, d9e4926) and
//! asserts them against [`run`]. Python remains the oracle under ADR-0049/0050: a port is
//! done only when every recorded case matches and the Rust gate is green.

#![forbid(unsafe_code)]

use ezchain_executor::gates::RefusalCode;
use ezchain_executor::state_machine::{self, Run, RunInputs, Terminal};
use ezchain_scheduler::{EvaluationOutcome, TickPlan};

/// One fresh, pre-recorded read of the operator kill switch.
///
/// The real read is Python I/O; this is the opaque typed fact handed to the executor's
/// entry gates. `Unavailable` is a distinct fail-closed state, never "off" (CONTRACT I3).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum KillRead {
    /// `Ok(false)` — switch disarmed at this gate.
    Disarmed,
    /// `Ok(true)` — switch armed at this gate.
    Armed,
    /// `Err` — switch could not be read; an entry refuses, an exit is unaffected.
    Unavailable,
}

impl KillRead {
    fn into_result(self) -> Result<bool, ()> {
        match self {
            Self::Disarmed => Ok(false),
            Self::Armed => Ok(true),
            Self::Unavailable => Err(()),
        }
    }
}

/// One executor input paired with the switch reads its entry gates will consume, in the
/// order the state machine reads them (before venue, after quote, at sign, before submit).
///
/// A sell supplies no reads: a reduce-only exit never reads the switch. A buy supplies
/// one read per entry gate the machine reaches; if it supplies fewer, the missing reads
/// are fail-closed as [`KillRead::Unavailable`] rather than silently read as "off".
#[derive(Debug, Clone)]
pub struct PlannedRun {
    /// Python's opaque per-stage verdicts and fingerprints for this one intent.
    pub inputs: RunInputs,
    /// Pre-recorded switch reads, consumed in gate order.
    pub kill_reads: Vec<KillRead>,
}

impl PlannedRun {
    pub fn new(inputs: RunInputs, kill_reads: Vec<KillRead>) -> Self {
        Self { inputs, kill_reads }
    }
}

/// The executor-side facts for one tick, aligned positionally with the tick plan.
///
/// `strategy_runs` matches `TickPlan::strategy_evaluations` and `protection_runs` matches
/// `TickPlan::protection_pass.arm_ids`, both in plan order. Alignment is checked by
/// [`run`]; a mismatch is a typed [`FlowError`] and no intent is driven (fail closed).
#[derive(Debug, Clone)]
pub struct ExecutorInputs {
    pub strategy_runs: Vec<PlannedRun>,
    pub protection_runs: Vec<PlannedRun>,
}

/// Where a driven run came from in the tick plan.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FlowSource {
    /// A planned strategy evaluation, in result order.
    Strategy {
        strategy_id: String,
        outcome: EvaluationOutcome,
    },
    /// A planned armed protection arm, a reduce-only exit, in arm order.
    Protection { arm_id: String },
}

impl FlowSource {
    /// The plan item's identifier, for replay and observability.
    pub fn id(&self) -> &str {
        match self {
            Self::Strategy { strategy_id, .. } => strategy_id,
            Self::Protection { arm_id } => arm_id,
        }
    }

    /// True when this plan item is new risk (a buy).
    ///
    /// Mirrors [`EvaluationOutcome::is_entry`] without deciding it again: exits and holds
    /// are not entries, so an armed switch cannot reject them.
    pub fn is_entry(&self) -> bool {
        matches!(
            self,
            Self::Strategy {
                outcome: EvaluationOutcome::Buy,
                ..
            }
        )
    }
}

/// One driven work item: its plan source, its typed executor trace, and the switch reads
/// that produced the trace. Keeping the reads makes the result replayable from recorded
/// inputs (CONTRACT I9).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FlowStep {
    pub source: FlowSource,
    pub run: Run,
    pub kill_reads: Vec<KillRead>,
}

/// An input-level failure. This is a caller/arity error, not a money refusal: it names a
/// plan/input misalignment and drives nothing, rather than guessing which inputs belong
/// to which planned item.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FlowError {
    StrategyRunCount { planned: usize, supplied: usize },
    ProtectionRunCount { planned: usize, supplied: usize },
}

/// The deterministic, ordered trace of one composed tick.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FlowResult {
    /// One step per planned work item, in plan order: strategies then protection exits.
    /// Empty when `input_error` is set — a misaligned tick drives no intent at all.
    pub steps: Vec<FlowStep>,
    /// The input misalignment, if any.
    pub input_error: Option<FlowError>,
}

impl FlowResult {
    fn with_error(error: FlowError) -> Self {
        Self {
            steps: Vec::new(),
            input_error: Some(error),
        }
    }

    /// True when the tick was driven (inputs matched the plan).
    pub fn is_ok(&self) -> bool {
        self.input_error.is_none()
    }

    /// The typed refusal codes, in plan order, one per step that refused.
    pub fn refusals(&self) -> Vec<(&FlowSource, RefusalCode)> {
        self.steps
            .iter()
            .filter_map(|step| step.run.refused_code().map(|code| (&step.source, code)))
            .collect()
    }

    /// The plan sources whose intent reached the journal, in plan order.
    pub fn journaled(&self) -> Vec<&FlowSource> {
        self.steps
            .iter()
            .filter(|step| matches!(step.run.terminal, Terminal::Journaled { .. }))
            .map(|step| &step.source)
            .collect()
    }

    /// True when a new entry (a buy) settled. Used to assert the armed-switch rule: a
    /// tick planned while armed must never produce a journaled entry.
    pub fn any_entry_journaled(&self) -> bool {
        self.journaled().iter().any(|source| source.is_entry())
    }

    /// True when any reduce-only exit settled.
    pub fn any_exit_journaled(&self) -> bool {
        self.journaled()
            .iter()
            .any(|source| matches!(source, FlowSource::Protection { .. }))
    }
}

/// The ordered work list the flow will drive, derived solely from the tick plan.
/// Strategies come first in result order; protection arms follow in load order.
pub fn planned_work(tick_plan: &TickPlan) -> Vec<FlowSource> {
    let mut work = Vec::with_capacity(
        tick_plan.strategy_evaluations.len() + tick_plan.protection_pass.arm_ids.len(),
    );
    for evaluation in &tick_plan.strategy_evaluations {
        work.push(FlowSource::Strategy {
            strategy_id: evaluation.strategy_id.clone(),
            outcome: evaluation.outcome,
        });
    }
    for arm_id in &tick_plan.protection_pass.arm_ids {
        work.push(FlowSource::Protection {
            arm_id: arm_id.clone(),
        });
    }
    work
}

fn drive_step(source: FlowSource, planned: &PlannedRun) -> FlowStep {
    // Consume the pre-recorded reads in gate order. Once exhausted, a further entry read
    // is `Unavailable` (fail closed), never an invented "off". A sell consumes none.
    let mut reads = planned.kill_reads.iter().copied();
    let run = state_machine::drive(&planned.inputs, || {
        reads.next().map_or(Err(()), KillRead::into_result)
    });
    FlowStep {
        source,
        run,
        kill_reads: planned.kill_reads.clone(),
    }
}

/// Compose one tick: take each planned strategy evaluation and protection exit in order,
/// feed it through the executor state machine, and return the ordered typed trace.
///
/// The executor kernel preserves the one-write-path stage order and its typed refusals;
/// this function only sequences the plan items and pairs them with their recorded inputs.
pub fn run(tick_plan: &TickPlan, executor_inputs: &ExecutorInputs) -> FlowResult {
    let planned_strategies = &tick_plan.strategy_evaluations;
    let planned_arms = &tick_plan.protection_pass.arm_ids;

    if planned_strategies.len() != executor_inputs.strategy_runs.len() {
        return FlowResult::with_error(FlowError::StrategyRunCount {
            planned: planned_strategies.len(),
            supplied: executor_inputs.strategy_runs.len(),
        });
    }
    if planned_arms.len() != executor_inputs.protection_runs.len() {
        return FlowResult::with_error(FlowError::ProtectionRunCount {
            planned: planned_arms.len(),
            supplied: executor_inputs.protection_runs.len(),
        });
    }

    let mut steps = Vec::with_capacity(planned_strategies.len() + planned_arms.len());

    // Strategies first, in result order. An armed plan has no strategy evaluations, so
    // no new entry is driven; any that were planned would still face the executor's four
    // entry-only switch gates, which refuse a buy while armed.
    for (evaluation, planned) in planned_strategies
        .iter()
        .zip(&executor_inputs.strategy_runs)
    {
        steps.push(drive_step(
            FlowSource::Strategy {
                strategy_id: evaluation.strategy_id.clone(),
                outcome: evaluation.outcome,
            },
            planned,
        ));
    }

    // Protection exits after strategies, in arm order. These are reduce-only sells: the
    // state machine never reads the switch for them, so an armed tick still settles them
    // (ADR-0031/0034).
    for (arm_id, planned) in planned_arms.iter().zip(&executor_inputs.protection_runs) {
        steps.push(drive_step(
            FlowSource::Protection {
                arm_id: arm_id.clone(),
            },
            planned,
        ));
    }

    FlowResult {
        steps,
        input_error: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_sell_never_consumes_a_recorded_switch_read() {
        let step = drive_step(
            FlowSource::Protection {
                arm_id: "arm".to_owned(),
            },
            &PlannedRun::new(RunInputs::exiting(), vec![KillRead::Armed]),
        );
        // The exit journals even with an armed read recorded (and unused).
        assert!(matches!(step.run.terminal, Terminal::Journaled { .. }));
        assert_eq!(step.run.kill_read_count(), 0);
    }

    #[test]
    fn a_buy_running_out_of_recorded_reads_fails_closed() {
        let step = drive_step(
            FlowSource::Strategy {
                strategy_id: "s".to_owned(),
                outcome: EvaluationOutcome::Buy,
            },
            &PlannedRun::new(RunInputs::buying(), vec![]),
        );
        assert_eq!(
            step.run.refused_code(),
            Some(RefusalCode::KillSwitchUnavailable)
        );
    }

    #[test]
    fn misaligned_inputs_drive_nothing() {
        let plan = ezchain_scheduler::TickInput {
            kill_switch_armed: false,
            now: 0.0,
            strategies: vec![ezchain_scheduler::StrategyInput {
                id: "s".to_owned(),
                status: ezchain_scheduler::StrategyStatus::Active,
                last_run_at: None,
                interval_secs: 60.0,
                outcome: EvaluationOutcome::Buy,
            }],
            arms: vec![],
        }
        .plan();
        let result = run(
            &plan,
            &ExecutorInputs {
                strategy_runs: vec![],
                protection_runs: vec![],
            },
        );
        assert_eq!(
            result.input_error,
            Some(FlowError::StrategyRunCount {
                planned: 1,
                supplied: 0
            })
        );
        assert!(result.steps.is_empty());
    }
}
