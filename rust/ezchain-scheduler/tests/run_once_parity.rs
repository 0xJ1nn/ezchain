//! Recorded decision table for one scheduler tick, asserted against the Rust port.
//!
//! The oracle is `services/engine/src/ezchain_engine/scheduler.py::run_once` and the
//! engine tests that pin it. Each case below records the plan the Python pass produces
//! for a given input; the `view` of the Rust plan must match it exactly.
//!
//! Sources for the recorded cases:
//! * `tests/engine/test_scheduler.py::test_scheduler_run_once_with_no_active_strategies`,
//!   `::test_scheduler_run_once_covers_active_strategies_only`,
//!   `::test_scheduler_skips_not_due_strategies_without_a_run_row`,
//!   `::test_scheduler_skips_the_pass_without_touching_market_data_while_armed`,
//!   `::test_an_arm_inside_a_tick_is_obeyed_by_that_same_tick` (base commit bf64eab);
//! * `tests/engine/test_scheduler.py::test_scheduler_runs_an_armed_protection_exit_while_the_kill_switch_is_armed`
//!   on `fix/protection-runs-when-armed` (d9e4926) — the armed-switch fix required by
//!   ADR-0031/0034. That fix is not merged into the base commit; the table records the
//!   fixed behaviour, as explicitly required for this port.
//! * `ezchain_core::due::should_run` boundaries (the interval gate reused by `_due`), and
//!   the `except Exception` branch of `run_once` that appends no result for a crash.
//!
//! ADR-0049/0050 parity gate: these cases are the oracle until Rust passes them.

use ezchain_protection::ArmStatus;
use ezchain_scheduler::{
    evaluated_strategy_ids, ArmInput, EvaluationOutcome, PlannedAction, PlannedProtectionPass,
    StrategyInput, StrategyStatus, TickDecision, TickInput, TickPlan, TickReason,
};

// ---- compact, comparable view of a whole plan -----------------------------------------

#[derive(Debug, PartialEq, Eq)]
struct PlanView {
    skipped: bool,
    evaluations: Vec<(String, String)>,
    reasons: Vec<String>,
    arm_ids: Vec<String>,
    protection_reason: String,
    reduce_only_allowed: bool,
    actions: Vec<String>,
}

fn view(plan: &TickPlan) -> PlanView {
    let reasons = plan
        .decisions
        .iter()
        .map(|decision| match decision {
            TickDecision::StrategyPassSkipped { .. } => "KILL_SWITCH_ACTIVE:pass".to_string(),
            TickDecision::StrategySkipped {
                strategy_id,
                reason,
            } => format!("{}:{strategy_id}", reason.as_str()),
            TickDecision::StrategyEvaluationFailed {
                strategy_id,
                reason,
            } => format!("{}:{strategy_id}", reason.as_str()),
            TickDecision::StrategyPlanned {
                strategy_id,
                reason,
                ..
            } => format!("{}:{strategy_id}", reason.as_str()),
            TickDecision::ProtectionPassPlanned { reason } => {
                format!("{}:pass", reason.as_str())
            }
            TickDecision::ArmPlanned { arm_id, reason } => {
                format!("{}:{arm_id}", reason.as_str())
            }
            TickDecision::ArmSkipped { arm_id, reason } => {
                format!("{}:{arm_id}", reason.as_str())
            }
        })
        .collect();
    let actions = plan
        .actions
        .iter()
        .map(|action| match action {
            PlannedAction::EvaluateStrategy {
                strategy_id,
                reason,
            } => format!("{}:{strategy_id}", reason.as_str()),
            PlannedAction::EvaluateProtectionArms {
                arm_ids, reason, ..
            } => format!("{}:[{}]", reason.as_str(), arm_ids.join(",")),
        })
        .collect();
    PlanView {
        skipped: plan.strategy_pass_skipped,
        evaluations: plan
            .strategy_evaluations
            .iter()
            .map(|evaluation| {
                (
                    evaluation.strategy_id.clone(),
                    evaluation.outcome.as_str().to_string(),
                )
            })
            .collect(),
        reasons,
        arm_ids: plan.protection_pass.arm_ids.clone(),
        protection_reason: plan.protection_pass.reason.as_str().to_string(),
        reduce_only_allowed: plan.protection_pass.reduce_only_exit_allowed,
        actions,
    }
}

fn expected(
    skipped: bool,
    evaluations: &[(&str, &str)],
    reasons: &[&str],
    arm_ids: &[&str],
    protection_reason: &str,
    actions: &[&str],
) -> PlanView {
    PlanView {
        skipped,
        evaluations: evaluations
            .iter()
            .map(|(id, outcome)| ((*id).to_string(), (*outcome).to_string()))
            .collect(),
        reasons: reasons.iter().map(|reason| (*reason).to_string()).collect(),
        arm_ids: arm_ids.iter().map(|id| (*id).to_string()).collect(),
        protection_reason: protection_reason.to_string(),
        reduce_only_allowed: true, // the ported gate always allows a reduce-only exit
        actions: actions.iter().map(|action| (*action).to_string()).collect(),
    }
}

// ---- input builders --------------------------------------------------------------------

fn active(id: &str, outcome: EvaluationOutcome) -> StrategyInput {
    StrategyInput {
        id: id.to_string(),
        status: StrategyStatus::Active,
        last_run_at: None,
        interval_secs: 3600.0,
        outcome,
    }
}

fn with_due(
    id: &str,
    outcome: EvaluationOutcome,
    last_run_at: f64,
    interval: f64,
) -> StrategyInput {
    let mut strategy = active(id, outcome);
    strategy.last_run_at = Some(last_run_at);
    strategy.interval_secs = interval;
    strategy
}

fn paused(id: &str) -> StrategyInput {
    StrategyInput {
        status: StrategyStatus::Inactive,
        ..active(id, EvaluationOutcome::Buy)
    }
}

fn arm(id: &str, status: ArmStatus) -> ArmInput {
    ArmInput {
        id: id.to_string(),
        status,
    }
}

fn input(armed: bool, now: f64, strategies: Vec<StrategyInput>, arms: Vec<ArmInput>) -> TickInput {
    TickInput {
        kill_switch_armed: armed,
        now,
        strategies,
        arms,
    }
}

// ---- the recorded decision table -------------------------------------------------------

struct Case {
    label: &'static str,
    input: TickInput,
    expected: PlanView,
}

fn cases() -> Vec<Case> {
    vec![
        Case {
            label: "no active strategies, switch disarmed, no arms",
            input: input(false, 0.0, vec![], vec![]),
            expected: expected(
                false,
                &[],
                &["PROTECTION_PASS:pass"],
                &[],
                "PROTECTION_PASS",
                &["PROTECTION_PASS:[]"],
            ),
        },
        Case {
            label: "two active due strategies plus a paused one, disarmed",
            input: input(
                false,
                0.0,
                vec![
                    active("s1", EvaluationOutcome::Buy),
                    paused("s2"),
                    active("s3", EvaluationOutcome::Hold),
                ],
                vec![],
            ),
            expected: expected(
                false,
                &[("s1", "buy"), ("s3", "hold")],
                &[
                    "STRATEGY_EVALUATE:s1",
                    "PAUSED:s2",
                    "STRATEGY_EVALUATE:s3",
                    "PROTECTION_PASS:pass",
                ],
                &[],
                "PROTECTION_PASS",
                &[
                    "STRATEGY_EVALUATE:s1",
                    "STRATEGY_EVALUATE:s3",
                    "PROTECTION_PASS:[]",
                ],
            ),
        },
        Case {
            label: "active strategy whose interval has not elapsed is skipped without a result",
            input: input(
                false,
                59.0,
                vec![with_due("s1", EvaluationOutcome::Buy, 0.0, 60.0)],
                vec![],
            ),
            expected: expected(
                false,
                &[],
                &["INTERVAL_NOT_ELAPSED:s1", "PROTECTION_PASS:pass"],
                &[],
                "PROTECTION_PASS",
                &["PROTECTION_PASS:[]"],
            ),
        },
        Case {
            label: "exact interval boundary opens the gate (inclusive)",
            input: input(
                false,
                60.0,
                vec![with_due("s1", EvaluationOutcome::Buy, 0.0, 60.0)],
                vec![],
            ),
            expected: expected(
                false,
                &[("s1", "buy")],
                &["STRATEGY_EVALUATE:s1", "PROTECTION_PASS:pass"],
                &[],
                "PROTECTION_PASS",
                &["STRATEGY_EVALUATE:s1", "PROTECTION_PASS:[]"],
            ),
        },
        Case {
            label: "first run ignores now and even a pathological interval",
            input: input(
                false,
                -3600.0,
                vec![{
                    let mut strategy = active("s1", EvaluationOutcome::Buy);
                    strategy.interval_secs = -300.0;
                    strategy
                }],
                vec![],
            ),
            expected: expected(
                false,
                &[("s1", "buy")],
                &["STRATEGY_EVALUATE:s1", "PROTECTION_PASS:pass"],
                &[],
                "PROTECTION_PASS",
                &["STRATEGY_EVALUATE:s1", "PROTECTION_PASS:[]"],
            ),
        },
        Case {
            label: "clock moved backwards never opens the gate",
            input: input(
                false,
                59.0,
                vec![with_due("s1", EvaluationOutcome::Buy, 60.0, 60.0)],
                vec![],
            ),
            expected: expected(
                false,
                &[],
                &["INTERVAL_NOT_ELAPSED:s1", "PROTECTION_PASS:pass"],
                &[],
                "PROTECTION_PASS",
                &["PROTECTION_PASS:[]"],
            ),
        },
        Case {
            label: "armed switch skips the whole strategy pass before any read or run row",
            input: input(
                true,
                0.0,
                vec![
                    active("s1", EvaluationOutcome::Buy),
                    active("s2", EvaluationOutcome::Sell),
                ],
                vec![],
            ),
            expected: expected(
                true,
                &[],
                &[
                    "KILL_SWITCH_ACTIVE:pass",
                    "PROTECTION_PASS_REDUCE_ONLY:pass",
                ],
                &[],
                "PROTECTION_PASS_REDUCE_ONLY",
                &["PROTECTION_PASS_REDUCE_ONLY:[]"],
            ),
        },
        Case {
            label: "armed switch with an armed arm still fires a reduce-only exit (fixed case)",
            input: input(
                true,
                0.0,
                vec![active("s1", EvaluationOutcome::Buy)],
                vec![
                    arm("arm-1", ArmStatus::Armed),
                    arm("arm-2", ArmStatus::Triggered),
                ],
            ),
            expected: expected(
                true,
                &[],
                &[
                    "KILL_SWITCH_ACTIVE:pass",
                    "PROTECTION_PASS_REDUCE_ONLY:pass",
                    "ARM_EVALUATE:arm-1",
                    "NOT_ARMED:arm-2",
                ],
                &["arm-1"],
                "PROTECTION_PASS_REDUCE_ONLY",
                &["PROTECTION_PASS_REDUCE_ONLY:[arm-1]"],
            ),
        },
        Case {
            label: "a crashed evaluation appends no result and never stops the pass",
            input: input(
                false,
                0.0,
                vec![
                    active("s1", EvaluationOutcome::Buy),
                    active("s2", EvaluationOutcome::Failed),
                    active("s3", EvaluationOutcome::Sell),
                ],
                vec![],
            ),
            expected: expected(
                false,
                &[("s1", "buy"), ("s3", "sell")],
                &[
                    "STRATEGY_EVALUATE:s1",
                    "EVALUATION_FAILED:s2",
                    "STRATEGY_EVALUATE:s3",
                    "PROTECTION_PASS:pass",
                ],
                &[],
                "PROTECTION_PASS",
                &[
                    "STRATEGY_EVALUATE:s1",
                    "STRATEGY_EVALUATE:s3",
                    "PROTECTION_PASS:[]",
                ],
            ),
        },
        Case {
            label: "only armed arms are evaluated; other arm states are skipped",
            input: input(
                false,
                0.0,
                vec![active("s1", EvaluationOutcome::Hold)],
                vec![
                    arm("a1", ArmStatus::Triggered),
                    arm("a2", ArmStatus::Cancelled),
                    arm("a3", ArmStatus::Expired),
                    arm("a4", ArmStatus::Armed),
                ],
            ),
            expected: expected(
                false,
                &[("s1", "hold")],
                &[
                    "STRATEGY_EVALUATE:s1",
                    "PROTECTION_PASS:pass",
                    "NOT_ARMED:a1",
                    "NOT_ARMED:a2",
                    "NOT_ARMED:a3",
                    "ARM_EVALUATE:a4",
                ],
                &["a4"],
                "PROTECTION_PASS",
                &["STRATEGY_EVALUATE:s1", "PROTECTION_PASS:[a4]"],
            ),
        },
        Case {
            label: "mixed tick keeps created_at order in both passes",
            input: input(
                false,
                59.0,
                vec![
                    paused("s1"),
                    with_due("s2", EvaluationOutcome::Buy, 0.0, 60.0),
                    active("s3", EvaluationOutcome::Buy),
                    active("s4", EvaluationOutcome::Failed),
                    active("s5", EvaluationOutcome::Sell),
                ],
                vec![
                    arm("a1", ArmStatus::Armed),
                    arm("a2", ArmStatus::Triggered),
                    arm("a3", ArmStatus::Armed),
                ],
            ),
            expected: expected(
                false,
                &[("s3", "buy"), ("s5", "sell")],
                &[
                    "PAUSED:s1",
                    "INTERVAL_NOT_ELAPSED:s2",
                    "STRATEGY_EVALUATE:s3",
                    "EVALUATION_FAILED:s4",
                    "STRATEGY_EVALUATE:s5",
                    "PROTECTION_PASS:pass",
                    "ARM_EVALUATE:a1",
                    "NOT_ARMED:a2",
                    "ARM_EVALUATE:a3",
                ],
                &["a1", "a3"],
                "PROTECTION_PASS",
                &[
                    "STRATEGY_EVALUATE:s3",
                    "STRATEGY_EVALUATE:s5",
                    "PROTECTION_PASS:[a1,a3]",
                ],
            ),
        },
        Case {
            label: "armed switch with no strategies still runs protection",
            input: input(true, 0.0, vec![], vec![arm("a1", ArmStatus::Armed)]),
            expected: expected(
                true,
                &[],
                &[
                    "KILL_SWITCH_ACTIVE:pass",
                    "PROTECTION_PASS_REDUCE_ONLY:pass",
                    "ARM_EVALUATE:a1",
                ],
                &["a1"],
                "PROTECTION_PASS_REDUCE_ONLY",
                &["PROTECTION_PASS_REDUCE_ONLY:[a1]"],
            ),
        },
    ]
}

#[test]
fn every_recorded_run_once_case_matches() {
    let cases = cases();
    for case in &cases {
        let plan = case.input.plan();
        assert_eq!(view(&plan), case.expected, "case: {}", case.label);
        // The result order must be the order of planned evaluations (no re-sorting).
        assert_eq!(
            evaluated_strategy_ids(&plan),
            plan.strategy_evaluations
                .iter()
                .map(|evaluation| evaluation.strategy_id.as_str())
                .collect::<Vec<_>>(),
            "case: {}",
            case.label
        );
    }
}

#[test]
fn the_table_has_the_recorded_number_of_cases() {
    // Recorded from the Python oracle; a silent edit to the table must fail the gate.
    assert_eq!(cases().len(), 12);
}

#[test]
fn protection_always_runs_after_strategies_even_when_armed() {
    for case in cases() {
        let plan = case.input.plan();
        assert!(
            plan.protection_pass.reduce_only_exit_allowed,
            "case: {}",
            case.label
        );
        let pass_index = plan
            .decisions
            .iter()
            .position(|decision| matches!(decision, TickDecision::ProtectionPassPlanned { .. }))
            .expect("protection pass must be planned");
        assert!(
            plan.decisions[..pass_index]
                .iter()
                .all(|decision| !matches!(decision, TickDecision::ArmPlanned { .. })),
            "case: {}",
            case.label
        );
        let actions = &plan.actions;
        let protection_index = actions
            .iter()
            .position(|action| matches!(action, PlannedAction::EvaluateProtectionArms { .. }))
            .expect("protection action must be planned");
        assert!(
            actions[..protection_index]
                .iter()
                .all(|action| matches!(action, PlannedAction::EvaluateStrategy { .. })),
            "case: {}",
            case.label
        );
    }
}

#[test]
fn the_fixed_case_has_no_entry_and_exactly_one_reduce_only_action() {
    let case = cases()
        .into_iter()
        .find(|case| case.label.contains("fixed case"))
        .expect("the armed-switch regression case must be recorded");
    let plan = case.input.plan();
    assert!(plan.strategy_pass_skipped);
    assert!(plan.strategy_evaluations.is_empty());
    assert!(!plan
        .strategy_evaluations
        .iter()
        .any(|evaluation| evaluation.outcome.is_entry()));
    let PlannedProtectionPass {
        arm_ids,
        kill_switch_armed,
        reduce_only_exit_allowed,
        reason,
    } = &plan.protection_pass;
    assert_eq!(arm_ids, &["arm-1"]);
    assert!(*kill_switch_armed);
    assert!(*reduce_only_exit_allowed);
    assert_eq!(*reason, TickReason::ProtectionPassWhileArmed);
}
