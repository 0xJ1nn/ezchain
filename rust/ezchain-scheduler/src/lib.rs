//! Pure orchestration of one engine scheduler tick, ported from
//! `services/engine/src/ezchain_engine/scheduler.py::StrategyScheduler.run_once`.
//!
//! # What this crate decides
//!
//! Given a set of strategies with their due inputs, the kill-switch state read at pass
//! start, and a set of protection arms, compute the ordered tick plan:
//!
//! * which strategies are evaluated, in the order their results are appended;
//! * whether the whole strategy pass is skipped (armed switch, no market read, no run
//!   row, no interval consumed);
//! * that the protection pass always follows the strategy pass — **including while the
//!   switch is armed**, because a switch refuses new entries, never a reduce-only exit;
//! * a typed reason for every plan and skip, and the opaque side effects the Python
//!   control plane will perform.
//!
//! # What stays in Python
//!
//! Everything that touches the world: the clock, the session/DB reads, market snapshots,
//! ledger replay, the strategy and arm evaluators, order placement, optimistic arm writes,
//! event journaling, logging and the per-order re-read of the switch. The side effects
//! planned here are opaque values; this crate performs none of them.
//!
//! # Reused kernels (never reimplemented here)
//!
//! * [`ezchain_core::due::should_run_exact`] decides each strategy's interval gate. This
//!   crate supplies the caller's instants in seconds and the resolved interval; Python's
//!   `_due` fallback for invalid params happens before this boundary, as in the original.
//! * [`ezchain_protection::entry_gate_allows`] with [`IntentDirection::ReduceOnlyExit`]
//!   proves the armed-switch case: an armed protection exit is never vetoed.
//! * `ezchain_core::freshness_policy` is deliberately **not** consulted: a stale or
//!   unknown mark is decided inside the strategy and arm evaluators (already ported in
//!   `ezchain-core`/`ezchain-protection`), not by the scheduler pass. No mark observation
//!   crosses this boundary, so classifying one here would invent behaviour the Python
//!   `run_once` does not have.
//!
//! # Oracle for the armed-switch case
//!
//! The regression case (`switch armed + armed arm => reduce-only exit`) is the fixed
//! behaviour recorded on `fix/protection-runs-when-armed` (d9e4926) and required by
//! ADR-0031/0034. `main` at the base of this branch still returns before its protection
//! pass while armed; that is the bug the fix corrects. The recorded table in
//! `tests/run_once_parity.rs` asserts the fixed behaviour, with the other cases taken
//! from the scheduler tests on the base commit.

#![forbid(unsafe_code)]

use ezchain_core::due::{should_run_exact, Seconds};
use ezchain_protection::{entry_gate_allows, ArmStatus, IntentDirection};

/// A strategy's lifecycle state as the pass sees it. Only `active` rows are loaded and
/// evaluated; everything else (paused, deleted between the id read and the row load) is
/// skipped with no run row.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum StrategyStatus {
    Active,
    Inactive,
}

impl StrategyStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Inactive => "inactive",
        }
    }

    pub fn is_active(self) -> bool {
        self == Self::Active
    }
}

/// The opaque result a strategy evaluation would produce.
///
/// The evaluator itself owns the market read, ledger replay and order intent and is out
/// of scope. The planner only needs whether the evaluation appends a `RunResult`:
/// `Failed` models the `except Exception` branch of `run_once`, which logs and continues
/// without appending. Buying and selling are the two risk-increasing outcomes.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum EvaluationOutcome {
    Buy,
    Sell,
    Hold,
    Failed,
}

impl EvaluationOutcome {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Buy => "buy",
            Self::Sell => "sell",
            Self::Hold => "hold",
            Self::Failed => "EVALUATION_FAILED",
        }
    }

    /// Python appends a `RunResult` for every outcome except a crashed evaluation.
    pub fn produces_result(self) -> bool {
        !matches!(self, Self::Failed)
    }

    /// A buy is new risk; a sell is bounded by the held quantity. Mirrors the direction
    /// the order service gates (ADR-0034), without placing an order.
    pub fn is_entry(self) -> bool {
        self == Self::Buy
    }
}

/// One strategy in the tick, in the order `run_once` loads ids (`created_at`).
#[derive(Clone, Debug, PartialEq)]
pub struct StrategyInput {
    pub id: String,
    pub status: StrategyStatus,
    /// Last decision instant on the injected clock; `None` means it never ran.
    pub last_run_at: Option<Seconds>,
    /// The resolved cooldown in seconds (Python's `interval_minutes * 60`).
    pub interval_secs: Seconds,
    /// What the out-of-scope evaluator would return for this strategy.
    pub outcome: EvaluationOutcome,
}

impl StrategyInput {
    /// The interval gate, computed by the already-ported kernel — never reimplemented.
    pub fn is_due(&self, now: Seconds) -> bool {
        should_run_exact(self.last_run_at, now, self.interval_secs)
    }
}

/// One protection arm in the tick, in the order `evaluate_all_arms` loads ids.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArmInput {
    pub id: String,
    /// Reuses the `ezchain-protection` lifecycle vocabulary: only `Armed` is evaluated.
    pub status: ArmStatus,
}

/// Every input the pure orchestration reads. There is no clock, I/O or ambient state.
#[derive(Clone, Debug, PartialEq)]
pub struct TickInput {
    /// The kill switch read once at pass start: `getattr(executor, "active", False)`.
    pub kill_switch_armed: bool,
    /// Injected current instant, on the same clock as `StrategyInput.last_run_at`.
    pub now: Seconds,
    pub strategies: Vec<StrategyInput>,
    pub arms: Vec<ArmInput>,
}

/// Typed reason codes carried by planned actions and by the decision trail.
///
/// Pass/skip tokens reuse the Python vocabulary where one exists: `KILL_SWITCH_ACTIVE`
/// for the strategy-level switch, `PAUSED`, `INTERVAL_NOT_ELAPSED`, `NOT_ARMED` and
/// `EVALUATION_FAILED`. The three orchestration-only tokens name the two pass decisions
/// and the planned strategy evaluation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum TickReason {
    StrategyPassSkipped,
    StrategySkippedNotActive,
    StrategySkippedNotDue,
    StrategyEvaluate,
    StrategyEvaluationFailed,
    ProtectionPass,
    ProtectionPassWhileArmed,
    ArmEvaluate,
    ArmSkippedNotArmed,
}

impl TickReason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::StrategyPassSkipped => "KILL_SWITCH_ACTIVE",
            Self::StrategySkippedNotActive => "PAUSED",
            Self::StrategySkippedNotDue => "INTERVAL_NOT_ELAPSED",
            Self::StrategyEvaluate => "STRATEGY_EVALUATE",
            Self::StrategyEvaluationFailed => "EVALUATION_FAILED",
            Self::ProtectionPass => "PROTECTION_PASS",
            Self::ProtectionPassWhileArmed => "PROTECTION_PASS_REDUCE_ONLY",
            Self::ArmEvaluate => "ARM_EVALUATE",
            Self::ArmSkippedNotArmed => "NOT_ARMED",
        }
    }
}

/// A strategy evaluation placed in the result order.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StrategyEvaluation {
    pub strategy_id: String,
    pub outcome: EvaluationOutcome,
}

/// The protection pass the Python runner will perform after strategies.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PlannedProtectionPass {
    /// Armed arm ids in load order; only these get a market read and possible exit.
    pub arm_ids: Vec<String>,
    pub kill_switch_armed: bool,
    /// `entry_gate_allows(armed, ReduceOnlyExit)` — true in both switch states.
    pub reduce_only_exit_allowed: bool,
    pub reason: TickReason,
}

/// An opaque side effect. Nothing here executes it; the Python control plane does.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PlannedAction {
    EvaluateStrategy {
        strategy_id: String,
        reason: TickReason,
    },
    EvaluateProtectionArms {
        arm_ids: Vec<String>,
        kill_switch_armed: bool,
        reduce_only_exit_allowed: bool,
        reason: TickReason,
    },
}

/// One recorded decision, in the order the Python pass reaches it.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum TickDecision {
    StrategyPassSkipped {
        reason: TickReason,
    },
    StrategySkipped {
        strategy_id: String,
        reason: TickReason,
    },
    StrategyEvaluationFailed {
        strategy_id: String,
        reason: TickReason,
    },
    StrategyPlanned {
        strategy_id: String,
        outcome: EvaluationOutcome,
        reason: TickReason,
    },
    ProtectionPassPlanned {
        reason: TickReason,
    },
    ArmPlanned {
        arm_id: String,
        reason: TickReason,
    },
    ArmSkipped {
        arm_id: String,
        reason: TickReason,
    },
}

/// The complete plan for one tick.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TickPlan {
    /// True when the switch was armed at pass start, so the strategy pass was skipped.
    pub strategy_pass_skipped: bool,
    /// Strategy evaluations in the order `run_once` appends their results.
    pub strategy_evaluations: Vec<StrategyEvaluation>,
    /// Always planned, after strategies, even while the switch is armed.
    pub protection_pass: PlannedProtectionPass,
    /// Every decision in deterministic order, for replay and observability.
    pub decisions: Vec<TickDecision>,
    /// Opaque side effects in the order the Python control plane will perform them.
    pub actions: Vec<PlannedAction>,
}

impl TickInput {
    /// Compute the tick plan. Pure: no I/O, no clock, no order placement, no mutations
    /// beyond the returned plan.
    pub fn plan(&self) -> TickPlan {
        let mut decisions = Vec::new();
        let mut evaluations = Vec::new();
        let mut actions = Vec::new();

        if self.kill_switch_armed {
            // The armed pass writes no hold rows and touches no market data, but it does
            // not return: the protection pass still follows below.
            decisions.push(TickDecision::StrategyPassSkipped {
                reason: TickReason::StrategyPassSkipped,
            });
        } else {
            for strategy in &self.strategies {
                if !strategy.status.is_active() {
                    decisions.push(TickDecision::StrategySkipped {
                        strategy_id: strategy.id.clone(),
                        reason: TickReason::StrategySkippedNotActive,
                    });
                    continue;
                }
                if !strategy.is_due(self.now) {
                    decisions.push(TickDecision::StrategySkipped {
                        strategy_id: strategy.id.clone(),
                        reason: TickReason::StrategySkippedNotDue,
                    });
                    continue;
                }
                if !strategy.outcome.produces_result() {
                    decisions.push(TickDecision::StrategyEvaluationFailed {
                        strategy_id: strategy.id.clone(),
                        reason: TickReason::StrategyEvaluationFailed,
                    });
                    continue;
                }
                decisions.push(TickDecision::StrategyPlanned {
                    strategy_id: strategy.id.clone(),
                    outcome: strategy.outcome,
                    reason: TickReason::StrategyEvaluate,
                });
                evaluations.push(StrategyEvaluation {
                    strategy_id: strategy.id.clone(),
                    outcome: strategy.outcome,
                });
                actions.push(PlannedAction::EvaluateStrategy {
                    strategy_id: strategy.id.clone(),
                    reason: TickReason::StrategyEvaluate,
                });
            }
        }

        // The reduction-only gate is evaluated even while armed; it is the reason the
        // protection pass is never skipped. Entry PlannedActions above are already
        // suppressed by the armed check, so there is no second switch to consult here.
        let reduce_only_exit_allowed =
            entry_gate_allows(self.kill_switch_armed, IntentDirection::ReduceOnlyExit);
        let protection_reason = if self.kill_switch_armed {
            TickReason::ProtectionPassWhileArmed
        } else {
            TickReason::ProtectionPass
        };
        decisions.push(TickDecision::ProtectionPassPlanned {
            reason: protection_reason,
        });

        let mut arm_ids = Vec::new();
        for arm in &self.arms {
            if arm.status == ArmStatus::Armed {
                arm_ids.push(arm.id.clone());
                decisions.push(TickDecision::ArmPlanned {
                    arm_id: arm.id.clone(),
                    reason: TickReason::ArmEvaluate,
                });
            } else {
                decisions.push(TickDecision::ArmSkipped {
                    arm_id: arm.id.clone(),
                    reason: TickReason::ArmSkippedNotArmed,
                });
            }
        }

        let protection_pass = PlannedProtectionPass {
            arm_ids: arm_ids.clone(),
            kill_switch_armed: self.kill_switch_armed,
            reduce_only_exit_allowed,
            reason: protection_reason,
        };
        actions.push(PlannedAction::EvaluateProtectionArms {
            arm_ids,
            kill_switch_armed: self.kill_switch_armed,
            reduce_only_exit_allowed,
            reason: protection_reason,
        });

        TickPlan {
            strategy_pass_skipped: self.kill_switch_armed,
            strategy_evaluations: evaluations,
            protection_pass,
            decisions,
            actions,
        }
    }
}

/// Convenience: the ordered strategy ids that will be evaluated, in result order.
pub fn evaluated_strategy_ids(plan: &TickPlan) -> Vec<&str> {
    plan.strategy_evaluations
        .iter()
        .map(|evaluation| evaluation.strategy_id.as_str())
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn strategy(id: &str, outcome: EvaluationOutcome) -> StrategyInput {
        StrategyInput {
            id: id.to_string(),
            status: StrategyStatus::Active,
            last_run_at: None,
            interval_secs: 3600.0,
            outcome,
        }
    }

    #[test]
    fn the_kernel_decides_due_not_this_crate() {
        let mut input = strategy("s", EvaluationOutcome::Buy);
        input.last_run_at = Some(0.0);
        input.interval_secs = 60.0;
        assert!(input.is_due(60.0)); // inclusive boundary
        assert!(!input.is_due(59.999));
        assert!(!input.is_due(-1.0)); // backwards clock
        input.last_run_at = None;
        assert!(input.is_due(0.0)); // first run ignores now and interval
    }

    #[test]
    fn the_armed_switch_never_vetoes_a_reduce_only_exit() {
        assert!(entry_gate_allows(true, IntentDirection::ReduceOnlyExit));
        assert!(!entry_gate_allows(true, IntentDirection::Entry));
    }
}
