//! Canonical JSON for transaction fingerprints.
//!
//! This crate answers one question from ADR-0050 rule 2: can a dependency-free
//! Rust writer reproduce, byte for byte, the JSON that `packages/core-py` emits
//! for a transaction payload? The Python original is
//!
//! ```python
//! json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
//! ```
//!
//! with the default `ensure_ascii=True`. This writer emits exactly those bytes:
//! object keys sorted by Unicode scalar value, no insignificant whitespace,
//! ASCII-only strings (non-ASCII escaped, non-BMP as surrogate pairs), exact
//! integers, decimals in the canonical fixed-point form that
//! `ezchain_core.signing._canonical_decimal` produces, booleans and null.
//! Non-finite values and Python types the original cannot serialise are refused
//! with the same exception class the original raises.
//!
//! There is deliberately **no hashing here**. A SHA-256 fingerprint needs a
//! crypto dependency and its own decision; this crate stops at the exact bytes
//! that would be hashed. It is pure: no dependencies, no I/O, no keys, no
//! ledger writes, no money-path caller changed and no `unsafe`.

#![forbid(unsafe_code)]

use std::fmt;

/// A value the canonical writer can encode.
///
/// `Decimal` is intentionally a JSON *string*: that is what `signing.py` does
/// before it calls `json.dumps` (amounts and floors are not JSON numbers).
/// `NonFinite` and `Unsupported` model the Python values the original refuses;
/// they exist so the parity gate can assert those refusals in Rust as well.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum JsonValue {
    /// JSON `null` (Python `None`).
    Null,
    /// JSON `true` / `false`.
    Bool(bool),
    /// A Python `int`, carried as canonical decimal text (no leading zeros, no
    /// `+`, no `-0`). Arbitrary precision: it is never parsed into a machine int.
    Int(String),
    /// A `Decimal` canonicalised exactly as `signing.py` does, then written as
    /// a JSON string. See [`canonical_decimal`].
    Decimal(String),
    /// A JSON string.
    Str(String),
    /// A JSON array.
    Array(Vec<JsonValue>),
    /// A JSON object. Field order is irrelevant: keys are sorted on write, as
    /// `sort_keys=True` does. Keys must be unique, as in a Python dict.
    Object(Vec<(String, JsonValue)>),
    /// A Python float that is not finite. Refused because `allow_nan=False`.
    NonFinite(NonFinite),
    /// A Python value whose type `json.dumps` cannot serialise. Refused with
    /// the original's `TypeError`. The string is the Python type name.
    Unsupported(String),
}

/// The three non-finite Python float spellings `allow_nan=False` rejects.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NonFinite {
    Nan,
    PositiveInfinity,
    NegativeInfinity,
}

/// A refusal from the canonical writer. `code()` is the Python exception
/// class, which is what the parity fixture records.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CanonError {
    /// `ValueError: Out of range float values are not JSON compliant` (or the
    /// equivalent refusal from `_canonical_decimal`).
    NonFinite(NonFinite),
    /// `TypeError: Object of type <name> is not JSON serializable`.
    Unsupported(String),
    /// Caller contract violation: [`JsonValue::Int`] did not carry canonical
    /// decimal text. Python ints cannot produce this; it fails closed instead
    /// of emitting bytes the original never would.
    InvalidInteger,
    /// Caller contract violation: [`JsonValue::Decimal`] was not a finite
    /// decimal lexeme.
    InvalidDecimal,
}

impl CanonError {
    /// The Python exception class this refusal mirrors.
    pub fn code(&self) -> &'static str {
        match self {
            Self::NonFinite(_) | Self::InvalidInteger | Self::InvalidDecimal => "ValueError",
            Self::Unsupported(_) => "TypeError",
        }
    }
}

impl fmt::Display for CanonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::NonFinite(_) => f.write_str("Out of range float values are not JSON compliant"),
            Self::Unsupported(name) => {
                write!(f, "Object of type {name} is not JSON serializable")
            }
            Self::InvalidInteger => f.write_str("not a canonical decimal integer lexeme"),
            Self::InvalidDecimal => f.write_str("not a finite decimal lexeme"),
        }
    }
}

impl std::error::Error for CanonError {}

/// Write one value as canonical JSON bytes (an ASCII `String`).
pub fn canonical_json(value: &JsonValue) -> Result<String, CanonError> {
    let mut out = String::new();
    write_value(value, &mut out)?;
    Ok(out)
}

/// Convenience for a top-level object: a transaction payload is always an
/// object, and callers should not have to build a one-element [`JsonValue`]
/// tree just to sort its keys.
pub fn canonical_object<'a>(
    fields: impl IntoIterator<Item = (&'a str, JsonValue)>,
) -> Result<String, CanonError> {
    canonical_json(&JsonValue::Object(
        fields
            .into_iter()
            .map(|(key, value)| (key.to_string(), value))
            .collect(),
    ))
}

/// The canonical fixed-point spelling of a decimal lexeme.
///
/// Mirrors `ezchain_core.signing._canonical_decimal`: `format(value, "f")`,
/// then trailing zeros and a trailing point are stripped, and `-0` becomes
/// `0`. Python `Decimal` accepts surrounding ASCII whitespace, a sign,
/// underscores strictly between digits and an `e`/`E` exponent; all of those
/// are handled here. Non-finite spellings are refused.
pub fn canonical_decimal(text: &str) -> Result<String, CanonError> {
    let trimmed = text.trim_matches(|c: char| c.is_ascii_whitespace());
    if let Some(kind) = non_finite_kind(trimmed) {
        return Err(CanonError::NonFinite(kind));
    }
    if trimmed.is_empty() {
        return Err(CanonError::InvalidDecimal);
    }

    let (negative, rest) = match trimmed.as_bytes()[0] {
        b'+' => (false, &trimmed[1..]),
        b'-' => (true, &trimmed[1..]),
        _ => (false, trimmed),
    };
    if rest.is_empty() {
        return Err(CanonError::InvalidDecimal);
    }

    // Split the exponent off before touching the mantissa.
    let (mantissa, exponent) = match rest.find(['e', 'E']) {
        Some(index) => (
            &rest[..index],
            parse_exponent(&rest[index + 1..]).ok_or(CanonError::InvalidDecimal)?,
        ),
        None => (rest, 0),
    };

    let (whole, fraction) = match mantissa.find('.') {
        Some(index) => {
            let after = &mantissa[index + 1..];
            if after.contains('.') {
                return Err(CanonError::InvalidDecimal);
            }
            (&mantissa[..index], Some(after))
        }
        None => (mantissa, None),
    };

    let fraction_len = fraction.map_or(0, |part| part.len());
    let digits = match (whole.is_empty(), fraction) {
        (true, None) => return Err(CanonError::InvalidDecimal),
        (true, Some(part)) => digit_run(part).ok_or(CanonError::InvalidDecimal)?,
        (false, None) => digit_run(whole).ok_or(CanonError::InvalidDecimal)?,
        (false, Some(part)) => {
            let whole = digit_run(whole).ok_or(CanonError::InvalidDecimal)?;
            // Decimal("1.") is valid and means 1: an empty fraction is allowed
            // when there are whole digits.
            let part = if part.is_empty() {
                String::new()
            } else {
                digit_run(part).ok_or(CanonError::InvalidDecimal)?
            };
            format!("{whole}{part}")
        }
    };
    let digits = digits.trim_start_matches('0');
    if digits.is_empty() {
        // Decimal("0E+5") formats as "0", never as "000000".
        return Ok("0".to_string());
    }

    // The point sits `fraction_len - exponent` digits from the right of the
    // coefficient, whether or not leading zeros were stripped.
    let point = digits.len() as i64 + exponent - fraction_len as i64;
    let body = if point >= digits.len() as i64 {
        format!(
            "{}{}",
            digits,
            "0".repeat((point - digits.len() as i64) as usize)
        )
    } else if point > 0 {
        let point = point as usize;
        format!("{}.{}", &digits[..point], &digits[point..])
    } else {
        format!("0.{}{}", "0".repeat(point.unsigned_abs() as usize), digits)
    };
    // Only the fractional part may lose trailing zeros. Stripping them from
    // the whole body would turn the exponent-generated zeros of "1E+3" into
    // "1", which is not what format(Decimal("1E+3"), "f") produces.
    let body = match body.find('.') {
        Some(dot) => {
            let (whole, fraction) = body.split_at(dot);
            let fraction = fraction[1..].trim_end_matches('0');
            if fraction.is_empty() {
                whole.to_string()
            } else {
                format!("{whole}.{fraction}")
            }
        }
        None => body,
    };
    if body.is_empty() || body == "0" {
        return Ok("0".to_string());
    }
    if negative {
        Ok(format!("-{body}"))
    } else {
        Ok(body)
    }
}

const HEX: &[u8; 16] = b"0123456789abcdef";

fn push_u_escape(out: &mut String, value: u32) {
    out.push('\\');
    out.push('u');
    for shift in [12, 8, 4, 0] {
        out.push(HEX[((value >> shift) & 0xf) as usize] as char);
    }
}

/// Python's `encode_basestring_ascii` with `ensure_ascii=True`: the six named
/// escapes, `\\u00xx` for other controls and DEL, `\\uXXXX` for the rest of the
/// BMP, and a surrogate pair above it. `/` is never escaped.
fn write_string(text: &str, out: &mut String) {
    out.push('"');
    for ch in text.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if ('\u{20}'..='\u{7e}').contains(&c) => out.push(c),
            c if (c as u32) <= 0xffff => push_u_escape(out, c as u32),
            c => {
                let scalar = c as u32 - 0x1_0000;
                push_u_escape(out, 0xd800 + (scalar >> 10));
                push_u_escape(out, 0xdc00 + (scalar & 0x3ff));
            }
        }
    }
    out.push('"');
}

fn write_value(value: &JsonValue, out: &mut String) -> Result<(), CanonError> {
    match value {
        JsonValue::Null => out.push_str("null"),
        JsonValue::Bool(true) => out.push_str("true"),
        JsonValue::Bool(false) => out.push_str("false"),
        JsonValue::Int(text) => {
            out.push_str(validate_integer(text)?);
        }
        JsonValue::Decimal(text) => write_string(&canonical_decimal(text)?, out),
        JsonValue::Str(text) => write_string(text, out),
        JsonValue::Array(items) => {
            out.push('[');
            for (index, item) in items.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_value(item, out)?;
            }
            out.push(']');
        }
        JsonValue::Object(fields) => {
            let mut sorted: Vec<&(String, JsonValue)> = fields.iter().collect();
            // Rust's String order is UTF-8 byte order, which matches Python's
            // code-point order for all valid scalar values.
            sorted.sort_by(|a, b| a.0.cmp(&b.0));
            out.push('{');
            for (index, (key, item)) in sorted.iter().enumerate() {
                if index > 0 {
                    out.push(',');
                }
                write_string(key, out);
                out.push(':');
                write_value(item, out)?;
            }
            out.push('}');
        }
        JsonValue::NonFinite(kind) => return Err(CanonError::NonFinite(*kind)),
        JsonValue::Unsupported(name) => return Err(CanonError::Unsupported(name.clone())),
    }
    Ok(())
}

fn validate_integer(text: &str) -> Result<&str, CanonError> {
    let digits = text.strip_prefix('-').unwrap_or(text);
    if digits.is_empty()
        || !digits.bytes().all(|byte| byte.is_ascii_digit())
        || (digits.len() > 1 && digits.starts_with('0'))
        || text == "-0"
    {
        return Err(CanonError::InvalidInteger);
    }
    Ok(text)
}

/// Digit characters with a single underscore strictly between two digits, as
/// Python `Decimal` accepts.
fn digit_run(text: &str) -> Option<String> {
    let bytes = text.as_bytes();
    if bytes.is_empty() {
        return None;
    }
    let mut out = String::with_capacity(bytes.len());
    for (index, &byte) in bytes.iter().enumerate() {
        if byte == b'_' {
            if index == 0
                || index + 1 == bytes.len()
                || !bytes[index - 1].is_ascii_digit()
                || !bytes[index + 1].is_ascii_digit()
            {
                return None;
            }
        } else if byte.is_ascii_digit() {
            out.push(byte as char);
        } else {
            return None;
        }
    }
    Some(out)
}

/// A signed decimal exponent with underscores between digits. It saturates at
/// `i64` bounds so an extreme lexeme fails later on a range check, never by
/// wrapping (there is no float and no machine-int precision in this writer).
fn parse_exponent(text: &str) -> Option<i64> {
    let (negative, body) = match text.as_bytes().first() {
        Some(b'+') => (false, &text[1..]),
        Some(b'-') => (true, &text[1..]),
        _ => (false, text),
    };
    let digits = digit_run(body)?;
    let mut value: i64 = 0;
    for byte in digits.bytes() {
        value = value
            .saturating_mul(10)
            .saturating_add(i64::from(byte - b'0'));
    }
    Some(if negative { -value } else { value })
}

fn non_finite_kind(text: &str) -> Option<NonFinite> {
    let lower = text.to_ascii_lowercase();
    let unsigned = lower
        .strip_prefix('+')
        .or_else(|| lower.strip_prefix('-'))
        .unwrap_or(&lower);
    match unsigned {
        "nan" | "snan" => Some(NonFinite::Nan),
        "inf" | "infinity" => Some(if lower.starts_with('-') {
            NonFinite::NegativeInfinity
        } else {
            NonFinite::PositiveInfinity
        }),
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sorted_keys_and_no_whitespace() {
        let value = JsonValue::Object(vec![
            ("b".to_string(), JsonValue::Int("1".to_string())),
            ("a".to_string(), JsonValue::Int("2".to_string())),
        ]);
        assert_eq!(canonical_json(&value).as_deref(), Ok("{\"a\":2,\"b\":1}"));
    }

    #[test]
    fn non_bmp_is_a_surrogate_pair() {
        assert_eq!(
            canonical_json(&JsonValue::Str("😀".to_string())).as_deref(),
            Ok("\"\\ud83d\\ude00\"")
        );
    }

    #[test]
    fn non_finite_and_unsupported_are_refused() {
        assert_eq!(
            canonical_json(&JsonValue::NonFinite(NonFinite::Nan)),
            Err(CanonError::NonFinite(NonFinite::Nan))
        );
        assert_eq!(
            canonical_json(&JsonValue::Unsupported("Decimal".to_string())),
            Err(CanonError::Unsupported("Decimal".to_string()))
        );
        assert_eq!(CanonError::NonFinite(NonFinite::Nan).code(), "ValueError");
        assert_eq!(
            CanonError::Unsupported("bytes".to_string()).code(),
            "TypeError"
        );
    }

    #[test]
    fn malformed_integer_lexeme_fails_closed() {
        for bad in ["", "-0", "01", "+1", "1.0", "- 1"] {
            assert_eq!(
                canonical_json(&JsonValue::Int(bad.to_string())),
                Err(CanonError::InvalidInteger),
                "{bad:?}"
            );
        }
    }
}
