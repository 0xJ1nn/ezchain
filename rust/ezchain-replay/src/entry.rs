//! One immutable journal leg, built from canonical text amounts.
//!
//! Mirrors `LedgerEntry.__post_init__`: `amount` is validated as an exact
//! quantity and `usd_value` as an exact whole number of minor units at
//! construction, so no projection function ever sees an unvalidated amount.
//! Attribution fields that do not affect a read-only balance projection
//! (`created_at`, wallet/bucket/actor ids) are intentionally not carried; only
//! `group_id` is kept so a caller can group a tape before asserting balance.

use ezchain_decimal::{require_money, require_quantity, Decimal, ParseError};

use crate::error::ReplayError;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LedgerEntry {
    pub account: String,
    pub asset: String,
    pub amount: Decimal,
    pub usd_value: Decimal,
    pub kind: String,
    pub group_id: Option<String>,
}

impl LedgerEntry {
    /// Construct and validate one leg. The four text amounts are canonical
    /// decimal lexemes, exactly what a caller would persist as `TEXT` columns
    /// (ADR-0011: serialization keeps raw strings, not JSON numbers).
    pub fn new(
        account: impl Into<String>,
        asset: impl Into<String>,
        amount: &str,
        usd_value: &str,
        kind: impl Into<String>,
    ) -> Result<Self, ReplayError> {
        let amount = parse_decimal(amount)?;
        let usd_value = parse_decimal(usd_value)?;
        require_quantity(&amount).map_err(ReplayError::from)?;
        require_money(&usd_value).map_err(ReplayError::from)?;
        Ok(Self {
            account: account.into(),
            asset: asset.into(),
            amount,
            usd_value,
            kind: kind.into(),
            group_id: None,
        })
    }

    /// Stamp the journal group this leg belongs to. Mirrors the optional
    /// `group_id` field of the Python dataclass.
    #[must_use]
    pub fn with_group_id(mut self, group_id: impl Into<String>) -> Self {
        self.group_id = Some(group_id.into());
        self
    }
}

fn parse_decimal(text: &str) -> Result<Decimal, ReplayError> {
    match Decimal::parse(text) {
        Ok(value) => Ok(value),
        Err(ParseError::NonFinite) => Err(ReplayError::NonFinite),
        Err(ParseError::Syntax | ParseError::ExpOutOfRange) => Err(ReplayError::InvalidDecimal),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_whole_minor_unit_usd_value_is_accepted() {
        let entry = LedgerEntry::new("cash:USD", "USD", "1.5", "0.00000001", "buy").unwrap();
        assert_eq!(entry.account, "cash:USD");
        assert_eq!(entry.kind, "buy");
        assert_eq!(entry.group_id, None);
    }

    #[test]
    fn a_sub_minor_usd_value_is_refused_like_python() {
        let err = LedgerEntry::new("cash:USD", "USD", "1", "0.000000009", "buy").unwrap_err();
        assert_eq!(err, ReplayError::SubQuantum);
        assert_eq!(err.code(), "SubQuantumError");
    }
}
