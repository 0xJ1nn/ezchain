//! Validated decimal lexemes: the only money representation this crate carries.
//!
//! A `DecimalLexeme` is a finite decimal literal plus its parsed value from
//! `ezchain-decimal`. This crate never adds, subtracts, multiplies or divides
//! money. It only compares two validated lexemes numerically, the way
//! `Decimal.__le__` / `__ge__` do in `ezchain_core.protection`.

use std::cmp::Ordering;

use ezchain_decimal::{Decimal, ParseError};

/// Why a spelling is not a valid decimal lexeme.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LexemeError {
    /// A non-finite spelling such as `NaN` or `Infinity`.
    NonFinite,
    /// Any other malformed decimal literal.
    Syntax,
}

/// A finite decimal literal, validated once at the boundary.
#[derive(Clone, Debug)]
pub struct DecimalLexeme {
    raw: String,
    value: Decimal,
}

impl DecimalLexeme {
    /// Validate a decimal lexeme. Mirrors the finite-literal subset of
    /// `Decimal(str(raw))` accepted by `ezchain_core.protection._decimal`.
    pub fn parse(raw: impl Into<String>) -> Result<Self, LexemeError> {
        let raw = raw.into();
        let value = Decimal::parse(&raw).map_err(|e| match e {
            ParseError::NonFinite => LexemeError::NonFinite,
            ParseError::Syntax => LexemeError::Syntax,
        })?;
        Ok(Self { raw, value })
    }

    /// The exact lexeme as supplied (used for replay traces).
    pub fn as_str(&self) -> &str {
        &self.raw
    }

    /// Numeric ordering, ignoring representation: `0.1 == 0.10`, `-0 == 0`.
    pub fn cmp_lexeme(&self, other: &Self) -> Ordering {
        self.value.compare_numeric(&other.value)
    }

    /// True when the numeric value is zero.
    pub fn is_zero(&self) -> bool {
        self.value.is_zero()
    }
}

impl PartialEq for DecimalLexeme {
    fn eq(&self, other: &Self) -> bool {
        self.cmp_lexeme(other) == Ordering::Equal
    }
}

impl Eq for DecimalLexeme {}

/// A literal used internally; parsing a valid constant cannot fail.
pub(crate) fn constant(raw: &str) -> DecimalLexeme {
    DecimalLexeme::parse(raw).expect("internal decimal constant is valid")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn comparison_is_numeric_not_representational() {
        assert_eq!(
            constant("0.1").cmp_lexeme(&constant("0.10")),
            Ordering::Equal
        );
        assert_eq!(constant("-0").cmp_lexeme(&constant("0")), Ordering::Equal);
        assert_eq!(
            constant("1E+3").cmp_lexeme(&constant("1000")),
            Ordering::Equal
        );
        assert_eq!(
            constant("-2.5").cmp_lexeme(&constant("0.1")),
            Ordering::Less
        );
        assert_eq!(constant("0.5"), constant("0.50"));
    }

    #[test]
    fn non_finite_is_rejected() {
        for s in ["NaN", "sNaN", "Infinity", "-Infinity"] {
            assert_eq!(DecimalLexeme::parse(s).unwrap_err(), LexemeError::NonFinite);
        }
        assert_eq!(DecimalLexeme::parse("x").unwrap_err(), LexemeError::Syntax);
    }
}
