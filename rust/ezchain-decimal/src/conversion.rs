//! One-to-one parity layer for the pure cross-asset conversion helpers in
//! `packages/adapters/src/ezchain_adapters/quotes.py` (`to_raw`, `from_raw`,
//! `quote_price`, `value_impact`).
//!
//! These helpers are pure: they take Decimals and integers, and do no I/O. The
//! network-dependent quote paths in that module (`QuoteRouter._quantity_from_usd`,
//! `QuoteRouter._paper_mark`, `QuoteRouter._has_exit`) ask a provider or a
//! DexScreener mark and are deliberately NOT ported here — they cannot be
//! reproduced from inputs alone. This module only carries the arithmetic that
//! converts between a USD leg, a token amount and raw token-decimal units.
//!
//! Unlike the `money.py` helpers, the adapters' helpers use bare arithmetic
//! under the *ambient* decimal context (`prec = 28`, half-even). Every operation
//! below therefore runs under [`DEFAULT_CONTEXT`], exactly as the Python
//! expressions `amount * (Decimal(10) ** decimals)`,
//! `Decimal(raw) / (Decimal(10) ** decimals)`, `usd / token` and `(out/in) - 1`
//! do. The oracle harness records their results and this module is asserted
//! against every recorded token.

use std::cmp::Ordering;

use crate::biguint::BigUint;
use crate::decimal::{Context, Decimal, DEFAULT_CONTEXT};
use crate::money::MoneyError;

/// The adapters' `MAX_TOKEN_DECIMALS`: a decimals count outside `0..=36` is
/// refused with the adapters' `NotFound("unknown token decimals")`.
const MAX_TOKEN_DECIMALS: i32 = 36;

/// A refusal from a conversion helper, named after the Python exception class
/// the oracle records (`NotFound` is `ezchain_adapters.errors.NotFound`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ConversionError {
    /// `decimals` was outside `0..=36` (`NotFound`).
    UnknownTokenDecimals,
    /// A non-zero numerator divided by zero (`decimal.DivisionByZero`).
    DivisionByZero,
    /// Zero divided by zero (`decimal.InvalidOperation`).
    InvalidOperation,
    /// A result's adjusted exponent exceeded the ambient context `Emax`
    /// (`decimal.Overflow`).
    Overflow,
}

impl ConversionError {
    /// The Python exception class name recorded by the oracle.
    pub fn code(&self) -> &'static str {
        match self {
            Self::UnknownTokenDecimals => "NotFound",
            Self::DivisionByZero => "DivisionByZero",
            Self::InvalidOperation => "InvalidOperation",
            Self::Overflow => "Overflow",
        }
    }
}

impl From<MoneyError> for ConversionError {
    fn from(err: MoneyError) -> Self {
        match err {
            MoneyError::DivisionByZero => Self::DivisionByZero,
            MoneyError::InvalidOperation => Self::InvalidOperation,
            MoneyError::Overflow => Self::Overflow,
            // `mul`/`div` under the fixed, valid DEFAULT_CONTEXT cannot raise
            // any other `MoneyError`; map defensively rather than panic.
            _ => Self::InvalidOperation,
        }
    }
}

/// `Decimal(10) ** decimals` as Python computes it under the ambient context.
///
/// For `decimals <= 27` the exact power has at most 28 digits and survives
/// unchanged. For larger counts the power operation rounds to 28 significant
/// digits (e.g. `Decimal(10) ** 36` is `1.000000000000000000000000000E+36`),
/// which is reproduced by multiplying the exact power by one under
/// [`DEFAULT_CONTEXT`].
fn ten_pow(decimals: i32) -> Result<Decimal, ConversionError> {
    let exact = Decimal {
        sign: false,
        coef: BigUint::pow10(decimals as usize),
        exp: 0,
    };
    Ok(exact.mul(&Decimal::from_i64(1), &DEFAULT_CONTEXT)?)
}

fn check_decimals(decimals: i32) -> Result<(), ConversionError> {
    if (0..=MAX_TOKEN_DECIMALS).contains(&decimals) {
        Ok(())
    } else {
        Err(ConversionError::UnknownTokenDecimals)
    }
}

/// Bare division under the ambient 28-digit context, matching Python's
/// `a / b`. Python raises `InvalidOperation` for `0 / 0` and `DivisionByZero`
/// for a non-zero numerator over zero; the underlying `div` reports the latter
/// for both, so the zero/zero case is distinguished here.
fn divide(
    numerator: &Decimal,
    denominator: &Decimal,
    ctx: &Context,
) -> Result<Decimal, ConversionError> {
    if denominator.is_zero() {
        return Err(if numerator.is_zero() {
            ConversionError::InvalidOperation
        } else {
            ConversionError::DivisionByZero
        });
    }
    Ok(numerator.div(denominator, ctx)?)
}

/// Rust mirror of `quotes.to_raw`: scale a token amount to raw integer units,
/// truncating toward zero. The product is a bare `amount * (Decimal(10) **
/// decimals)` under the ambient context, so a value with more than 28
/// significant digits is first rounded half-even, then `int()` truncates.
pub fn to_raw(amount: &Decimal, decimals: i32) -> Result<crate::MinorUnits, ConversionError> {
    check_decimals(decimals)?;
    let product = amount.mul(&ten_pow(decimals)?, &DEFAULT_CONTEXT)?;
    // `int(Decimal)` truncates toward zero: drop the fractional digits and
    // keep the sign only when a non-zero integer remains.
    let magnitude = if product.exp >= 0 {
        product.coef.mul_pow10(product.exp as usize)
    } else {
        let (whole, _) = product
            .coef
            .divmod_pow10(product.exp.unsigned_abs() as usize);
        whole
    };
    Ok(crate::MinorUnits {
        sign: product.sign && !magnitude.is_zero(),
        magnitude,
    })
}

/// Rust mirror of `quotes.from_raw`: the inverse scaling, `Decimal(raw) /
/// (Decimal(10) ** decimals)`, under the ambient context.
pub fn from_raw(raw: &crate::MinorUnits, decimals: i32) -> Result<Decimal, ConversionError> {
    check_decimals(decimals)?;
    let raw_decimal = Decimal {
        sign: raw.sign,
        coef: raw.magnitude.clone(),
        exp: 0,
    };
    divide(&raw_decimal, &ten_pow(decimals)?, &DEFAULT_CONTEXT)
}

/// Rust mirror of `quotes.quote_price`: USD per token, using the provider's
/// published USD legs when it has them, otherwise the amount in its own unit.
///
/// A buy divides the input-leg USD value by the token amount received; a sell
/// divides the output-leg USD value by the token amount paid. Any `side` other
/// than `"buy"` takes the sell branch, as in the Python helper.
pub fn quote_price(
    side: &str,
    amount_in: &Decimal,
    amount_out: &Decimal,
    amount_in_usd: Option<&Decimal>,
    amount_out_usd: Option<&Decimal>,
) -> Result<Decimal, ConversionError> {
    let (usd, token) = if side == "buy" {
        (amount_in_usd.unwrap_or(amount_in), amount_out)
    } else {
        (amount_out_usd.unwrap_or(amount_out), amount_in)
    };
    divide(usd, token, &DEFAULT_CONTEXT)
}

/// Rust mirror of `quotes.value_impact`: derive a price-impact ratio from a
/// provider's two USD legs, or `None` when either leg is absent or the input
/// leg is not positive (risk then treats impact as unknown — CONTRACT I3).
///
/// The ratio is `(amount_out_usd / amount_in_usd) - 1` under the ambient
/// context; subtraction is addition of the negated integer one, which applies
/// the same context rounding as Python's `Decimal - int`.
pub fn value_impact(
    amount_in_usd: Option<&Decimal>,
    amount_out_usd: Option<&Decimal>,
) -> Result<Option<Decimal>, ConversionError> {
    let (input, output) = match (amount_in_usd, amount_out_usd) {
        (Some(input), Some(output)) => (input, output),
        _ => return Ok(None),
    };
    if input.compare_numeric(&Decimal::from_i64(0)) != Ordering::Greater {
        return Ok(None);
    }
    let ratio = divide(output, input, &DEFAULT_CONTEXT)?;
    let minus_one = Decimal {
        sign: true,
        coef: BigUint::one(),
        exp: 0,
    };
    Ok(Some(ratio.add(&minus_one, &DEFAULT_CONTEXT)?))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> Decimal {
        Decimal::parse(s).unwrap()
    }

    fn token(value: &Decimal) -> String {
        format!("{}~{}", value.canonical(), value.str_repr())
    }

    #[test]
    fn to_raw_truncates_toward_zero_and_rounds_at_28_digits() {
        assert_eq!(to_raw(&d("1.5"), 2).unwrap().canonical(), "N:150");
        assert_eq!(
            to_raw(&d("1.2345678901"), 8).unwrap().canonical(),
            "N:123456789"
        );
        assert_eq!(to_raw(&d("-2.5"), 0).unwrap().canonical(), "N:-2");
        assert_eq!(to_raw(&d("-0.9"), 0).unwrap().canonical(), "N:0");
        assert_eq!(to_raw(&d("0.00000001"), 8).unwrap().canonical(), "N:1");
        // 29 significant digits round to 28 (half-even) before the truncation.
        assert_eq!(
            to_raw(&d("-1.9999999999999999999999999999"), 0)
                .unwrap()
                .canonical(),
            "N:-2"
        );
        assert_eq!(
            to_raw(&d("123456789012345678901234567890.12345"), 6)
                .unwrap()
                .canonical(),
            "N:123456789012345678901234567900000000"
        );
    }

    #[test]
    fn to_raw_refuses_out_of_range_decimals() {
        assert_eq!(
            to_raw(&d("1.5"), -1),
            Err(ConversionError::UnknownTokenDecimals)
        );
        assert_eq!(
            to_raw(&d("1.5"), 37),
            Err(ConversionError::UnknownTokenDecimals)
        );
        assert!(to_raw(&d("1.5"), 36)
            .unwrap()
            .canonical()
            .starts_with("N:1"));
    }

    #[test]
    fn from_raw_scales_back_under_the_ambient_context() {
        let raw = |s: &str| crate::MinorUnits::parse(s).unwrap();
        assert_eq!(
            token(&from_raw(&raw("12345"), 2).unwrap()),
            "0|12345|-2~123.45"
        );
        assert_eq!(token(&from_raw(&raw("1"), 2).unwrap()), "0|1|-2~0.01");
        assert_eq!(token(&from_raw(&raw("100"), 2).unwrap()), "0|1|0~1");
        assert_eq!(token(&from_raw(&raw("-5"), 2).unwrap()), "1|5|-2~-0.05");
        assert_eq!(token(&from_raw(&raw("1"), 36).unwrap()), "0|1|-36~1E-36");
        // Long raw integer is rounded to 28 significant digits by the division.
        assert_eq!(
            token(&from_raw(&raw("123456789012345678901234567890"), 18).unwrap()),
            "0|1234567890123456789012345679|-16~123456789012.3456789012345679"
        );
        assert_eq!(
            from_raw(&raw("1"), -1),
            Err(ConversionError::UnknownTokenDecimals)
        );
        assert_eq!(
            from_raw(&raw("1"), 37),
            Err(ConversionError::UnknownTokenDecimals)
        );
    }

    #[test]
    fn quote_price_inverts_a_quote_and_uses_published_usd_legs() {
        assert_eq!(
            token(&quote_price("buy", &d("100"), &d("0.5"), None, None).unwrap()),
            "0|20|1~2.0E+2"
        );
        assert_eq!(
            token(&quote_price("buy", &d("100"), &d("0.5"), Some(&d("100")), None).unwrap()),
            "0|20|1~2.0E+2"
        );
        assert_eq!(
            token(&quote_price("sell", &d("0.5"), &d("100"), None, None).unwrap()),
            "0|20|1~2.0E+2"
        );
        assert_eq!(
            token(&quote_price("sell", &d("0.5"), &d("100"), None, Some(&d("100"))).unwrap()),
            "0|20|1~2.0E+2"
        );
        // Preferred exponent and a non-terminating quotient.
        assert_eq!(
            token(&quote_price("buy", &d("10.00"), &d("4"), None, None).unwrap()),
            "0|250|-2~2.50"
        );
        assert_eq!(
            token(&quote_price("buy", &d("0E+5"), &d("5"), None, None).unwrap()),
            "0|0|5~0E+5"
        );
        assert_eq!(
            token(&quote_price("buy", &d("100"), &d("3"), None, None).unwrap()),
            "0|3333333333333333333333333333|-26~33.33333333333333333333333333"
        );
    }

    #[test]
    fn quote_price_distinguishes_zero_over_zero_from_nonzero_over_zero() {
        assert_eq!(
            quote_price("buy", &d("1"), &d("0"), None, None),
            Err(ConversionError::DivisionByZero)
        );
        assert_eq!(
            quote_price("buy", &d("0"), &d("0"), None, None),
            Err(ConversionError::InvalidOperation)
        );
        // A sell divides the output leg by the input amount: a zero *output*
        // is a valid zero price, a zero *input* is a division by zero.
        assert_eq!(
            quote_price("sell", &d("1"), &d("0"), None, None)
                .unwrap()
                .str_repr(),
            "0"
        );
        assert_eq!(
            quote_price("sell", &d("0"), &d("1"), None, None),
            Err(ConversionError::DivisionByZero)
        );
    }

    #[test]
    fn value_impact_is_none_when_a_leg_is_missing_or_input_is_not_positive() {
        assert_eq!(value_impact(None, Some(&d("99"))).unwrap(), None);
        assert_eq!(value_impact(Some(&d("100")), None).unwrap(), None);
        assert_eq!(value_impact(Some(&d("0")), Some(&d("99"))).unwrap(), None);
        assert_eq!(value_impact(Some(&d("-1")), Some(&d("99"))).unwrap(), None);
        assert_eq!(value_impact(Some(&d("-0")), Some(&d("99"))).unwrap(), None);
    }

    #[test]
    fn value_impact_derives_the_ratio_minus_one() {
        assert_eq!(
            token(
                &value_impact(Some(&d("100")), Some(&d("99")))
                    .unwrap()
                    .unwrap()
            ),
            "1|1|-2~-0.01"
        );
        assert_eq!(
            token(
                &value_impact(Some(&d("100")), Some(&d("101")))
                    .unwrap()
                    .unwrap()
            ),
            "0|1|-2~0.01"
        );
        assert_eq!(
            token(
                &value_impact(Some(&d("100")), Some(&d("100")))
                    .unwrap()
                    .unwrap()
            ),
            "0|0|0~0"
        );
        assert_eq!(
            token(&value_impact(Some(&d("1")), Some(&d("3"))).unwrap().unwrap()),
            "0|2|0~2"
        );
        assert_eq!(
            token(&value_impact(Some(&d("3")), Some(&d("1"))).unwrap().unwrap()),
            "1|6666666666666666666666666667|-28~-0.6666666666666666666666666667"
        );
    }
}
