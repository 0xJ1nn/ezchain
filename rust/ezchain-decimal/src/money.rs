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

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MoneyError {
    /// A non-finite value reached a money boundary (`_as_decimal` in Python).
    NonFinite,
    /// `exact_product()` with no factors.
    EmptyProduct,
}

impl MoneyError {
    /// The Python exception class name recorded by the oracle.
    pub fn code(&self) -> &'static str {
        "MoneyError"
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
}
