//! Typed refusals for a tape that violates a ledger invariant.
//!
//! Each variant carries the Python exception class name in [`ReplayError::code`]
//! so the parity harness can compare refusal types exactly, and the exact
//! message for the one refusal whose text is part of the contract
//! (`LedgerError: ledger group does not balance: N minor units`).

use ezchain_decimal::MinorUnits;

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ReplayError {
    /// A `usd_value` was not a whole number of 1e-8 minor units
    /// (`money.SubQuantumError`).
    SubQuantum,
    /// A canonical amount was not a valid decimal literal
    /// (`decimal.InvalidOperation` at `Decimal(str)` construction).
    InvalidDecimal,
    /// A non-finite spelling reached a money boundary (`money.MoneyError`).
    NonFinite,
    /// The tape's USD legs do not sum to zero minor units (`LedgerError`).
    Unbalanced { total_minor: MinorUnits },
    /// `replay_position` found a negative quantity (`ValueError`).
    NegativeQuantity,
    /// Any other money-range refusal reported by `ezchain-decimal`
    /// (`money.MoneyError` in the oracle).
    Money(ezchain_decimal::MoneyError),
}

impl From<ezchain_decimal::MoneyError> for ReplayError {
    fn from(error: ezchain_decimal::MoneyError) -> Self {
        match error {
            ezchain_decimal::MoneyError::SubQuantum => Self::SubQuantum,
            ezchain_decimal::MoneyError::NonFinite => Self::NonFinite,
            other => Self::Money(other),
        }
    }
}

impl ReplayError {
    /// The Python exception class name recorded by the oracle.
    #[must_use]
    pub fn code(&self) -> &'static str {
        match self {
            Self::SubQuantum => "SubQuantumError",
            Self::InvalidDecimal => "InvalidOperation",
            Self::NonFinite | Self::Money(_) => "MoneyError",
            Self::Unbalanced { .. } => "LedgerError",
            Self::NegativeQuantity => "ValueError",
        }
    }

    /// The exact message the Python exception carries where it is stable.
    #[must_use]
    pub fn message(&self) -> String {
        match self {
            Self::Unbalanced { total_minor } => format!(
                "ledger group does not balance: {} minor units",
                crate::minor::signed_string(total_minor)
            ),
            Self::NegativeQuantity => "position quantity cannot be negative".to_string(),
            other => other.code().to_string(),
        }
    }
}
