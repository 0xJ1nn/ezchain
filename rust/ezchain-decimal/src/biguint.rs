//! Dependency-free arbitrary-precision unsigned integer over decimal digits.
//!
//! Digits are stored little-endian (`digits[0]` is the least significant digit),
//! each in `0..=9`. The canonical form has no high-order zero digits; zero is a
//! single digit `[0]`. This is deliberately a decimal-digit representation: it
//! mirrors Python's `Decimal.as_tuple().digits` directly, so digit counts (which
//! `money.py` uses to size its contexts) are exact rather than estimated from a
//! binary word size.

use std::cmp::Ordering;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BigUint {
    digits: Vec<u8>,
}

impl BigUint {
    /// The value zero, as a single digit.
    pub fn zero() -> Self {
        Self { digits: vec![0] }
    }

    /// The value one.
    pub fn one() -> Self {
        Self { digits: vec![1] }
    }

    /// A single decimal digit (`0..=9`), used when growing a quotient.
    pub fn from_digit(d: u8) -> Self {
        Self {
            digits: vec![d % 10],
        }
    }

    /// Build from a big-endian digit string (most significant digit first).
    ///
    /// Leading zeros are stripped, matching `Decimal.as_tuple().digits`; an
    /// empty or all-zero string becomes canonical zero.
    pub fn from_digits_str(s: &str) -> Option<Self> {
        let bytes = s.as_bytes();
        if bytes.is_empty() {
            return None;
        }
        if !bytes.iter().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let first = bytes.iter().position(|b| *b != b'0');
        let Some(start) = first else {
            return Some(Self::zero());
        };
        let mut digits: Vec<u8> = bytes[start..].iter().rev().map(|b| b - b'0').collect();
        if digits.is_empty() {
            digits.push(0);
        }
        Some(Self { digits })
    }

    /// Number of decimal digits in the canonical form (zero counts as one).
    pub fn digit_count(&self) -> usize {
        self.digits.len()
    }

    /// True for the canonical zero value.
    pub fn is_zero(&self) -> bool {
        self.digits.len() == 1 && self.digits[0] == 0
    }

    /// The least significant digit (0 for zero).
    pub fn units_digit(&self) -> u8 {
        self.digits[0]
    }

    fn trim(&mut self) {
        while self.digits.len() > 1 && self.digits.last() == Some(&0) {
            self.digits.pop();
        }
    }

    fn from_little_endian(mut digits: Vec<u8>) -> Self {
        if digits.is_empty() {
            digits.push(0);
        }
        let mut value = Self { digits };
        value.trim();
        value
    }

    /// Magnitude comparison.
    pub fn compare(&self, other: &Self) -> Ordering {
        match self.digits.len().cmp(&other.digits.len()) {
            Ordering::Equal => {}
            other_ordering => return other_ordering,
        }
        for (a, b) in self.digits.iter().rev().zip(other.digits.iter().rev()) {
            match a.cmp(b) {
                Ordering::Equal => {}
                other_ordering => return other_ordering,
            }
        }
        Ordering::Equal
    }

    /// Exact sum of two magnitudes.
    pub fn add(&self, other: &Self) -> Self {
        let mut digits = Vec::with_capacity(self.digits.len().max(other.digits.len()) + 1);
        let mut carry = 0u8;
        for i in 0..self.digits.len().max(other.digits.len()) {
            let a = self.digits.get(i).copied().unwrap_or(0);
            let b = other.digits.get(i).copied().unwrap_or(0);
            let sum = a + b + carry;
            digits.push(sum % 10);
            carry = sum / 10;
        }
        if carry != 0 {
            digits.push(carry);
        }
        Self::from_little_endian(digits)
    }

    /// Add one to a magnitude.
    pub fn add_one(&self) -> Self {
        self.add(&Self::one())
    }

    /// Exact difference of two magnitudes; assumes `self >= other`.
    pub fn sub(&self, other: &Self) -> Self {
        debug_assert!(self.compare(other) != Ordering::Less);
        let mut digits = Vec::with_capacity(self.digits.len());
        let mut borrow = 0i16;
        for (i, &a) in self.digits.iter().enumerate() {
            let b = other.digits.get(i).copied().unwrap_or(0) as i16;
            let mut diff = a as i16 - b - borrow;
            if diff < 0 {
                diff += 10;
                borrow = 1;
            } else {
                borrow = 0;
            }
            digits.push(diff as u8);
        }
        debug_assert_eq!(borrow, 0);
        Self::from_little_endian(digits)
    }

    /// Exact product of two magnitudes (grade-school multiplication).
    pub fn mul(&self, other: &Self) -> Self {
        if self.is_zero() || other.is_zero() {
            return Self::zero();
        }
        let mut digits = vec![0u8; self.digits.len() + other.digits.len()];
        for (i, &a) in self.digits.iter().enumerate() {
            let mut carry = 0u16;
            for (j, &b) in other.digits.iter().enumerate() {
                let slot = i + j;
                let product = a as u16 * b as u16 + digits[slot] as u16 + carry;
                digits[slot] = (product % 10) as u8;
                carry = product / 10;
            }
            let mut slot = i + other.digits.len();
            while carry != 0 {
                let total = digits[slot] as u16 + carry;
                digits[slot] = (total % 10) as u8;
                carry = total / 10;
                slot += 1;
            }
        }
        Self::from_little_endian(digits)
    }

    /// Multiply by a single decimal digit (`0..=9`).
    pub fn mul_small(&self, d: u8) -> Self {
        if d == 0 || self.is_zero() {
            return Self::zero();
        }
        let mut digits = Vec::with_capacity(self.digits.len() + 1);
        let mut carry = 0u16;
        for &x in &self.digits {
            let product = x as u16 * d as u16 + carry;
            digits.push((product % 10) as u8);
            carry = product / 10;
        }
        if carry != 0 {
            digits.push(carry as u8);
        }
        Self::from_little_endian(digits)
    }

    /// Divide by a non-zero magnitude, returning `(quotient, remainder)`.
    ///
    /// Schoolbook long division over decimal digits: each step shifts the
    /// running remainder by one digit, then picks the largest `0..=9` multiple
    /// of the divisor that still fits. Because the remainder is always smaller
    /// than the divisor before a step, the next quotient digit fits in one
    /// decimal digit.
    pub fn div_mod(&self, divisor: &Self) -> (Self, Self) {
        assert!(!divisor.is_zero(), "division by zero");
        if self.compare(divisor) == Ordering::Less {
            return (Self::zero(), self.clone());
        }
        let mut quotient = vec![0u8; self.digits.len()];
        let mut remainder = Self::zero();
        for i in (0..self.digits.len()).rev() {
            // remainder = remainder * 10 + next dividend digit
            remainder = remainder.mul_pow10(1).add(&Self {
                digits: vec![self.digits[i]],
            });
            let mut digit = 9u8;
            while divisor.mul_small(digit).compare(&remainder) == Ordering::Greater {
                digit -= 1;
            }
            if digit != 0 {
                remainder = remainder.sub(&divisor.mul_small(digit));
                quotient[i] = digit;
            }
        }
        (Self::from_little_endian(quotient), remainder)
    }

    /// Multiply by ten raised to `k` (prepend `k` low-order zeros).
    pub fn mul_pow10(&self, k: usize) -> Self {
        if self.is_zero() || k == 0 {
            return self.clone();
        }
        let mut digits = vec![0u8; k];
        digits.extend_from_slice(&self.digits);
        Self { digits }
    }

    /// Ten raised to `k`, as a magnitude.
    pub fn pow10(k: usize) -> Self {
        let mut digits = vec![0u8; k];
        digits.push(1);
        Self { digits }
    }

    /// Split into quotient and remainder on division by ten raised to `k`.
    ///
    /// In a decimal-digit representation this is a digit split, not long
    /// division: the low `k` digits are the remainder, the rest the quotient.
    pub fn divmod_pow10(&self, k: usize) -> (Self, Self) {
        if k == 0 {
            return (self.clone(), Self::zero());
        }
        if self.digits.len() <= k {
            return (Self::zero(), Self::from_little_endian(self.digits.clone()));
        }
        let quotient = Self::from_little_endian(self.digits[k..].to_vec());
        let remainder = Self::from_little_endian(self.digits[..k].to_vec());
        (quotient, remainder)
    }

    /// Render as a big-endian decimal digit string (no leading zeros).
    pub fn to_digits_be(&self) -> String {
        self.digits
            .iter()
            .rev()
            .map(|d| (b'0' + *d) as char)
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_zero_and_leading_zeros() {
        assert_eq!(BigUint::from_digits_str("000").unwrap(), BigUint::zero());
        assert_eq!(
            BigUint::from_digits_str("00120").unwrap().to_digits_be(),
            "120"
        );
    }

    #[test]
    fn add_mul_and_shift() {
        let a = BigUint::from_digits_str("99").unwrap();
        assert_eq!(a.add(&BigUint::one()).to_digits_be(), "100");
        assert_eq!(a.mul(&a).to_digits_be(), "9801");
        assert_eq!(a.mul_pow10(2).to_digits_be(), "9900");
    }

    #[test]
    fn divmod_pow10_split() {
        let a = BigUint::from_digits_str("123456789").unwrap();
        let (q, r) = a.divmod_pow10(3);
        assert_eq!(q.to_digits_be(), "123456");
        assert_eq!(r.to_digits_be(), "789");
        assert_eq!(q.mul_pow10(3).add(&r), a);
    }

    #[test]
    fn general_div_mod() {
        for (a, b, q, r) in [
            ("100000", "3", "33333", "1"),
            ("1", "8", "0", "1"),
            ("999", "2", "499", "1"),
            ("1000", "8", "125", "0"),
            ("12345", "100", "123", "45"),
        ] {
            let a = BigUint::from_digits_str(a).unwrap();
            let b = BigUint::from_digits_str(b).unwrap();
            let (got_q, got_r) = a.div_mod(&b);
            assert_eq!(got_q.mul(&b).add(&got_r), a, "{a:?} / {b:?}");
            assert_eq!(got_r.compare(&b), Ordering::Less, "{a:?} / {b:?}");
            assert_eq!(got_q.to_digits_be(), q);
            assert_eq!(got_r.to_digits_be(), r);
        }
    }
}
