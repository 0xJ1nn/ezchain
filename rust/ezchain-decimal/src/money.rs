//! One-to-one parity layer for the operations in
//! `packages/core-py/src/ezchain_core/money.py`.
//!
//! Each function derives its context precision the same way `money.py` does and
//! never trusts an ambient 28-digit default. The harness records the Python
//! results as oracle fixtures and asserts these functions match exactly.

use std::cmp::Ordering;

use crate::biguint::BigUint;
use crate::decimal::{Context, Decimal, RoundMode, DEFAULT_CONTEXT, MAX_PREC};

/// The money constants from `money.py` that affect precision derivation.
const MONEY_PRECISION: i64 = 60;
const MONEY_SCALE: i64 = 8;
/// Dividing by `BPS` shifts the exponent by four (BPS_DIGITS in `money.py`).
const BPS_DIGITS: i64 = 4;
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
    /// An explicit value is not a whole number of minor units
    /// (`SubQuantumError` in Python).
    SubQuantum,
    /// A `bool` reached `_as_decimal` (`MoneyError: ... not bool`).
    BoolInput,
    /// A `float` reached `_as_decimal` (`FloatMoneyError` in Python).
    FloatInput,
    /// A value of any other non-Decimal, non-int type reached a boundary.
    WrongType,
    /// A result's adjusted exponent exceeded the ambient context `Emax`
    /// (`decimal.Overflow` in Python).
    Overflow,
    /// Quantize needed more than `prec` digits or exceeded `Emax`
    /// (`decimal.InvalidOperation` in Python).
    InvalidOperation,
    /// A decimal `InvalidOperation`/`Overflow` that a money helper caught and
    /// re-raised as its own `MoneyError("... outside the supported money range")`.
    OutsideMoneyRange,
    /// A derived precision was outside `[1, MAX_PREC]`, so assigning
    /// `context.prec` in Python raises `ValueError` before the operation runs.
    PrecisionOutOfRange,
}

impl MoneyError {
    /// The Python exception class name recorded by the oracle.
    pub fn code(&self) -> &'static str {
        match self {
            MoneyError::NegativeFeeBps
            | MoneyError::NonPositiveNotional
            | MoneyError::PrecisionOutOfRange => "ValueError",
            MoneyError::DivisionByZero => "DivisionByZero",
            MoneyError::SubQuantum => "SubQuantumError",
            MoneyError::FloatInput => "FloatMoneyError",
            MoneyError::Overflow => "Overflow",
            MoneyError::InvalidOperation => "InvalidOperation",
            _ => "MoneyError",
        }
    }
}

/// A value at a money boundary, preserving the Python type the caller used.
///
/// `money.py`'s `_as_decimal` accepts only `Decimal` and `int`; `bool`, `float`
/// and every other type are refused with distinct exception classes. Rust's
/// type system cannot express "a bool passed where a Decimal was expected", so
/// this enum makes that boundary explicit for the parity harness. The payload
/// of `Int` is a `Decimal` whose exponent is always zero (`Decimal(int)`).
#[derive(Clone, Debug, PartialEq)]
pub enum MoneyInput {
    Decimal(Decimal),
    Int(Decimal),
    Float(f64),
    Bool(bool),
    Str(String),
    None,
    /// A Python `Decimal('NaN' | 'sNaN' | '±Infinity')`: constructed fine, but
    /// refused by `_as_decimal`'s finite guard.
    NonFinite,
}

impl MoneyInput {
    /// Parse a harness boundary-input token: `<tag>:<payload>`.
    pub fn parse(token: &str) -> Option<Self> {
        let (tag, payload) = token.split_once(':')?;
        match tag {
            "D" => Decimal::parse(payload).ok().map(Self::Decimal),
            "I" => Decimal::parse(payload).ok().map(Self::Int),
            "F" => payload.parse::<f64>().ok().map(Self::Float),
            "B" => match payload {
                "True" => Some(Self::Bool(true)),
                "False" => Some(Self::Bool(false)),
                _ => None,
            },
            "S" => Some(Self::Str(payload.to_string())),
            "N" => Some(Self::None),
            "X" => Some(Self::NonFinite),
            _ => None,
        }
    }
}

/// Rust mirror of `money._as_decimal`: accept `Decimal` and `int`, refuse
/// `bool`, `float` and every other type with the same exception class.
pub fn as_decimal(input: &MoneyInput) -> Result<Decimal, MoneyError> {
    match input {
        MoneyInput::Decimal(value) | MoneyInput::Int(value) => Ok(value.clone()),
        MoneyInput::Float(_) => Err(MoneyError::FloatInput),
        MoneyInput::Bool(_) => Err(MoneyError::BoolInput),
        MoneyInput::Str(_) | MoneyInput::None => Err(MoneyError::WrongType),
        MoneyInput::NonFinite => Err(MoneyError::NonFinite),
    }
}

/// Rust mirror of `money.from_minor_units`' type guard: only an `int` is
/// accepted. Notably a `float` here is a plain `MoneyError`, **not** the
/// `FloatMoneyError` that `_as_decimal` raises.
pub fn from_minor_units_input(input: &MoneyInput) -> Result<MinorUnits, MoneyError> {
    match input {
        MoneyInput::Int(value) => Ok(MinorUnits {
            sign: value.sign && !value.coef.is_zero(),
            magnitude: value.coef.clone(),
        }),
        MoneyInput::Bool(_) | MoneyInput::Float(_) | MoneyInput::Str(_) | MoneyInput::None => {
            Err(MoneyError::WrongType)
        }
        MoneyInput::Decimal(_) => Err(MoneyError::WrongType),
        MoneyInput::NonFinite => Err(MoneyError::NonFinite),
    }
}

/// Build the ambient decimal context for a money operation. Assigning a
/// precision outside CPython's `[1, MAX_PREC]` range raises `ValueError` in
/// Python before the operation starts; refuse here with the same code.
fn checked_context(precision: i128) -> Result<Context, MoneyError> {
    if precision < 1 || precision > MAX_PREC as i128 {
        return Err(MoneyError::PrecisionOutOfRange);
    }
    Ok(Context::with_precision(precision as i64))
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
    }
}

/// Parity for `money.exact_precision`: `precision = 60 + extra + Σ(len(digits)
/// + max(exponent, 0))`. Every operand is finite by construction in this type.
pub fn exact_precision(values: &[Decimal], extra: i64) -> i64 {
    let mut needed = MONEY_PRECISION + extra;
    for value in values {
        needed += value.coef.digit_count() as i64 + value.exp.max(0);
    }
    needed
}

fn quantize_with(value: &Decimal, mode: RoundMode) -> Result<Decimal, MoneyError> {
    let precision = exact_precision(std::slice::from_ref(value), MONEY_SCALE);
    let ctx = checked_context(precision.into())?;
    // money.py wraps quantize in `except InvalidOperation -> MoneyError`.
    value
        .quantize(-MONEY_SCALE, mode, &ctx)
        .map_err(|e| match e {
            MoneyError::InvalidOperation => MoneyError::OutsideMoneyRange,
            other => other,
        })
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

    let mut integer_digits = 1i128;
    let mut min_exponent = i64::MAX;
    for value in values {
        integer_digits =
            integer_digits.max(value.coef.digit_count() as i128 + value.exp.max(0) as i128);
        min_exponent = min_exponent.min(value.exp);
    }
    let scale = if min_exponent < 0 {
        min_exponent.unsigned_abs() as i128
    } else {
        0
    };
    let precision = integer_digits + scale + values.len().to_string().len() as i128 + 2;
    let ctx = checked_context(precision)?;

    let mut total = Decimal {
        sign: false,
        coef: BigUint::zero(),
        exp: 0,
    };
    for value in values {
        total = total.add(value, &ctx)?;
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
    let precision = significant as i64 + values.len() as i64 + 2;
    let ctx = checked_context(precision.into())?;

    let mut product = Decimal {
        sign: false,
        coef: BigUint::one(),
        exp: 0,
    };
    for value in values {
        product = product.mul(value, &ctx)?;
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
    );
    let ctx = checked_context(precision.into())?;
    let product = notional.mul(&fee_bps_dec, &ctx)?;
    let fee = product.div(&Decimal::from_i64(10_000), &ctx)?;
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
            let precision = exact_precision(std::slice::from_ref(amount), MONEY_SCALE + BPS_DIGITS);
            let ctx = checked_context(precision.into())?;
            let factor = Decimal::from_i64(10_000 - slippage);
            let inside_window = amount
                .mul(&factor, &ctx)?
                .div(&Decimal::from_i64(10_000), &ctx)?;
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

    // `amount.scaleb(MONEY_SCALE)` is context-checked in Python: an adjusted
    // exponent above Emax raises Overflow, which to_minor_units catches and
    // re-raises as MoneyError. Check it before any coefficient is grown.
    if value.adjusted() + MONEY_SCALE as i128 > DEFAULT_CONTEXT.emax as i128 {
        return Err(MoneyError::OutsideMoneyRange);
    }
    // The check above bounds `shift` to roughly Emax in magnitude, so the
    // i128 -> usize conversion below cannot overflow or allocate wildly.
    let shift = value.exp as i128 + MONEY_SCALE as i128;
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

/// Parity for the `Decimal` comparison operators `money.py` relies on.
pub fn compare(a: &Decimal, b: &Decimal) -> Ordering {
    a.compare_numeric(b)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::decimal::ParseError;

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

    #[test]
    fn as_decimal_guards_match_python_input_types() {
        let accepted = |token: &str| as_decimal(&MoneyInput::parse(token).unwrap()).unwrap();
        assert_eq!(token(&accepted("D:1.5")), "0|15|-1~1.5");
        assert_eq!(token(&accepted("I:42")), "0|42|0~42");
        assert_eq!(
            token(&accepted("I:123456789012345678901234567890")),
            "0|123456789012345678901234567890|0~123456789012345678901234567890"
        );
        let refused = |token: &str| {
            as_decimal(&MoneyInput::parse(token).unwrap())
                .unwrap_err()
                .code()
        };
        assert_eq!(refused("B:True"), "MoneyError");
        assert_eq!(refused("B:False"), "MoneyError");
        assert_eq!(refused("F:0.1"), "FloatMoneyError");
        assert_eq!(refused("S:1.5"), "MoneyError");
        assert_eq!(refused("N:"), "MoneyError");
        assert_eq!(refused("X:NaN"), "MoneyError");
    }

    #[test]
    fn from_minor_units_guard_uses_plain_money_error_for_float() {
        let accepted = from_minor_units_input(&MoneyInput::parse("I:-250000000").unwrap()).unwrap();
        assert_eq!(accepted.canonical(), "N:-250000000");
        // _as_decimal raises FloatMoneyError, but from_minor_units raises a
        // plain MoneyError for a float — the two guards must stay distinct.
        let refused = |token: &str| {
            from_minor_units_input(&MoneyInput::parse(token).unwrap())
                .unwrap_err()
                .code()
        };
        assert_eq!(refused("F:1.0"), "MoneyError");
        assert_eq!(refused("B:True"), "MoneyError");
        assert_eq!(refused("D:1"), "MoneyError");
    }

    #[test]
    fn ambient_context_boundaries_match_python() {
        // money.py catches InvalidOperation/Overflow in quantize and
        // to_minor_units and re-raises its own MoneyError.
        assert_eq!(
            quantize_money(&d("1E+1000000")),
            Err(MoneyError::OutsideMoneyRange)
        );
        assert_eq!(
            quantize_money(&d("1E+9999999999")),
            Err(MoneyError::OutsideMoneyRange)
        );
        assert_eq!(
            to_minor_units(&d("1E+999999")),
            Err(MoneyError::OutsideMoneyRange)
        );
        // exact_product does not catch Overflow; Python raises it directly.
        assert_eq!(
            exact_product(&[d("1E+999999"), d("1E+999999")]),
            Err(MoneyError::Overflow)
        );
        // A precision beyond MAX_PREC makes `context.prec = n` raise ValueError.
        assert_eq!(
            quantize_money(&d("1E+999999999999999999")),
            Err(MoneyError::PrecisionOutOfRange)
        );
        // Tiny values quantize to zero, and exponents beyond i32 parse.
        assert_eq!(quantize_money(&d("1E-1000000")).unwrap().str_repr(), "0E-8");
        assert_eq!(d("1E+9999999999").exp, 9_999_999_999);
        assert_eq!(
            Decimal::parse("1E+9999999999999999999"),
            Err(ParseError::ExpOutOfRange)
        );
    }
}
