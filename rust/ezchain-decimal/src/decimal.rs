//! A sign + arbitrary-precision decimal coefficient + exponent value, with the
//! operations `money.py` actually uses.
//!
//! This is not a general decimal implementation. It deliberately implements only
//! construction from string and integer, numeric comparison, context-rounded
//! addition, multiplication and division, context-precision derivation, and
//! quantisation with half-even / floor. Currency conversion and canonical JSON
//! are out of scope (see the crate README).
//!
//! Division produces the correctly rounded (half-even) quotient at a caller-
//! supplied precision, including the preferred-exponent and trailing-zero
//! behaviour of Python's `Decimal.__truediv__`.

use std::cmp::Ordering;

use crate::biguint::BigUint;
use crate::money::MoneyError;

/// Rounding modes used by `money.py`.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum RoundMode {
    /// Python's `ROUND_HALF_EVEN`: nearest, ties to an even last digit.
    HalfEven,
    /// Python's `ROUND_FLOOR`: toward negative infinity.
    Floor,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ParseError {
    /// A non-finite spelling (`NaN`, `sNaN`, `Infinity`, ...).
    NonFinite,
    /// A string that is not a valid decimal literal.
    Syntax,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Decimal {
    /// `true` means negative; the sign of a zero is preserved, as in Python.
    pub sign: bool,
    /// Unsigned coefficient digits (canonical, arbitrary precision).
    pub coef: BigUint,
    /// Base-10 exponent.
    pub exp: i32,
}

impl Decimal {
    /// Parse a finite decimal literal. Mirrors the subset of `Decimal(str)`
    /// that `money.py` accepts; non-finite spellings are reported separately
    /// so the money layer can raise the same `MoneyError` as `_as_decimal`.
    pub fn parse(input: &str) -> Result<Self, ParseError> {
        let s = input.trim();
        if s.is_empty() {
            return Err(ParseError::Syntax);
        }

        let (sign, rest) = match s.as_bytes()[0] {
            b'-' => (true, &s[1..]),
            b'+' => (false, &s[1..]),
            _ => (false, s),
        };
        if rest.is_empty() {
            return Err(ParseError::Syntax);
        }

        if matches!(
            rest.to_ascii_lowercase().as_str(),
            "nan" | "snan" | "inf" | "infinity"
        ) {
            return Err(ParseError::NonFinite);
        }

        let (mantissa, exponent) = match rest.find(['e', 'E']) {
            Some(index) => (&rest[..index], &rest[index + 1..]),
            None => (rest, ""),
        };
        if mantissa.is_empty() {
            return Err(ParseError::Syntax);
        }

        let exp_override = if exponent.is_empty() {
            0i32
        } else {
            let exponent = exponent.strip_prefix('+').unwrap_or(exponent);
            exponent.parse::<i32>().map_err(|_| ParseError::Syntax)?
        };

        let mut digits = String::new();
        let mut fraction_len = 0usize;
        let mut seen_point = false;
        let mut seen_digit = false;
        for byte in mantissa.bytes() {
            match byte {
                b'0'..=b'9' => {
                    digits.push(byte as char);
                    if seen_point {
                        fraction_len += 1;
                    }
                    seen_digit = true;
                }
                b'.' if !seen_point => seen_point = true,
                _ => return Err(ParseError::Syntax),
            }
        }
        if !seen_digit {
            return Err(ParseError::Syntax);
        }

        let coef = BigUint::from_digits_str(&digits).ok_or(ParseError::Syntax)?;
        let exp = exp_override
            .checked_sub(fraction_len as i32)
            .ok_or(ParseError::Syntax)?;
        Ok(Self { sign, coef, exp })
    }

    /// Build from an integer, as `Decimal(int)` does in `money.py` (e.g. the
    /// `fee_bps` multiplier and the `BPS` divisor).
    pub fn from_i64(value: i64) -> Self {
        Self {
            sign: value < 0,
            coef: BigUint::from_digits_str(&value.unsigned_abs().to_string())
                .expect("integer digits are a valid BigUint"),
            exp: 0,
        }
    }

    /// True when the numeric value is zero (sign and exponent ignored).
    pub fn is_zero(&self) -> bool {
        self.coef.is_zero()
    }

    /// Numeric comparison, ignoring representation (so `0.1 == 0.10` and
    /// `-0 == 0`). This is the comparison `money.py` operators perform.
    pub fn compare_numeric(&self, other: &Self) -> Ordering {
        let self_zero = self.is_zero();
        let other_zero = other.is_zero();
        if self_zero && other_zero {
            return Ordering::Equal;
        }
        if self_zero {
            return if other.sign {
                Ordering::Greater
            } else {
                Ordering::Less
            };
        }
        if other_zero {
            return if self.sign {
                Ordering::Less
            } else {
                Ordering::Greater
            };
        }
        if self.sign != other.sign {
            return if self.sign {
                Ordering::Less
            } else {
                Ordering::Greater
            };
        }

        let base = self.exp.min(other.exp);
        let a = self.coef.mul_pow10((self.exp - base) as usize);
        let b = other.coef.mul_pow10((other.exp - base) as usize);
        let magnitude = a.compare(&b);
        if self.sign {
            magnitude.reverse()
        } else {
            magnitude
        }
    }

    /// Context-rounded addition with the given precision (half-even, the
    /// default context rounding in `money.py`). The result exponent is the
    /// smaller operand exponent, matching Python's addition.
    pub fn add(&self, other: &Self, precision: usize) -> Self {
        let base = self.exp.min(other.exp);
        let a = self.coef.mul_pow10((self.exp - base) as usize);
        let b = other.coef.mul_pow10((other.exp - base) as usize);
        let (coef, sign) = if self.sign == other.sign {
            (a.add(&b), self.sign)
        } else {
            match a.compare(&b) {
                Ordering::Greater => (a.sub(&b), self.sign),
                Ordering::Less => (b.sub(&a), other.sign),
                Ordering::Equal => (BigUint::zero(), false),
            }
        };
        let mut result = Self {
            sign,
            coef,
            exp: base,
        };
        result.round_to_precision(precision);
        result
    }

    /// Context-rounded multiplication with the given precision (half-even).
    pub fn mul(&self, other: &Self, precision: usize) -> Self {
        let coef = self.coef.mul(&other.coef);
        let exp = self.exp + other.exp;
        let mut result = Self {
            sign: self.sign ^ other.sign,
            coef,
            exp,
        };
        result.round_to_precision(precision);
        result
    }

    /// Context-rounded division with the given precision (half-even), matching
    /// Python's `Decimal.__truediv__` under `localcontext().prec = precision`.
    ///
    /// The exact quotient of the coefficients is expanded digit by digit until
    /// it terminates or has `precision + 1` significant digits, then rounded
    /// half-even to `precision` digits. The result exponent is the preferred
    /// exponent (`self.exp - other.exp`) minus the fractional digits generated,
    /// plus any digits dropped during rounding. This reproduces Python's
    /// preferred-exponent and trailing-zero behaviour (e.g. `1.00 / 2` stays
    /// `50E-2`, while `1 / 3` becomes 28 significant digits at `exp = -28`).
    pub fn div(&self, other: &Self, precision: usize) -> Result<Self, MoneyError> {
        if other.is_zero() {
            return Err(MoneyError::DivisionByZero);
        }
        let sign = self.sign ^ other.sign;
        let preferred_exp = self.exp - other.exp;
        if self.is_zero() {
            // Python keeps the preferred exponent for a zero quotient.
            return Ok(Self {
                sign,
                coef: BigUint::zero(),
                exp: preferred_exp,
            });
        }

        // Integer part of coef_self / coef_other, plus the first remainder.
        let (mut coefficient, mut remainder) = self.coef.div_mod(&other.coef);
        let mut fractional_digits = 0usize;

        // Generate fractional digits until the quotient terminates or we have
        // one digit more than the context precision (a rounding guard).
        while !remainder.is_zero() && coefficient.digit_count() < precision + 1 {
            let shifted = remainder.mul_pow10(1);
            let (digit, next) = shifted.div_mod(&other.coef);
            remainder = next;
            let digit = digit.units_digit();
            coefficient = coefficient.mul_pow10(1).add(&BigUint::from_digit(digit));
            fractional_digits += 1;
        }

        let mut exp = preferred_exp - fractional_digits as i32;
        let significant = coefficient.digit_count();
        if significant > precision {
            // Drop the excess low-order digits, rounding the retained prefix
            // half-even using the first dropped digit and a sticky remainder.
            let drop = significant - precision;
            let (mut kept, dropped) = coefficient.divmod_pow10(drop);
            let guard = if drop > 0 {
                let (head, _) = dropped.divmod_pow10(drop - 1);
                head.units_digit()
            } else {
                0
            };
            let rest_nonzero = if drop > 1 {
                let (_, rest) = dropped.divmod_pow10(drop - 1);
                !rest.is_zero()
            } else {
                false
            };
            let sticky = rest_nonzero || !remainder.is_zero();
            if guard > 5 || (guard == 5 && (sticky || kept.units_digit() % 2 == 1)) {
                kept = kept.add_one();
                if kept.digit_count() > precision {
                    let (trimmed, _) = kept.divmod_pow10(1);
                    kept = trimmed;
                    exp += 1;
                }
            }
            coefficient = kept;
            exp += drop as i32;
        }

        Ok(Self {
            sign,
            coef: coefficient,
            exp,
        })
    }

    /// The quantize operation used by `quantize_money` / `floor_money`: round
    /// the value to `target_exp` under `mode` and return a value with exactly
    /// that exponent. `precision` is the locally derived context size; it is
    /// always large enough in this prototype's scope, so the result cannot
    /// raise Python's `InvalidOperation` here.
    pub fn quantize(&self, target_exp: i32, mode: RoundMode, _precision: usize) -> Self {
        let coef = if self.exp >= target_exp {
            self.coef.mul_pow10((self.exp - target_exp) as usize)
        } else {
            let k = (target_exp - self.exp) as usize;
            let (quotient, remainder) = self.coef.divmod_pow10(k);
            let mut quotient = quotient;
            match mode {
                RoundMode::HalfEven => {
                    let twice_remainder = remainder.add(&remainder);
                    let half = BigUint::pow10(k);
                    let ordering = twice_remainder.compare(&half);
                    if ordering == Ordering::Greater
                        || (ordering == Ordering::Equal && quotient.units_digit() % 2 == 1)
                    {
                        quotient = quotient.add_one();
                    }
                }
                RoundMode::Floor => {
                    // Toward negative infinity: a negative non-exact value's
                    // magnitude grows; a positive one truncates.
                    if self.sign && !remainder.is_zero() {
                        quotient = quotient.add_one();
                    }
                }
            }
            quotient
        };
        Self {
            sign: self.sign,
            coef,
            exp: target_exp,
        }
    }

    /// Canonical oracle encoding of the exact decimal triple:
    /// `sign|coefficient_digits|exponent`.
    pub fn canonical(&self) -> String {
        format!(
            "{}|{}|{}",
            self.sign as u8,
            self.coef.to_digits_be(),
            self.exp
        )
    }

    /// Python's `str(Decimal)` formatting, reproduced from the value's triple.
    pub fn str_repr(&self) -> String {
        let digits = self.coef.to_digits_be();
        let adjusted = self.exp as i64 + digits.len() as i64 - 1;
        let body = if self.exp <= 0 && adjusted >= -6 {
            if self.exp == 0 {
                digits
            } else {
                let point = digits.len() as i64 + self.exp as i64;
                if point > 0 {
                    let point = point as usize;
                    format!("{}.{}", &digits[..point], &digits[point..])
                } else {
                    format!("0.{}{}", "0".repeat((-point) as usize), digits)
                }
            }
        } else {
            let mantissa = if digits.len() == 1 {
                digits
            } else {
                format!("{}.{}", &digits[..1], &digits[1..])
            };
            let exponent = if adjusted >= 0 {
                format!("E+{adjusted}")
            } else {
                format!("E{adjusted}")
            };
            format!("{mantissa}{exponent}")
        };
        if self.sign {
            format!("-{body}")
        } else {
            body
        }
    }

    /// Round the coefficient to `precision` significant digits with
    /// half-even, adjusting the exponent. No-op when the value already fits.
    fn round_to_precision(&mut self, precision: usize) {
        if self.coef.digit_count() <= precision {
            return;
        }
        let drop = self.coef.digit_count() - precision;
        let (mut quotient, remainder) = self.coef.divmod_pow10(drop);
        let twice_remainder = remainder.add(&remainder);
        let half = BigUint::pow10(drop);
        let ordering = twice_remainder.compare(&half);
        if ordering == Ordering::Greater
            || (ordering == Ordering::Equal && quotient.units_digit() % 2 == 1)
        {
            quotient = quotient.add_one();
        }
        let mut exp = self.exp + drop as i32;
        if quotient.digit_count() > precision {
            // A carry turned 999...9 into 1000...0; renormalise to `precision`
            // digits by dividing out one ten and raising the exponent.
            let (trimmed, _) = quotient.divmod_pow10(1);
            quotient = trimmed;
            exp += 1;
        }
        self.coef = quotient;
        self.exp = exp;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn d(s: &str) -> Decimal {
        Decimal::parse(s).unwrap()
    }

    #[test]
    fn parses_and_roundtrips_str() {
        for (input, expected) in [
            ("0.1", "0.1"),
            ("1E+3", "1E+3"),
            ("1.50E+3", "1.50E+3"),
            ("1E-7", "1E-7"),
            ("1E-6", "0.000001"),
            ("-0.0", "-0.0"),
            ("0.00000001", "1E-8"),
        ] {
            assert_eq!(d(input).str_repr(), expected, "input {input}");
        }
    }

    #[test]
    fn numeric_comparison_ignores_representation() {
        assert_eq!(d("0.1").compare_numeric(&d("0.10")), Ordering::Equal);
        assert_eq!(d("-0").compare_numeric(&d("0")), Ordering::Equal);
        assert_eq!(d("1E+3").compare_numeric(&d("1000")), Ordering::Equal);
        assert_eq!(d("-2.5").compare_numeric(&d("0.1")), Ordering::Less);
    }

    #[test]
    fn non_finite_is_reported() {
        for s in ["NaN", "sNaN", "Infinity", "-Infinity"] {
            assert_eq!(Decimal::parse(s), Err(ParseError::NonFinite));
        }
    }

    #[test]
    fn division_matches_python_examples() {
        // Exact quotient, preferred-exponent and half-even rounding cases
        // recorded from Python's Decimal under the given context precision.
        for (a, b, precision, expected) in [
            ("1", "8", 28, "0|125|-3~0.125"),
            ("1.00", "2", 28, "0|50|-2~0.50"),
            ("100000", "3", 4, "0|3333|1~3.333E+4"),
            ("101", "1000", 2, "0|10|-2~0.10"),
            (
                "2",
                "3",
                28,
                "0|6666666666666666666666666667|-28~0.6666666666666666666666666667",
            ),
            ("-1", "8", 28, "1|125|-3~-0.125"),
            ("0E+5", "5", 28, "0|0|5~0E+5"),
        ] {
            let got = d(a).div(&d(b), precision).unwrap();
            let token = format!("{}~{}", got.canonical(), got.str_repr());
            assert_eq!(token, expected, "{a}/{b}");
        }
        assert!(d("1").div(&d("0"), 28).is_err());
    }
}
