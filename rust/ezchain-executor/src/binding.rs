//! The intent-bound transaction check, ported from `ezchain_core.signing` value objects
//! and `verify_prepared_transaction` / `verify_signed_transaction`, plus the canonical
//! decimal helper the checks share with `pipeline.py`.
//!
//! Python compares decimal fields canonically so `10.0` and `10` bind alike, while any
//! real movement fails. No money arithmetic happens here: an amount is a *canonical
//! decimal string*, produced by pure string manipulation, and equality is string
//! equality on that canonical form. Magnitude comparison (used by receipt verification)
//! compares digit strings, never floats. That is exact for the whole decimal domain and
//! does not depend on a fixed-precision decimal type.
//!
//! SHA-256 digest *computation* and the JSON wire codec stay in Python: they need a
//! crypto/JSON dependency, which this crate deliberately has none of. The digests
//! produced there are carried on the value objects below as opaque strings; this crate
//! ports the *comparison* Python performs on them.

use std::cmp::Ordering;

use crate::Side;

/// Why a decimal string cannot be bound. Python's decoder would reject these before the
/// check runs; refusing here is the fail-closed equivalent.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecimalError {
    /// `NaN`, `sNaN`, `Infinity` or `-Infinity`: `Decimal.is_finite()` is false.
    NonFinite,
    /// Malformed decimal spelling.
    Invalid,
    /// An exponent so large that expanding it would be unreasonable for a bound amount.
    ExponentTooLarge,
}

/// Guards against a hostile exponent expanding into an unbounded allocation. The bound
/// is far above any settlement amount (the ledger uses 8 fractional digits).
const MAX_EXPANDED_DIGITS: i64 = 10_000;

/// A decimal amount in the single canonical spelling Python's `_canonical_decimal`
/// produces: no exponent, no leading zeros, no trailing fraction zeros, no `-0`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct BoundAmount(String);

impl BoundAmount {
    /// Canonicalize a decimal string exactly as `ezchain_core.signing._canonical_decimal`
    /// does after `Decimal(value)` parses it.
    pub fn parse(raw: &str) -> Result<Self, DecimalError> {
        let trimmed = raw.trim();
        if trimmed.is_empty() {
            return Err(DecimalError::Invalid);
        }

        let (negative, body) = match trimmed.as_bytes()[0] {
            b'+' => (false, &trimmed[1..]),
            b'-' => (true, &trimmed[1..]),
            _ => (false, trimmed),
        };
        if body.is_empty() {
            return Err(DecimalError::Invalid);
        }

        let lowered = body.to_ascii_lowercase();
        if matches!(lowered.as_str(), "nan" | "snan" | "inf" | "infinity") {
            return Err(DecimalError::NonFinite);
        }

        let (mantissa, exponent) = split_exponent(body)?;
        if mantissa.matches('.').count() > 1 {
            return Err(DecimalError::Invalid);
        }
        let (int_part, frac_part) = match mantissa.split_once('.') {
            Some((i, f)) => (i, f),
            None => (mantissa, ""),
        };
        if int_part.is_empty() && frac_part.is_empty() {
            return Err(DecimalError::Invalid);
        }
        if !int_part.bytes().all(|b| b.is_ascii_digit())
            || !frac_part.bytes().all(|b| b.is_ascii_digit())
        {
            return Err(DecimalError::Invalid);
        }

        let mut digits = String::with_capacity(int_part.len() + frac_part.len());
        digits.push_str(int_part);
        digits.push_str(frac_part);

        // `format(Decimal, "f")` places the point `fraction_len - exponent` digits from
        // the right of the combined mantissa digits.
        let scale = i64::try_from(frac_part.len())
            .map_err(|_| DecimalError::ExponentTooLarge)?
            .saturating_sub(exponent);
        if scale.abs() > MAX_EXPANDED_DIGITS || digits.len() as i64 > MAX_EXPANDED_DIGITS {
            return Err(DecimalError::ExponentTooLarge);
        }
        let fixed = to_fixed(&digits, scale);
        Ok(Self(canonicalize_fixed(&fixed, negative)))
    }

    /// The canonical spelling, used as the identity of the bound value.
    pub fn as_canonical(&self) -> &str {
        &self.0
    }

    /// Exact numeric ordering of two canonical decimals, without any arithmetic.
    pub fn numeric_cmp(&self, other: &Self) -> Ordering {
        let (self_negative, self_magnitude) = split_sign(&self.0);
        let (other_negative, other_magnitude) = split_sign(&other.0);
        match (self_negative, other_negative) {
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
            (false, false) => compare_magnitudes(self_magnitude, other_magnitude),
            (true, true) => compare_magnitudes(other_magnitude, self_magnitude),
        }
    }

    /// True when this value is strictly positive (`Decimal > 0`).
    ///
    /// Canonical form is `"0"`, `"-<digits>"` or `"<digits>"` (never `-0`), so this is a
    /// pure spelling test with no arithmetic and no allocation.
    pub fn is_positive(&self) -> bool {
        !self.0.starts_with('-') && self.0 != "0"
    }
}

impl PartialOrd for BoundAmount {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for BoundAmount {
    fn cmp(&self, other: &Self) -> Ordering {
        self.numeric_cmp(other)
    }
}

/// Split off an `e`/`E` exponent, returning the mantissa and the parsed exponent.
fn split_exponent(body: &str) -> Result<(&str, i64), DecimalError> {
    let Some(index) = body.find(['e', 'E']) else {
        return Ok((body, 0));
    };
    let mantissa = &body[..index];
    let mut exponent_text = &body[index + 1..];
    let mut sign = 1i64;
    if let Some(rest) = exponent_text.strip_prefix('+') {
        exponent_text = rest;
    } else if let Some(rest) = exponent_text.strip_prefix('-') {
        exponent_text = rest;
        sign = -1;
    }
    if exponent_text.is_empty() || !exponent_text.bytes().all(|b| b.is_ascii_digit()) {
        return Err(DecimalError::Invalid);
    }
    let mut value = 0i64;
    for byte in exponent_text.bytes() {
        value = value
            .saturating_mul(10)
            .saturating_add(i64::from(byte - b'0'));
    }
    Ok((mantissa, sign * value))
}

/// Expand `digits` into fixed-point form with the point `scale` digits from the right.
fn to_fixed(digits: &str, scale: i64) -> String {
    if scale <= 0 {
        let mut out = String::with_capacity(digits.len() + scale.unsigned_abs() as usize);
        out.push_str(digits);
        for _ in 0..(-scale) {
            out.push('0');
        }
        return out;
    }
    let scale = scale as usize;
    if scale >= digits.len() {
        let mut out = String::with_capacity(scale + 3);
        out.push_str("0.");
        for _ in 0..(scale - digits.len()) {
            out.push('0');
        }
        out.push_str(digits);
        out
    } else {
        let split = digits.len() - scale;
        let mut out = String::with_capacity(digits.len() + 1);
        out.push_str(&digits[..split]);
        out.push('.');
        out.push_str(&digits[split..]);
        out
    }
}

/// Strip insignificant zeros and the sign of zero, producing the canonical spelling.
fn canonicalize_fixed(fixed: &str, negative: bool) -> String {
    let (int_part, frac_part) = match fixed.split_once('.') {
        Some((i, f)) => (i, f),
        None => (fixed, ""),
    };
    let int_trimmed = int_part.trim_start_matches('0');
    let int_canonical = if int_trimmed.is_empty() {
        "0"
    } else {
        int_trimmed
    };
    let frac_trimmed = frac_part.trim_end_matches('0');
    let mut canonical = String::with_capacity(int_canonical.len() + frac_trimmed.len() + 2);
    canonical.push_str(int_canonical);
    if !frac_trimmed.is_empty() {
        canonical.push('.');
        canonical.push_str(frac_trimmed);
    }
    if canonical == "0" || canonical.is_empty() {
        return "0".to_owned();
    }
    if negative {
        canonical.insert(0, '-');
    }
    canonical
}

/// Split the canonical sign. Canonical zero has no sign, so zero is never negative.
fn split_sign(canonical: &str) -> (bool, &str) {
    if let Some(rest) = canonical.strip_prefix('-') {
        (true, rest)
    } else {
        (false, canonical)
    }
}

/// Compare two non-negative canonical magnitudes digit by digit.
fn compare_magnitudes(left: &str, right: &str) -> Ordering {
    let (left_int, left_frac) = match left.split_once('.') {
        Some((i, f)) => (i, f),
        None => (left, ""),
    };
    let (right_int, right_frac) = match right.split_once('.') {
        Some((i, f)) => (i, f),
        None => (right, ""),
    };
    let left_int = left_int.trim_start_matches('0');
    let right_int = right_int.trim_start_matches('0');
    let left_int = if left_int.is_empty() { "0" } else { left_int };
    let right_int = if right_int.is_empty() { "0" } else { right_int };

    let integer_order = left_int
        .len()
        .cmp(&right_int.len())
        .then_with(|| left_int.cmp(right_int));
    if integer_order != Ordering::Equal {
        return integer_order;
    }
    let width = left_frac.len().max(right_frac.len());
    for index in 0..width {
        let a = left_frac.as_bytes().get(index).copied().unwrap_or(b'0');
        let b = right_frac.as_bytes().get(index).copied().unwrap_or(b'0');
        match a.cmp(&b) {
            Ordering::Equal => continue,
            other => return other,
        }
    }
    Ordering::Equal
}

/// The decoded transaction payload: every field the venue can substitute.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TransactionPayload {
    pub cluster: String,
    pub program_id: String,
    pub mint: String,
    pub side: Side,
    pub amount: BoundAmount,
    pub min_out: BoundAmount,
    pub recipient: String,
    pub idempotency_key: String,
}

/// Raw prepared bytes plus the payload decoded from them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PreparedTransaction {
    pub raw: Vec<u8>,
    pub payload_digest: String,
    pub payload: TransactionPayload,
}

/// The immutable approval a transaction must reproduce. `digest` is the SHA-256 the
/// Python boundary computed over the canonical approval; this crate only compares it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ApprovedIntent {
    pub chain: String,
    pub cluster: String,
    pub mint: String,
    pub side: Side,
    pub amount: BoundAmount,
    pub min_out: BoundAmount,
    pub recipient: String,
    pub program_id: String,
    pub idempotency_key: String,
    pub digest: String,
}

/// Bytes a signer produced for one approval, with the digests it carries.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SignedTransaction {
    pub raw: Vec<u8>,
    pub payload_digest: String,
    pub approval_digest: String,
}

/// The first field the prepared transaction changed, or `None` when it is bound.
///
/// Check order mirrors Python exactly (cheapest field first); when several fields differ
/// the first code in this order is the one reported.
pub fn verify_prepared_transaction(
    prepared: &PreparedTransaction,
    approved: &ApprovedIntent,
) -> Option<&'static str> {
    let payload = &prepared.payload;
    if payload.program_id != approved.program_id {
        return Some("TRANSACTION_PROGRAM_MISMATCH");
    }
    if payload.cluster != approved.cluster {
        return Some("TRANSACTION_CLUSTER_MISMATCH");
    }
    if payload.mint != approved.mint {
        return Some("TRANSACTION_MINT_MISMATCH");
    }
    if payload.side != approved.side {
        return Some("TRANSACTION_SIDE_MISMATCH");
    }
    if payload.amount != approved.amount {
        return Some("TRANSACTION_AMOUNT_MISMATCH");
    }
    if payload.min_out != approved.min_out {
        return Some("TRANSACTION_MIN_OUT_MISMATCH");
    }
    if payload.recipient != approved.recipient {
        return Some("TRANSACTION_RECIPIENT_MISMATCH");
    }
    if payload.idempotency_key != approved.idempotency_key {
        return Some("TRANSACTION_IDEMPOTENCY_MISMATCH");
    }
    None
}

/// The first thing that makes `signed` not a signature over the approved payload.
pub fn verify_signed_transaction(
    signed: &SignedTransaction,
    prepared: &PreparedTransaction,
    approved: &ApprovedIntent,
) -> Option<&'static str> {
    if signed.approval_digest != approved.digest {
        return Some("TRANSACTION_APPROVAL_MISMATCH");
    }
    if signed.raw != prepared.raw || signed.payload_digest != prepared.payload_digest {
        return Some("TRANSACTION_DIGEST_MISMATCH");
    }
    verify_prepared_transaction(prepared, approved)
}
