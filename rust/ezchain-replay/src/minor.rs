//! Signed, arbitrary-precision ledger minor units.
//!
//! Python's `assert_balanced` and `replay_balances` accumulate `int` minor
//! units (`money.to_minor_units`), which are unbounded. `ezchain-decimal`
//! exposes that integer as [`MinorUnits`] (a sign plus a decimal-digit
//! [`BigUint`]); this module supplies the addition and display the projection
//! needs. No new arithmetic primitive is invented here — magnitude add/sub and
//! compare come from `ezchain-decimal`'s `BigUint`.

use std::cmp::Ordering;

use ezchain_decimal::{BigUint, MinorUnits};

/// The additive identity, `0` minor units.
#[must_use]
pub fn zero() -> MinorUnits {
    MinorUnits {
        sign: false,
        magnitude: BigUint::zero(),
    }
}

/// Exact signed addition of two minor-unit counts. Magnitude operations are
/// delegated to `BigUint`; only sign resolution lives here.
#[must_use]
pub fn add(a: &MinorUnits, b: &MinorUnits) -> MinorUnits {
    if a.sign == b.sign {
        MinorUnits {
            sign: a.sign && !(a.magnitude.is_zero() && b.magnitude.is_zero()),
            magnitude: a.magnitude.add(&b.magnitude),
        }
    } else {
        match a.magnitude.compare(&b.magnitude) {
            Ordering::Greater => MinorUnits {
                sign: a.sign,
                magnitude: a.magnitude.sub(&b.magnitude),
            },
            Ordering::Less => MinorUnits {
                sign: b.sign,
                magnitude: b.magnitude.sub(&a.magnitude),
            },
            Ordering::Equal => zero(),
        }
    }
}

/// The signed decimal integer a Python `int` would print for this count.
/// `MinorUnits::canonical` prefixes with `N:`, which is a harness token, not
/// the value; the ledger error message needs the bare integer.
#[must_use]
pub fn signed_string(value: &MinorUnits) -> String {
    let canonical = value.canonical();
    canonical
        .strip_prefix("N:")
        .unwrap_or(&canonical)
        .to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn units(text: &str) -> MinorUnits {
        MinorUnits::parse(text).expect("valid minor integer")
    }

    #[test]
    fn signed_addition_matches_integer_arithmetic() {
        let cases = [
            ("5", "-3", "2"),
            ("-5", "3", "-2"),
            ("-5", "-3", "-8"),
            ("5", "3", "8"),
            ("12345678901234567890", "-12345678901234567891", "-1"),
            ("-0", "0", "0"),
        ];
        for (a, b, expected) in cases {
            let got = add(&units(a), &units(b));
            assert_eq!(signed_string(&got), expected, "{a} + {b}");
        }
    }
}
