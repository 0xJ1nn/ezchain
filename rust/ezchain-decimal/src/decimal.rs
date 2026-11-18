//! A sign + arbitrary-precision decimal coefficient + exponent value, with the
//! operations `money.py` actually uses.
//!
//! This is not a general decimal implementation. It deliberately implements only
//! construction from string and integer, numeric comparison, context-checked
//! addition, multiplication and division, context-precision derivation, and
//! quantisation with half-even / floor. Currency conversion and canonical JSON
//! are out of scope (see the crate README).
//!
//! Division produces the correctly rounded (half-even) quotient at a caller-
//! supplied precision, including the preferred-exponent and trailing-zero
//! behaviour of Python's `Decimal.__truediv__`.
//!
//! Operations take a [`Context`]. `money.py` overrides only `prec` in its
//! `localcontext()` blocks; `emin`, `emax` and `clamp` stay at the interpreter
//! defaults, which are captured by [`DEFAULT_CONTEXT`] / [`Context::with_precision`].
//! That is the ambient context the money path actually runs under.

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

/// The decimal context `money.py` inherits from the interpreter.
///
/// `money.py`'s `localcontext()` blocks set `context.prec` only. `emin`, `emax`
/// and `clamp` remain at their defaults, so `DEFAULT_CONTEXT` and
/// `Context::with_precision` model exactly the ambient context the money path
/// runs under. Non-default `clamp`/`emin`/`emax` values are never set by
/// `money.py` and are therefore not claimed as proven parity (README).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Context {
    pub prec: i64,
    pub emin: i64,
    pub emax: i64,
    pub clamp: bool,
}

/// Python's default context: `prec=28`, `Emin=-999999`, `Emax=999999`, `clamp=0`.
pub const DEFAULT_CONTEXT: Context = Context {
    prec: 28,
    emin: -999_999,
    emax: 999_999,
    clamp: false,
};

/// CPython's `decimal.MAX_PREC` / `MAX_EMAX` on a 64-bit build. A context whose
/// `prec` exceeds this makes `context.prec = n` raise `ValueError` before any
/// operation runs; `money.py` does not catch that exception.
pub const MAX_PREC: i64 = 999_999_999_999_999_999;
/// The largest exponent `Decimal(str)` accepts on a 64-bit CPython build.
pub const MAX_EMAX: i64 = 999_999_999_999_999_999;

impl Context {
    /// A context with a derived precision and the default exponent limits.
    pub fn with_precision(prec: i64) -> Self {
        Self {
            prec,
            ..DEFAULT_CONTEXT
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum ParseError {
    /// A non-finite spelling (`NaN`, `sNaN`, `Infinity`, ...).
    NonFinite,
    /// A string that is not a valid decimal literal.
    Syntax,
    /// An exponent outside CPython's constructible `Decimal` range
    /// (`decimal.InvalidOperation` at construction).
    ExpOutOfRange,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Decimal {
    /// `true` means negative; the sign of a zero is preserved, as in Python.
    pub sign: bool,
    /// Unsigned coefficient digits (canonical, arbitrary precision).
    pub coef: BigUint,
    /// Base-10 exponent. `i64` spans CPython's whole constructible range
    /// (`MAX_EMAX`), so exponents beyond the old `i32` representation are proven.
    pub exp: i64,
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
            0i64
        } else {
            let exponent = exponent.strip_prefix('+').unwrap_or(exponent);
            let value = exponent
                .parse::<i64>()
                .map_err(|_| ParseError::ExpOutOfRange)?;
            if !(-MAX_EMAX..=MAX_EMAX).contains(&value) {
                return Err(ParseError::ExpOutOfRange);
            }
            value
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
            .checked_sub(fraction_len as i64)
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

    /// The adjusted exponent (`exp + coefficient_digits - 1`), computed in
    /// `i128` so even a maximal `i64` exponent plus a long coefficient cannot
    /// overflow. This is the value Python compares against `Emin`/`Emax`.
    pub fn adjusted(&self) -> i128 {
        self.exp as i128 + self.coef.digit_count() as i128 - 1
    }

    /// Numeric comparison, ignoring representation (so `0.1 == 0.10` and
    /// `-0 == 0`). This is the comparison `money.py` operators perform.
    ///
    /// Non-zero same-sign values are ordered by adjusted exponent first, so a
    /// comparison of two extreme exponents never tries to materialise an
    /// aligned coefficient the way naive digit-padding would.
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

        let adjusted = self.adjusted();
        let other_adjusted = other.adjusted();
        if adjusted != other_adjusted {
            return if self.sign {
                other_adjusted.cmp(&adjusted)
            } else {
                adjusted.cmp(&other_adjusted)
            };
        }
        // Equal adjusted exponents imply the exponent difference equals the
        // coefficient-digit-count difference, which is bounded by the stored
        // digits — aligning here cannot allocate an unbounded coefficient.
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

    /// Context-checked addition. The result exponent is the smaller operand
    /// exponent, matching Python's addition. An adjusted exponent above `Emax`
    /// raises Python's `Overflow` (trapped by default).
    pub fn add(&self, other: &Self, ctx: &Context) -> Result<Self, MoneyError> {
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
        result.round_to_precision(ctx.prec as usize);
        result.check_overflow(ctx)?;
        Ok(result)
    }

    /// Context-checked multiplication (half-even under `ctx.prec`).
    pub fn mul(&self, other: &Self, ctx: &Context) -> Result<Self, MoneyError> {
        let coef = self.coef.mul(&other.coef);
        let exp = (self.exp as i128 + other.exp as i128)
            .try_into()
            .map_err(|_| MoneyError::Overflow)?;
        let mut result = Self {
            sign: self.sign ^ other.sign,
            coef,
            exp,
        };
        result.round_to_precision(ctx.prec as usize);
        result.check_overflow(ctx)?;
        Ok(result)
    }

    /// Context-checked division with the given context precision (half-even),
    /// matching Python's `Decimal.__truediv__` under `localcontext().prec`.
    ///
    /// The exact quotient of the coefficients is expanded digit by digit until
    /// it terminates or has `prec + 1` significant digits, then rounded
    /// half-even to `prec` digits. The result exponent is the preferred
    /// exponent (`self.exp - other.exp`) minus the fractional digits generated,
    /// plus any digits dropped during rounding.
    pub fn div(&self, other: &Self, ctx: &Context) -> Result<Self, MoneyError> {
        if other.is_zero() {
            return Err(MoneyError::DivisionByZero);
        }
        let precision = ctx.prec as usize;
        let sign = self.sign ^ other.sign;
        let preferred_exp = self.exp as i128 - other.exp as i128;
        if self.is_zero() {
            // Python keeps the preferred exponent for a zero quotient.
            let exp = preferred_exp.try_into().map_err(|_| MoneyError::Overflow)?;
            return Ok(Self {
                sign,
                coef: BigUint::zero(),
                exp,
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

        let mut exp = preferred_exp - fractional_digits as i128;
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
            exp += drop as i128;
        }

        let exp = exp.try_into().map_err(|_| MoneyError::Overflow)?;
        let result = Self {
            sign,
            coef: coefficient,
            exp,
        };
        result.check_overflow(ctx)?;
        Ok(result)
    }

    /// The quantize operation used by `quantize_money` / `floor_money`: round
    /// the value to `target_exp` under `mode` and return a value with exactly
    /// that exponent.
    ///
    /// Python raises `InvalidOperation` (trapped by default) when the result
    /// would need more than `ctx.prec` digits or its adjusted exponent exceeds
    /// `ctx.emax`. The `Emax` check runs *before* any coefficient is grown, so
    /// an exponent such as `1E+9999999999` refuses instead of allocating a
    /// ten-billion-digit coefficient. `money.py` catches that exception and
    /// turns it into its own `MoneyError`.
    pub fn quantize(
        &self,
        target_exp: i64,
        mode: RoundMode,
        ctx: &Context,
    ) -> Result<Self, MoneyError> {
        if self.adjusted() > ctx.emax as i128 {
            return Err(MoneyError::InvalidOperation);
        }
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
        if coef.digit_count() as i64 > ctx.prec {
            return Err(MoneyError::InvalidOperation);
        }
        let result = Self {
            sign: self.sign,
            coef,
            exp: target_exp,
        };
        if !result.is_zero() && result.adjusted() > ctx.emax as i128 {
            return Err(MoneyError::InvalidOperation);
        }
        Ok(result)
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
        let adjusted = self.adjusted();
        let body = if self.exp <= 0 && adjusted >= -6 {
            if self.exp == 0 {
                digits
            } else {
                let point = self.exp as i128 + digits.len() as i128;
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

    /// A non-zero result whose adjusted exponent exceeds `Emax` raises
    /// Python's `Overflow` under the default (trapping) context.
    fn check_overflow(&self, ctx: &Context) -> Result<(), MoneyError> {
        if !self.is_zero() && self.adjusted() > ctx.emax as i128 {
            Err(MoneyError::Overflow)
        } else {
            Ok(())
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
        let mut exp = self.exp as i128 + drop as i128;
        if quotient.digit_count() > precision {
            // A carry turned 999...9 into 1000...0; renormalise to `precision`
            // digits by dividing out one ten and raising the exponent.
            let (trimmed, _) = quotient.divmod_pow10(1);
            quotient = trimmed;
            exp += 1;
        }
        self.coef = quotient;
        self.exp = exp.try_into().expect("rounded exponent fits i64");
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
        // Extreme exponents compare by adjusted exponent without materialising
        // an aligned coefficient.
        assert_eq!(
            d("1E+9999999999").compare_numeric(&d("1E-9999999999")),
            Ordering::Greater
        );
    }

    #[test]
    fn non_finite_is_reported() {
        for s in ["NaN", "sNaN", "Infinity", "-Infinity"] {
            assert_eq!(Decimal::parse(s), Err(ParseError::NonFinite));
        }
    }

    #[test]
    fn exponents_outside_i32_are_supported() {
        assert_eq!(d("1E+9999999999").exp, 9_999_999_999);
        assert_eq!(d("1E-9999999999").exp, -9_999_999_999);
        assert_eq!(d("1E+999999999999999999").exp, MAX_EMAX);
        assert_eq!(
            Decimal::parse("1E+9999999999999999999"),
            Err(ParseError::ExpOutOfRange)
        );
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
            let ctx = Context::with_precision(precision as i64);
            let got = d(a).div(&d(b), &ctx).unwrap();
            let token = format!("{}~{}", got.canonical(), got.str_repr());
            assert_eq!(token, expected, "{a}/{b}");
        }
        let ctx = Context::with_precision(28);
        assert!(d("1").div(&d("0"), &ctx).is_err());
    }
}
