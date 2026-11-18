//! Pure protection-arm trigger evaluation, ported from the Python originals.
//!
//! Oracle: `packages/core-py/src/ezchain_core/protection.py` (`validate_arm`,
//! `evaluate_arm`). Side effects (market read, ledger replay, order placement,
//! arm writes and event journaling) stay in
//! `services/engine/src/ezchain_engine/protection.py`.
//!
//! # What is ported
//!
//! - `validate_arm`: every typed rejection code and message.
//! - `evaluate_arm`: every hold/trigger branch, the fired ladder level index,
//!   the trailing above-water peak and the replay `inputs` map.
//! - The entry-only kill-switch gate (ADR-0031/0034): a triggered arm is a
//!   reduce-only exit and is never blocked by an armed kill switch.
//!
//! # The arithmetic boundary
//!
//! Money values cross this crate as validated decimal lexemes. `ezchain-decimal`
//! supplies parsing and numeric comparison only; this crate never adds,
//! subtracts, multiplies or divides money. Python computes percent thresholds
//! and ladder-fraction sums under its own `Decimal` context and passes them as
//! opaque operands (`computed_trigger_price`, `computed_level_triggers`,
//! `fraction_sum`). A missing opaque operand maps to the Python `INVALID_ARM`
//! branch exactly where Python would decline to compute a threshold. This is
//! deliberate: this field ports the decision table only. `ezchain-decimal` has
//! since gained division under its own parity gate, but performing arithmetic
//! here would cross this crate's scope and the ADR-0049/0050 money boundary.

#![forbid(unsafe_code)]

pub mod evaluation;
pub mod gating;
pub mod lexeme;
pub mod validation;

pub use evaluation::{
    evaluate_arm, Action, ArmEvaluation, ArmStatus, EvaluateInput, ProtectionArm, Reason,
};
pub use gating::{entry_gate_allows, IntentDirection};
pub use lexeme::{DecimalLexeme, LexemeError};
pub use validation::{
    validate_arm, ArmKind, ArmParams, LevelInput, LevelsInput, ParamValue, ParamsInput,
    ProtectionValidationError, TakeProfitLevel, ValidateInput, ValidationCode,
};
