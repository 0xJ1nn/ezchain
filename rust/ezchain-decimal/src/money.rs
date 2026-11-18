//! One-to-one parity layer for the operations in
//! `packages/core-py/src/ezchain_core/money.py`.
//!
//! Each function derives its context precision the same way `money.py` does and
//! never trusts an ambient 28-digit default. The harness records the Python
//! results as oracle fixtures and asserts these functions match exactly.

use std::cmp::Ordering;

use crate::biguint::BigUint;
use crate::decimal::{Decimal, RoundMode};

/// The money constants from `money.py` that affect precision derivation.
const MONEY_PRECISION: i32 = 60;
const MONEY_SCALE: i32 = 8;
/// Dividing by `BPS` shifts the exponent by four (BPS_DIGITS in `money.py`).
const BPS_DIGITS: i32 = 4;
/// The slippage window used by `preview_cost` (DEFAULT_SLIPPAGE_BPS in money.py).
const DEFAULT_SLIPPAGE_BPS: i64 = 50;
/// The non-executable mark kind; executable quotes pay the larger fee leg.
const PAPER_MARK: &str = "paper_mark";

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MoneyError {
    /// A non-finite value reached a money boundary (`_as_decimal` in Python).
    NonFinite,
    /// `exact_product()` with no factors.
    EmptyProduct,
    /// Division by zero (`Decimal.DivisionByZero` in Python).
    DivisionByZero,
    /// `bps_fee` with a negative `fee_bps` (`ValueError` in Python).
    NegativeFeeBps,
    /// `preview_cost` with a non-positive notional (`ValueError` in Python).
    NonPositiveNotional,
<<<<<<< HEAD
=======
    /// An explicit value is not a whole number of minor units
    /// (`SubQuantumError` in Python).
    SubQuantum,
>>>>>>> origin/rust/migration
}

impl MoneyError {
    /// The Python exception class name recorded by the oracle.
    pub fn code(&self) -> &'static str {
        match self {
            MoneyError::NegativeFeeBps | MoneyError::NonPositiveNotional => "ValueError",
            MoneyError::DivisionByZero => "DivisionByZero",
<<<<<<< HEAD
            _ => "MoneyError",
        }
=======
            MoneyError::SubQuantum => "SubQuantumError",
            _ => "MoneyError",
        }
    }
}

/// An exact signed integer count of ledger minor units (1e-8 USD).
///
/// This is the Rust counterpart of the `int` returned by `money.to_minor_units`
/// and consumed by `money.from_minor_units`. The magnitude is arbitrary
/// precision, so a 38-digit `NUMERIC(38, 8)` notional survives the round trip.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct MinorUnits {
    /// `true` for a negative count; a zero count is always positive.
    pub sign: bool,
    /// The unsigned count of minor units.
    pub magnitude: BigUint,
}

impl MinorUnits {
    /// Parse a signed integer literal from the oracle fixture.
    pub fn parse(input: &str) -> Option<Self> {
        let (sign, digits) = match input.as_bytes().first() {
            Some(b'-') => (true, &input[1..]),
            Some(b'+') => (false, &input[1..]),
            _ => (false, input),
        };
        if digits.is_empty() {
            return None;
        }
        let magnitude = BigUint::from_digits_str(digits)?;
        Some(Self {
            sign: sign && !magnitude.is_zero(),
            magnitude,
        })
    }

    /// Oracle token: `N:<signed decimal digits>` (zero is never `-0`).
    pub fn canonical(&self) -> String {
        let digits = self.magnitude.to_digits_be();
        if self.sign {
            format!("N:-{digits}")
        } else {
            format!("N:{digits}")
        }
>>>>>>> origin/rust/migration
    }
}

/// Parity for `money.exact_precision`: `precision = 60 + extra + Σ(len(digits)
/// + max(exponent, 0))`. Every operand is finite by construction in this type.
pub fn exact_precision(values: &[Decimal], extra: i32) -> i64 {
    let mut needed = MONEY_PRECISION as i64 + extra as i64;
    for value in values {
        needed += value.coef.digit_count() as i64 + value.exp.max(0) as i64;
    }
    needed
}

fn quantize_with(value: &Decimal, mode: RoundMode) -> Result<Decimal, MoneyError> {
    let precision = exact_precision(std::slice::from_ref(value), MONEY_SCALE) as usize;
    Ok(value.quantize(-MONEY_SCALE, mode, precision))
}

/// Parity for `money.quantize_money`: quantize to 1e-8, half-even, under a
/// context sized by `exact_precision(value, extra=MONEY_SCALE)`.
pub fn quantize_money(value: &Decimal) -> Result<Decimal, MoneyError> {
    quantize_with(value, RoundMode::HalfEven)
}

/// Parity for `money.floor_money`: quantize to 1e-8 toward negative infinity,
/// under the same derived context.
pub fn floor_money(value: &Decimal) -> Result<Decimal, MoneyError> {
    quantize_with(value, RoundMode::Floor)
}

/// Parity for `money.exact_sum`: fold the values into `Decimal(0)` with addition
/// under the derived context `integer_digits + scale + len(str(count)) + 2`.
pub fn exact_sum(values: &[Decimal]) -> Result<Decimal, MoneyError> {
    if values.is_empty() {
        return Ok(Decimal {
            sign: false,
            coef: BigUint::zero(),
            exp: 0,
        });
    }

    let mut integer_digits = 1usize;
    let mut min_exponent = i32::MAX;
    for value in values {
        integer_digits = integer_digits.max(value.coef.digit_count() + value.exp.max(0) as usize);
        min_exponent = min_exponent.min(value.exp);
    }
    let scale = if min_exponent < 0 {
        min_exponent.unsigned_abs() as usize
    } else {
        0
    };
    let precision = integer_digits + scale + values.len().to_string().len() + 2;

    let mut total = Decimal {
        sign: false,
        coef: BigUint::zero(),
        exp: 0,
    };
    for value in values {
        total = total.add(value, precision);
    }
    Ok(total)
}

/// Parity for `money.exact_product`: fold the factors into `Decimal(1)` with
/// multiplication under `Σ(len(digits)) + len(factors) + 2`.
pub fn exact_product(values: &[Decimal]) -> Result<Decimal, MoneyError> {
    if values.is_empty() {
        return Err(MoneyError::EmptyProduct);
    }
    let significant: usize = values.iter().map(|value| value.coef.digit_count()).sum();
    let precision = significant + values.len() + 2;

    let mut product = Decimal {
        sign: false,
        coef: BigUint::one(),
        exp: 0,
    };
    for value in values {
        product = product.mul(value, precision);
    }
    Ok(product)
}

/// Parity for `money.bps_fee`: `notional * fee_bps / BPS` under a context sized
/// from both operands (`extra = MONEY_SCALE + BPS_DIGITS`), then quantized to
/// the ledger minor unit half-even.
pub fn bps_fee(notional: &Decimal, fee_bps: i64) -> Result<Decimal, MoneyError> {
    if fee_bps < 0 {
        return Err(MoneyError::NegativeFeeBps);
    }
    let fee_bps_dec = Decimal::from_i64(fee_bps);
    let precision = exact_precision(
        &[notional.clone(), fee_bps_dec.clone()],
        MONEY_SCALE + BPS_DIGITS,
    ) as usize;
    let product = notional.mul(&fee_bps_dec, precision);
    let fee = product.div(&Decimal::from_i64(10_000), precision)?;
    quantize_money(&fee)
}

/// The numeric cost sheet produced by `money.preview_cost`.
///
/// `SourceRef`, `network_fee_usd` and `price_impact_pct` are pure echoes/nulls
/// in `money.py` and carry no decimal arithmetic, so they are not part of this
/// prototype. `to_dict`/JSON canonicalisation remains out of scope (README).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CostPreview {
    pub platform_fee_usd: Decimal,
    pub venue_fee_usd: Decimal,
    pub slippage_bps: Option<i64>,
    pub worst_case_out: Option<Decimal>,
    pub fill_fee_usd: Decimal,
}

/// Parity for `money.preview_cost`: a paper mark pays the flat bps fee, an
/// executable quote pays `max(bps leg, quantized venue leg)`; the receiver's
/// worst case inside the slippage window is quantized half-even.
pub fn preview_cost(
    kind: &str,
    notional: &Decimal,
    venue_fee: &Decimal,
    amount_out: Option<&Decimal>,
    fee_bps: i64,
) -> Result<CostPreview, MoneyError> {
    // money.py validates/quantizes the venue field before the notional check.
    let venue_leg = quantize_money(venue_fee)?;
    if notional.compare_numeric(&Decimal::from_i64(0)) != Ordering::Greater {
        return Err(MoneyError::NonPositiveNotional);
    }
    let paper_leg = bps_fee(notional, fee_bps)?;
    let is_mark = kind == PAPER_MARK;
    let fill_fee_usd = if is_mark {
        paper_leg
    } else if compare(&paper_leg, &venue_leg) == Ordering::Less {
        venue_leg
    } else {
        paper_leg
    };
    let slippage_bps = if is_mark {
        None
    } else {
        Some(DEFAULT_SLIPPAGE_BPS)
    };
    let worst_case_out = match (slippage_bps, amount_out) {
        (Some(slippage), Some(amount)) => {
            let precision =
                exact_precision(std::slice::from_ref(amount), MONEY_SCALE + BPS_DIGITS) as usize;
            let factor = Decimal::from_i64(10_000 - slippage);
            let inside_window = amount
                .mul(&factor, precision)
                .div(&Decimal::from_i64(10_000), precision)?;
            Some(quantize_money(&inside_window)?)
        }
        _ => None,
    };
    Ok(CostPreview {
        platform_fee_usd: Decimal::from_i64(0),
        venue_fee_usd: venue_fee.clone(),
        slippage_bps,
        worst_case_out,
        fill_fee_usd,
    })
}

<<<<<<< HEAD
=======
/// Parity for `money.require_quantity`: an exact, finite token quantity.
///
/// `money.py` only calls `_as_decimal` here. In this prototype every `Decimal`
/// is finite by construction (`Decimal::parse` rejects `NaN`/`sNaN`/`±Infinity`),
/// so the finite guard is the parse step; the parity corpus feeds non-finite
/// spellings through it and asserts the same `MoneyError` as the oracle.
pub fn require_quantity(value: &Decimal) -> Result<Decimal, MoneyError> {
    Ok(value.clone())
}

/// Parity for `money.require_money`: an explicit value that is already a whole
/// number of ledger minor units. Quantization is only used as a test; the
/// accepted value is returned with its original representation unchanged.
pub fn require_money(value: &Decimal) -> Result<Decimal, MoneyError> {
    if quantize_money(value)?.compare_numeric(value) != Ordering::Equal {
        return Err(MoneyError::SubQuantum);
    }
    Ok(value.clone())
}

/// Parity for `money.to_minor_units`: the exact integer count of ledger minor
/// units. Scaling by 1e8 is a pure exponent shift; because `require_money` has
/// already proved the value is a whole minor unit, no fractional digit can be
/// dropped. The `from_minor_units` round trip is reproduced as the final check.
pub fn to_minor_units(value: &Decimal) -> Result<MinorUnits, MoneyError> {
    require_money(value)?;

    // `MONEY_SCALE` is added in i64 space so no i32 exponent can overflow here.
    let shift = value.exp as i64 + MONEY_SCALE as i64;
    let magnitude = if shift >= 0 {
        value.coef.mul_pow10(shift as usize)
    } else {
        let (quotient, remainder) = value.coef.divmod_pow10(shift.unsigned_abs() as usize);
        if !remainder.is_zero() {
            return Err(MoneyError::SubQuantum);
        }
        quotient
    };
    let minor = MinorUnits {
        sign: value.sign && !magnitude.is_zero(),
        magnitude,
    };

    if from_minor_units(&minor).compare_numeric(value) != Ordering::Equal {
        return Err(MoneyError::SubQuantum);
    }
    Ok(minor)
}

/// Parity for `money.from_minor_units`: the inverse conversion, used for
/// reporting and for the round-trip check in `to_minor_units`. `Decimal(int)`
/// scaled by 1e-8 is an exponent shift only, so no context rounding occurs.
pub fn from_minor_units(minor: &MinorUnits) -> Decimal {
    Decimal {
        sign: minor.sign && !minor.magnitude.is_zero(),
        coef: minor.magnitude.clone(),
        exp: -MONEY_SCALE,
    }
}

/// Parity for `money.exact_neg`: flip the sign on the digits as they are,
/// without touching any ambient context. `-value` in Python is a context
/// rounding operation; `copy_negate` is not, and neither is this.
///
/// The Python form guards that the argument is a `Decimal`; here the parameter
/// type is the guard. The sign of a zero flips too (`0 → -0`, `-0 → 0`).
pub fn exact_neg(value: &Decimal) -> Decimal {
    Decimal {
        sign: !value.sign,
        coef: value.coef.clone(),
        exp: value.exp,
    }
}

>>>>>>> origin/rust/migration
/// Parity for the `Decimal` comparison operators `money.py` relies on.
pub fn compare(a: &Decimal, b: &Decimal) -> Ordering {
    a.compare_numeric(b)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> Decimal {
        Decimal::parse(s).unwrap()
    }

    #[test]
    fn derived_precision_matches_money_py() {
        assert_eq!(exact_precision(&[d("0.1")], 0), 61);
        assert_eq!(exact_precision(&[d("0.1")], 8), 69);
        assert_eq!(
            exact_precision(&[d("12345678901234567890123456789012345678")], 8),
            106
        );
    }

    #[test]
    fn zero_point_one_plus_zero_point_two() {
        let sum = exact_sum(&[d("0.1"), d("0.2")]).unwrap();
        assert_eq!(sum.canonical(), "0|3|-1");
        assert_eq!(sum.str_repr(), "0.3");
    }

    #[test]
    fn half_even_and_floor_ties() {
        assert_eq!(quantize_money(&d("5e-9")).unwrap().str_repr(), "0E-8");
        assert_eq!(quantize_money(&d("15e-9")).unwrap().str_repr(), "2E-8");
        assert_eq!(quantize_money(&d("25e-9")).unwrap().str_repr(), "2E-8");
        assert_eq!(
            floor_money(&d("-1.234567895")).unwrap().str_repr(),
            "-1.23456790"
        );
    }

    #[test]
    fn empty_product_is_refused() {
        assert_eq!(exact_product(&[]), Err(MoneyError::EmptyProduct));
    }

    fn token(value: &Decimal) -> String {
        format!("{}~{}", value.canonical(), value.str_repr())
    }

    #[test]
    fn bps_fee_matches_python() {
        for (notional, fee_bps, expected) in [
            ("100", 50, "0|50000000|-8~0.50000000"),
            ("0.1", 25, "0|25000|-8~0.00025000"),
            ("-12.345678901", 50, "1|6172839|-8~-0.06172839"),
            ("100", 0, "0|0|-8~0E-8"),
            ("1", 3, "0|30000|-8~0.00030000"),
        ] {
            assert_eq!(token(&bps_fee(&d(notional), fee_bps).unwrap()), expected);
        }
        assert_eq!(bps_fee(&d("100"), -1), Err(MoneyError::NegativeFeeBps));
    }

    #[test]
    fn preview_cost_matches_python() {
        let mark = preview_cost("paper_mark", &d("100"), &d("999"), None, 50).unwrap();
        assert_eq!(token(&mark.fill_fee_usd), "0|50000000|-8~0.50000000");
        assert_eq!(mark.slippage_bps, None);
        assert_eq!(mark.worst_case_out, None);

        let executable = preview_cost("jupiter", &d("100"), &d("2.5"), Some(&d("10")), 50).unwrap();
        assert_eq!(token(&executable.fill_fee_usd), "0|250000000|-8~2.50000000");
        assert_eq!(executable.slippage_bps, Some(50));
        assert_eq!(
            token(&executable.worst_case_out.unwrap()),
            "0|995000000|-8~9.95000000"
        );

        assert_eq!(
            preview_cost("jupiter", &d("0"), &d("1"), None, 50),
            Err(MoneyError::NonPositiveNotional)
        );
    }
<<<<<<< HEAD
=======

    #[test]
    fn require_money_preserves_representation_and_refuses_sub_quantum() {
        assert_eq!(
            token(&require_money(&d("1.500")).unwrap()),
            "0|1500|-3~1.500"
        );
        assert_eq!(
            token(&require_money(&d("-0.00000000")).unwrap()),
            "1|0|-8~-0E-8"
        );
        assert_eq!(token(&require_money(&d("0.1")).unwrap()), "0|1|-1~0.1");
        assert_eq!(
            require_money(&d("0.000000009")),
            Err(MoneyError::SubQuantum)
        );
        assert_eq!(
            require_money(&d("1.000000001")),
            Err(MoneyError::SubQuantum)
        );
    }

    #[test]
    fn require_quantity_passes_through_without_scale_limit() {
        assert_eq!(
            token(&require_quantity(&d("1.2345678901")).unwrap()),
            "0|12345678901|-10~1.2345678901"
        );
        assert_eq!(token(&require_quantity(&d("-0")).unwrap()), "1|0|0~-0");
    }

    #[test]
    fn minor_unit_round_trip() {
        for (value, minor) in [
            ("1.5", "150000000"),
            ("-2.5", "-250000000"),
            ("1E+3", "100000000000"),
            ("0.000000010", "1"),
            ("-0", "0"),
            (
                "12345678901234567890.12345678",
                "1234567890123456789012345678",
            ),
        ] {
            let original = d(value);
            let units = to_minor_units(&original).unwrap();
            assert_eq!(units.canonical(), format!("N:{minor}"), "{value}");
            assert_eq!(
                from_minor_units(&units).compare_numeric(&original),
                Ordering::Equal,
                "{value} round trip"
            );
        }
        // 0.1 is 10,000,000 whole minor units; 9e-9 is below the quantum.
        assert_eq!(to_minor_units(&d("0.1")).unwrap().canonical(), "N:10000000");
        assert_eq!(
            to_minor_units(&d("0.000000009")),
            Err(MoneyError::SubQuantum)
        );
    }

    #[test]
    fn exact_neg_flips_sign_without_rounding() {
        assert_eq!(token(&exact_neg(&d("1.5"))), "1|15|-1~-1.5");
        assert_eq!(token(&exact_neg(&d("0"))), "1|0|0~-0");
        assert_eq!(token(&exact_neg(&d("-0E-8"))), "0|0|-8~0E-8");
        // A 29-digit quantity must not lose its last digit.
        let long = d("12345678901234567890123456789");
        assert_eq!(
            token(&exact_neg(&long)),
            "1|12345678901234567890123456789|0~-12345678901234567890123456789"
        );
    }
>>>>>>> origin/rust/migration
}
