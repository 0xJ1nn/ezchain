//! Recorded Python ordering/refusal cases for the composed tick, asserted against `run`.
//!
//! The oracle is the real call relationship between
//! `services/engine/src/ezchain_engine/scheduler.py::StrategyScheduler.run_once` →
//! `evaluate_strategy` / `protection.evaluate_all_arms` → `OrderExecutor.place_order` →
//! `services/executor/src/ezchain_executor/pipeline.py::ExecutionPipeline.run`, and the
//! tests that pin them (`tests/engine/test_scheduler.py`, `tests/executor/test_pipeline.py`).
//!
//! Sources for the recorded cases:
//! * `test_scheduler_run_once_with_no_active_strategies` — no work, no trace.
//! * `test_scheduler_run_once_covers_active_strategies_only` — active/due strategies in
//!   `created_at` order, then the protection pass after them.
//! * `test_scheduler_runs_an_armed_protection_exit_while_the_kill_switch_is_armed` on
//!   `fix/protection-runs-when-armed` (d9e4926) — the armed-switch case required by
//!   ADR-0031/0034. The base commit still returns before the protection pass; the table
//!   records the fixed behaviour, as required for this port.
//! * `test_an_arm_between_quote_and_sign_refuses_before_the_key_is_touched` (ADR-0045)
//!   and `test_an_unreadable_kill_switch_fails_closed` — typed entry refusals that do not
//!   stop later strategies or the protection exit.
//! * `test_an_unknown_buy_mark_is_denied_before_the_venue` and `order_gate`'s
//!   `EXIT_NO_ROUTE` — fail-closed typed refusals on both paths.
//!
//! ADR-0049/0050 parity gate: these cases are the oracle until Rust passes them.

use ezchain_executor::gates::{RefusalCode, RiskVerdict};
use ezchain_executor::state_machine::{RunInputs, Stage, Terminal};
use ezchain_flow::{
    planned_work, run, ExecutorInputs, FlowResult, FlowSource, KillRead, PlannedRun,
};
use ezchain_protection::ArmStatus;
use ezchain_scheduler::{
    ArmInput, EvaluationOutcome, StrategyInput, StrategyStatus, TickInput, TickPlan,
};

// ---- compact, comparable view of a whole composed trace --------------------------------

#[derive(Debug, PartialEq, Eq)]
struct StepView {
    source: String,
    stages: Vec<&'static str>,
    terminal: String,
    kill_reads: usize,
}

fn step(source: &str, stages: &[&'static str], terminal: &str, reads: usize) -> StepView {
    StepView {
        source: source.to_owned(),
        stages: stages.to_vec(),
        terminal: terminal.to_owned(),
        kill_reads: reads,
    }
}

fn trace(result: &FlowResult) -> Vec<StepView> {
    assert!(result.is_ok(), "a recorded case must have aligned inputs");
    result
        .steps
        .iter()
        .map(|step| {
            let kind = match &step.source {
                FlowSource::Strategy { .. } => "S",
                FlowSource::Protection { .. } => "P",
            };
            let terminal = match step.run.terminal {
                Terminal::Replay => "replay".to_owned(),
                Terminal::Journaled { partial: false } => "filled".to_owned(),
                Terminal::Journaled { partial: true } => "partially_filled".to_owned(),
                Terminal::Refused { code, .. } => format!("refused:{}", code.as_str()),
            };
            StepView {
                source: format!("{}:{}", kind, step.source.id()),
                stages: step.run.path.iter().map(|s| s.as_str()).collect(),
                terminal,
                kill_reads: step.run.kill_read_count(),
            }
        })
        .collect()
}

const FULL: &[&str] = &[
    "intent",
    "risk_decision",
    "quote",
    "order",
    "fill",
    "journal",
];
const THROUGH_ORDER: &[&str] = &["intent", "risk_decision", "quote", "order"];
const THROUGH_QUOTE: &[&str] = &["intent", "risk_decision", "quote"];
const INTENT_ONLY: &[&str] = &["intent"];

// ---- input builders --------------------------------------------------------------------

fn active(id: &str, outcome: EvaluationOutcome) -> StrategyInput {
    StrategyInput {
        id: id.to_owned(),
        status: StrategyStatus::Active,
        last_run_at: None,
        interval_secs: 60.0,
        outcome,
    }
}

fn paused(id: &str) -> StrategyInput {
    StrategyInput {
        status: StrategyStatus::Inactive,
        ..active(id, EvaluationOutcome::Buy)
    }
}

fn arm(id: &str, status: ArmStatus) -> ArmInput {
    ArmInput {
        id: id.to_owned(),
        status,
    }
}

fn plan(armed: bool, strategies: Vec<StrategyInput>, arms: Vec<ArmInput>) -> TickPlan {
    TickInput {
        kill_switch_armed: armed,
        now: 0.0,
        strategies,
        arms,
    }
    .plan()
}

fn disarmed(n: usize) -> Vec<KillRead> {
    vec![KillRead::Disarmed; n]
}

fn buy(key: &str, reads: Vec<KillRead>) -> PlannedRun {
    let mut inputs = RunInputs::buying();
    inputs.key = key.to_owned();
    PlannedRun::new(inputs, reads)
}

/// An ordinary strategy sell, not an arm exit: still a sell, so it never reads the switch.
fn sell(key: &str) -> PlannedRun {
    let mut inputs = RunInputs::exiting();
    inputs.key = key.to_owned();
    inputs.reduce_only = false;
    PlannedRun::new(inputs, vec![])
}

/// A protection arm's reduce-only exit.
fn exit(key: &str) -> PlannedRun {
    let mut inputs = RunInputs::exiting();
    inputs.key = key.to_owned();
    PlannedRun::new(inputs, vec![])
}

// ---- the recorded decision table -------------------------------------------------------

struct Case {
    label: &'static str,
    plan: TickPlan,
    inputs: ExecutorInputs,
    expected: Vec<StepView>,
}

fn cases() -> Vec<Case> {
    vec![
        Case {
            label: "no active strategies, switch disarmed, no arms",
            plan: plan(false, vec![], vec![]),
            inputs: ExecutorInputs {
                strategy_runs: vec![],
                protection_runs: vec![],
            },
            expected: vec![],
        },
        Case {
            label: "active/due strategies then the protection pass, in load order",
            plan: plan(
                false,
                vec![
                    active("s1", EvaluationOutcome::Buy),
                    paused("s2"),
                    active("s3", EvaluationOutcome::Sell),
                ],
                vec![arm("a1", ArmStatus::Armed)],
            ),
            inputs: ExecutorInputs {
                strategy_runs: vec![buy("s1", disarmed(4)), sell("s3")],
                protection_runs: vec![exit("a1")],
            },
            expected: vec![
                step("S:s1", FULL, "filled", 4),
                step("S:s3", FULL, "filled", 0),
                step("P:a1", FULL, "filled", 0),
            ],
        },
        Case {
            label: "armed switch: no entries planned, reduce-only exit still settles (fixed case)",
            plan: plan(
                true,
                vec![active("s1", EvaluationOutcome::Buy)],
                vec![arm("a1", ArmStatus::Armed), arm("a2", ArmStatus::Triggered)],
            ),
            inputs: ExecutorInputs {
                strategy_runs: vec![],
                protection_runs: vec![exit("a1")],
            },
            expected: vec![step("P:a1", FULL, "filled", 0)],
        },
        Case {
            label: "arm between quote and sign refuses the entry at sign, exit still settles",
            plan: plan(
                false,
                vec![active("s1", EvaluationOutcome::Buy)],
                vec![arm("a1", ArmStatus::Armed)],
            ),
            inputs: ExecutorInputs {
                strategy_runs: vec![buy(
                    "s1",
                    vec![
                        KillRead::Disarmed,
                        KillRead::Disarmed,
                        KillRead::Armed,
                        KillRead::Disarmed,
                    ],
                )],
                protection_runs: vec![exit("a1")],
            },
            expected: vec![
                step("S:s1", THROUGH_ORDER, "refused:KILL_SWITCH_ACTIVE", 3),
                step("P:a1", FULL, "filled", 0),
            ],
        },
        Case {
            label: "a pre-submit denial never stops later strategies or the exit",
            plan: plan(
                false,
                vec![
                    active("s1", EvaluationOutcome::Buy),
                    active("s2", EvaluationOutcome::Sell),
                    active("s3", EvaluationOutcome::Buy),
                ],
                vec![arm("a1", ArmStatus::Armed)],
            ),
            inputs: ExecutorInputs {
                strategy_runs: {
                    let mut timed_out = buy("s1", disarmed(4));
                    timed_out.inputs.submit = Err(RefusalCode::Timeout);
                    vec![timed_out, sell("s2"), buy("s3", disarmed(4))]
                },
                protection_runs: vec![exit("a1")],
            },
            expected: vec![
                step("S:s1", THROUGH_ORDER, "refused:TIMEOUT", 4),
                step("S:s2", FULL, "filled", 0),
                step("S:s3", FULL, "filled", 4),
                step("P:a1", FULL, "filled", 0),
            ],
        },
        Case {
            label: "fail-closed typed refusals on both paths",
            plan: plan(
                false,
                vec![active("s1", EvaluationOutcome::Buy)],
                vec![arm("a1", ArmStatus::Armed)],
            ),
            inputs: ExecutorInputs {
                strategy_runs: {
                    let mut unknown = buy("s1", disarmed(4));
                    unknown.inputs.pre_quote_risk = RiskVerdict::Unknown;
                    vec![unknown]
                },
                protection_runs: {
                    let mut no_route = exit("a1");
                    no_route.inputs.quote.route_ok = false;
                    vec![no_route]
                },
            },
            expected: vec![
                step("S:s1", INTENT_ONLY, "refused:MISSING_RISK_INPUTS", 0),
                step("P:a1", THROUGH_QUOTE, "refused:EXIT_NO_ROUTE", 0),
            ],
        },
    ]
}

#[test]
fn every_recorded_flow_case_matches() {
    for case in &cases() {
        let result = run(&case.plan, &case.inputs);
        assert_eq!(trace(&result), case.expected, "case: {}", case.label);
    }
}

#[test]
fn the_table_has_the_recorded_number_of_cases() {
    // Recorded from the Python oracle; a silent edit to the table must fail the gate.
    assert_eq!(cases().len(), 6);
}

#[test]
fn the_armed_switch_case_has_no_entry_and_one_settled_reduce_only_exit() {
    let case = cases()
        .into_iter()
        .find(|case| case.label.contains("fixed case"))
        .expect("the armed-switch regression case must be recorded");
    assert!(case.plan.strategy_pass_skipped);
    let result = run(&case.plan, &case.inputs);
    assert!(!result.any_entry_journaled(), "case: {}", case.label);
    assert!(result.any_exit_journaled(), "case: {}", case.label);
    assert_eq!(result.refusals().len(), 0);
    assert_eq!(
        result
            .journaled()
            .iter()
            .map(|s| s.id())
            .collect::<Vec<_>>(),
        vec!["a1"]
    );
}

#[test]
fn stage_order_is_the_one_write_path_for_every_step() {
    for case in &cases() {
        let result = run(&case.plan, &case.inputs);
        for item in &result.steps {
            let indices: Vec<usize> = item
                .run
                .path
                .iter()
                .map(|stage| Stage::SEQUENCE.iter().position(|s| s == stage).unwrap())
                .collect();
            // Every produced stage is a prefix of the canonical six-stage path.
            assert_eq!(
                indices,
                (0..indices.len()).collect::<Vec<_>>(),
                "case: {}",
                case.label
            );
            assert!(indices.len() <= Stage::SEQUENCE.len());
        }
    }
}

#[test]
fn the_driven_order_is_exactly_the_planned_order() {
    for case in &cases() {
        let result = run(&case.plan, &case.inputs);
        let planned: Vec<String> = planned_work(&case.plan)
            .iter()
            .map(FlowSource::id)
            .map(str::to_owned)
            .collect();
        let driven: Vec<String> = result
            .steps
            .iter()
            .map(|item| item.source.id().to_owned())
            .collect();
        assert_eq!(driven, planned, "case: {}", case.label);
    }
}

#[test]
fn the_same_plan_and_inputs_produce_an_identical_trace() {
    for case in &cases() {
        assert_eq!(
            run(&case.plan, &case.inputs),
            run(&case.plan, &case.inputs),
            "case: {}",
            case.label
        );
    }
}
