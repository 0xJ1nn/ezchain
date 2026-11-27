//! Entry-only gating: an armed kill switch can refuse a new entry, never an exit.
//!
//! ADR-0034 makes the global kill switch a refusal of every *buy*; ADR-0031
//! makes a reduce-only sell skip the entry gates. A protection arm that fires
//! always produces a reduce-only exit intent, so the kill switch can never veto
//! it. The engine's I/O side effects and the persisted switch itself stay in
//! Python; this module models only the direction of the decision.

use crate::evaluation::{Action, ArmEvaluation};

/// The two decision directions the platform recognises.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum IntentDirection {
    /// New risk: buys and any risk-increasing action.
    Entry,
    /// A protection close: reduce-only, bounded by the held quantity.
    ReduceOnlyExit,
}

impl ArmEvaluation {
    /// A triggered arm is an exit; a hold produces no intent at all.
    pub fn direction(&self) -> Option<IntentDirection> {
        match self.action {
            Action::Trigger => Some(IntentDirection::ReduceOnlyExit),
            Action::Hold => None,
        }
    }
}

/// The global entry gate. `true` means the intent may proceed.
pub fn entry_gate_allows(kill_switch_armed: bool, direction: IntentDirection) -> bool {
    match direction {
        IntentDirection::Entry => !kill_switch_armed,
        IntentDirection::ReduceOnlyExit => true,
    }
}
