//! Minimal deterministic binary codec for the typed records carried inside a frame.
//!
//! [`crate::framing`](ezchain_rpc::framing) owns the six-byte frame header and treats the
//! payload as opaque bytes. This module is the small layer that turns those bytes into the
//! [`FieldMap`] the existing [`ezchain_rpc::dispatch`] kernel consumes, and turns a typed
//! result or error back into bytes the framing codec can carry. It is pure: no sockets, no
//! runtime, no I/O, no keys and no money arithmetic.
//!
//! # Record format
//!
//! ```text
//! u16             field count (big-endian)
//! repeat count times:
//!   u16           field-name length (big-endian)
//!   bytes         field name, UTF-8
//!   u8            value tag (see below)
//!   u32           value-byte length (big-endian)
//!   bytes         value bytes
//! ```
//!
//! Tags: `1` string, `2` boolean (`b"0"`/`b"1"`), `3` null (zero bytes), `4` integer text,
//! `5` float text. Numbers stay text, exactly as in [`ezchain_rpc::wire::WireValue`], so no
//! binary float or decimal precision is invented at the edge.
//!
//! Decoding is strict and total: every fault is a typed [`CodecError`], including a name that
//! repeats, a tag that is not declared, a non-UTF-8 scalar or surplus bytes. No input can
//! panic — lengths are checked against the bytes actually present before any slice is taken.
//! The caller bounds the whole buffer with the framing limits before calling [`decode_field_map`].

use ezchain_rpc::dispatch::{HandlerFamily, HandlerOutcome};
use ezchain_rpc::wire::{FieldMap, WireValue};

const TAG_STR: u8 = 1;
const TAG_BOOL: u8 = 2;
const TAG_NULL: u8 = 3;
const TAG_INT: u8 = 4;
const TAG_FLOAT: u8 = 5;

/// Why a typed record could not be encoded or decoded.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CodecError {
    /// A length prefix promised more bytes than the buffer holds.
    Truncated { needed: usize, available: usize },
    /// The record has more fields than the two-byte count can express.
    TooManyFields(usize),
    /// A field name is longer than the two-byte length prefix can express.
    NameTooLong { length: usize },
    /// A scalar is longer than the four-byte length prefix can express.
    ValueTooLong { length: usize },
    /// The tag is not one of the five declared tags.
    UnknownTag(u8),
    /// A boolean was not exactly one `0` or `1` byte.
    InvalidBool,
    /// A null carried value bytes.
    InvalidNull,
    /// A string-typed field was not valid UTF-8.
    InvalidUtf8,
    /// A field name appeared more than once in one record.
    DuplicateField,
    /// Bytes remained after the last declared field.
    TrailingBytes,
}

/// Take `n` bytes from the front, returning the head and the untouched tail, or `None` when
/// the buffer is shorter. Every read in the decoder goes through this so no slice can panic.
fn take(input: &[u8], n: usize) -> Option<(&[u8], &[u8])> {
    if input.len() < n {
        None
    } else {
        Some((&input[..n], &input[n..]))
    }
}

fn truncated(needed: usize, available: usize) -> CodecError {
    CodecError::Truncated { needed, available }
}

fn tag_of(value: &WireValue) -> u8 {
    match value {
        WireValue::Str(_) => TAG_STR,
        WireValue::Bool(_) => TAG_BOOL,
        WireValue::Null => TAG_NULL,
        WireValue::Int(_) => TAG_INT,
        WireValue::Float(_) => TAG_FLOAT,
    }
}

fn bytes_of(value: &WireValue) -> &[u8] {
    match value {
        WireValue::Str(text) | WireValue::Int(text) | WireValue::Float(text) => text.as_bytes(),
        WireValue::Bool(true) => b"1",
        WireValue::Bool(false) => b"0",
        WireValue::Null => &[],
    }
}

/// Encode one field map into a fresh byte buffer.
///
/// Refuses a record it cannot represent rather than truncating a length. A caller that feeds
/// the result to [`ezchain_rpc::framing::encode`] under the same limit round-trips exactly.
pub fn encode_field_map(map: &FieldMap) -> Result<Vec<u8>, CodecError> {
    let count = u16::try_from(map.len()).map_err(|_| CodecError::TooManyFields(map.len()))?;
    let mut out = Vec::new();
    out.extend_from_slice(&count.to_be_bytes());
    for (name, value) in map.iter() {
        let name_len = u16::try_from(name.len())
            .map_err(|_| CodecError::NameTooLong { length: name.len() })?;
        let bytes = bytes_of(value);
        let value_len = u32::try_from(bytes.len()).map_err(|_| CodecError::ValueTooLong {
            length: bytes.len(),
        })?;
        out.extend_from_slice(&name_len.to_be_bytes());
        out.extend_from_slice(name.as_bytes());
        out.push(tag_of(value));
        out.extend_from_slice(&value_len.to_be_bytes());
        out.extend_from_slice(bytes);
    }
    Ok(out)
}

fn text(bytes: &[u8]) -> Result<String, CodecError> {
    let value = std::str::from_utf8(bytes).map_err(|_| CodecError::InvalidUtf8)?;
    Ok(value.to_string())
}

/// Decode exactly one field map from `input`, or return the first typed [`CodecError`].
///
/// The input must be exactly one record: surplus bytes are a [`CodecError::TrailingBytes`]
/// refusal, never silently dropped.
pub fn decode_field_map(input: &[u8]) -> Result<FieldMap, CodecError> {
    let (raw_count, mut rest) = take(input, 2).ok_or(truncated(2, input.len()))?;
    let count = usize::from(u16::from_be_bytes([raw_count[0], raw_count[1]]));
    let mut map = FieldMap::new();

    for _ in 0..count {
        let (raw_name_len, tail) = take(rest, 2).ok_or(truncated(2, rest.len()))?;
        rest = tail;
        let name_len = usize::from(u16::from_be_bytes([raw_name_len[0], raw_name_len[1]]));

        let (name_bytes, tail) = take(rest, name_len).ok_or(truncated(name_len, rest.len()))?;
        rest = tail;
        let name = std::str::from_utf8(name_bytes).map_err(|_| CodecError::InvalidUtf8)?;
        if map.contains(name) {
            return Err(CodecError::DuplicateField);
        }

        let (raw_tag, tail) = take(rest, 1).ok_or(truncated(1, rest.len()))?;
        rest = tail;

        let (raw_value_len, tail) = take(rest, 4).ok_or(truncated(4, rest.len()))?;
        rest = tail;
        let value_len = u32::from_be_bytes([
            raw_value_len[0],
            raw_value_len[1],
            raw_value_len[2],
            raw_value_len[3],
        ]) as usize;

        let (value_bytes, tail) = take(rest, value_len).ok_or(truncated(value_len, rest.len()))?;
        rest = tail;

        let value = match raw_tag[0] {
            TAG_STR => WireValue::Str(text(value_bytes)?),
            TAG_INT => WireValue::Int(text(value_bytes)?),
            TAG_FLOAT => WireValue::Float(text(value_bytes)?),
            TAG_BOOL => match value_bytes {
                b"1" => WireValue::Bool(true),
                b"0" => WireValue::Bool(false),
                _ => return Err(CodecError::InvalidBool),
            },
            TAG_NULL => {
                if !value_bytes.is_empty() {
                    return Err(CodecError::InvalidNull);
                }
                WireValue::Null
            }
            other => return Err(CodecError::UnknownTag(other)),
        };
        map.insert(name, value);
    }

    if !rest.is_empty() {
        return Err(CodecError::TrailingBytes);
    }
    Ok(map)
}

/// The typed success payload: the ack the pure dispatch handler produced.
pub fn outcome_field_map(outcome: &HandlerOutcome) -> FieldMap {
    let family = match outcome.family {
        HandlerFamily::Read => "read",
        HandlerFamily::Trade => "trade",
        HandlerFamily::Admin => "admin",
    };
    FieldMap::with(vec![
        ("status", WireValue::Str("ok".to_string())),
        ("method", WireValue::Str(outcome.method.to_string())),
        ("family", WireValue::Str(family.to_string())),
    ])
}

/// The typed error payload: the `{code, message, retryable}` envelope used by the kernels.
pub fn error_field_map(code: &str, message: &str, retryable: bool) -> FieldMap {
    FieldMap::with(vec![
        ("status", WireValue::Str("error".to_string())),
        ("code", WireValue::Str(code.to_string())),
        ("message", WireValue::Str(message.to_string())),
        ("retryable", WireValue::Bool(retryable)),
    ])
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_wire_value_round_trips_in_order() {
        let mut map = FieldMap::new();
        map.insert("schema", WireValue::Str("ezchain.rpc-method/1".to_string()));
        map.insert("version", WireValue::Int("1".to_string()));
        map.insert("method", WireValue::Str("GET /healthz".to_string()));
        map.insert("flag", WireValue::Bool(true));
        map.insert("off", WireValue::Bool(false));
        map.insert("nothing", WireValue::Null);
        map.insert("ratio", WireValue::Float("0.125".to_string()));
        let bytes = encode_field_map(&map).expect("encode");
        assert_eq!(decode_field_map(&bytes), Ok(map));
    }

    #[test]
    fn empty_map_is_a_two_byte_zero_count() {
        let bytes = encode_field_map(&FieldMap::new()).expect("encode");
        assert_eq!(bytes, vec![0, 0]);
        assert_eq!(decode_field_map(&bytes), Ok(FieldMap::new()));
    }

    #[test]
    fn malformed_records_are_typed_refusals_not_panics() {
        assert_eq!(
            decode_field_map(&[]),
            Err(CodecError::Truncated {
                needed: 2,
                available: 0
            })
        );
        // One field promising a name that is not present.
        let mut short = 1u16.to_be_bytes().to_vec();
        short.extend_from_slice(&9u16.to_be_bytes());
        assert_eq!(
            decode_field_map(&short),
            Err(CodecError::Truncated {
                needed: 9,
                available: 0
            })
        );
        // An undeclared tag.
        let mut bad_tag = 1u16.to_be_bytes().to_vec();
        bad_tag.extend_from_slice(&1u16.to_be_bytes());
        bad_tag.push(b'x');
        bad_tag.push(99);
        bad_tag.extend_from_slice(&0u32.to_be_bytes());
        assert_eq!(decode_field_map(&bad_tag), Err(CodecError::UnknownTag(99)));
        // Surplus bytes after a valid empty record.
        let mut trailing = 0u16.to_be_bytes().to_vec();
        trailing.push(0);
        assert_eq!(decode_field_map(&trailing), Err(CodecError::TrailingBytes));
    }

    #[test]
    fn typed_result_and_error_payloads_have_the_kernel_shape() {
        let outcome = HandlerOutcome {
            method: "GET /healthz",
            family: HandlerFamily::Read,
        };
        let map = outcome_field_map(&outcome);
        assert_eq!(map.get("status"), Some(&WireValue::Str("ok".to_string())));
        assert_eq!(map.get("family"), Some(&WireValue::Str("read".to_string())));

        let error = error_field_map("FORBIDDEN", "denied", false);
        assert_eq!(
            error.get("status"),
            Some(&WireValue::Str("error".to_string()))
        );
        assert_eq!(error.get("retryable"), Some(&WireValue::Bool(false)));
    }
}
