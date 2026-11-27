//! Typed wire primitives for the node-facing RPC edge.
//!
//! This module carries no dependencies, performs no I/O and never parses a number into a
//! float. A record crosses the boundary as an ordered [`FieldMap`] whose scalars are
//! [`WireValue`]s; money is an exact [`DecimalLexeme`] preserved as text. A [`VersionedEnvelope`]
//! pins one record kind and integer version so either side of the boundary can reject a
//! document it does not understand (ADR-0050/0052 integration contract).
//!
//! The reason codes below are the stable `type` strings Pydantic emits for the Python
//! originals in `ezchain_api.schemas`. They are the wire vocabulary: a caller compares on
//! the code, never on the human message.

/// Stable reason codes emitted by field validation. These mirror the Pydantic v2 `type`
/// strings of the Python oracle so the two languages speak one vocabulary.
pub mod code {
    pub const EXTRA_FORBIDDEN: &str = "extra_forbidden";
    pub const MISSING: &str = "missing";
    pub const STRING_TYPE: &str = "string_type";
    pub const STRING_TOO_SHORT: &str = "string_too_short";
    pub const STRING_TOO_LONG: &str = "string_too_long";
    pub const LITERAL_ERROR: &str = "literal_error";
    pub const VALUE_ERROR: &str = "value_error";
    pub const GREATER_THAN: &str = "greater_than";
    pub const FINITE_NUMBER: &str = "finite_number";
    pub const DECIMAL_PARSING: &str = "decimal_parsing";
    pub const DECIMAL_TYPE: &str = "decimal_type";
    pub const BOOL_PARSING: &str = "bool_parsing";
    pub const BOOL_TYPE: &str = "bool_type";
}

/// One validation failure. `loc` is the field path; it is empty for a record-level
/// (model) error, exactly as in the Python `detail[].loc`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ValidationError {
    pub code: &'static str,
    pub loc: Vec<String>,
    pub message: String,
}

impl ValidationError {
    /// A failure attributed to one field.
    pub fn field(code: &'static str, field: &str, message: impl Into<String>) -> Self {
        Self {
            code,
            loc: vec![field.to_string()],
            message: message.into(),
        }
    }

    /// A record-level failure with no field location.
    pub fn model(code: &'static str, message: impl Into<String>) -> Self {
        Self {
            code,
            loc: Vec::new(),
            message: message.into(),
        }
    }
}

/// A scalar crossing the boundary. Numbers are held as their exact text — a Rust `f64`
/// is never constructed, so no precision is invented at the edge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WireValue {
    /// A JSON string.
    Str(String),
    /// A JSON boolean.
    Bool(bool),
    /// A JSON `null`.
    Null,
    /// A JSON integer carried verbatim.
    Int(String),
    /// A JSON floating literal carried verbatim. It is the non-finite float case when
    /// the text is `NaN`/`Infinity` or its magnitude overflows an `f64` (as `1e400`
    /// does when a JSON decoder builds a Python `float`).
    Float(String),
}

/// An ordered field map: the explicit, dependency-free stand-in for a parsed object.
/// Insertion order is preserved so error lists are reproducible.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct FieldMap {
    fields: Vec<(String, WireValue)>,
}

impl FieldMap {
    pub fn new() -> Self {
        Self::default()
    }

    /// Build a map from named values, keeping their order.
    pub fn with(fields: Vec<(&str, WireValue)>) -> Self {
        Self {
            fields: fields
                .into_iter()
                .map(|(name, value)| (name.to_string(), value))
                .collect(),
        }
    }

    /// Upsert a field. An existing name keeps its position, as a Python dict does on
    /// assignment; a new name is appended.
    pub fn insert(&mut self, name: impl Into<String>, value: WireValue) {
        let name = name.into();
        if let Some((_, existing)) = self.fields.iter_mut().find(|(key, _)| *key == name) {
            *existing = value;
        } else {
            self.fields.push((name, value));
        }
    }

    pub fn get(&self, name: &str) -> Option<&WireValue> {
        self.fields
            .iter()
            .find(|(key, _)| key == name)
            .map(|(_, value)| value)
    }

    pub fn contains(&self, name: &str) -> bool {
        self.fields.iter().any(|(key, _)| key == name)
    }

    /// Remove a field by name, returning its value if it was present.
    pub fn remove(&mut self, name: &str) -> Option<WireValue> {
        if let Some(index) = self.fields.iter().position(|(key, _)| key == name) {
            Some(self.fields.remove(index).1)
        } else {
            None
        }
    }

    /// Names present in the map that are not one of `known`, in insertion order. This is
    /// the `extra="forbid"` set of the Python originals.
    pub fn extras(&self, known: &[&str]) -> Vec<String> {
        self.fields
            .iter()
            .filter(|(key, _)| !known.iter().any(|name| name == key))
            .map(|(key, _)| key.clone())
            .collect()
    }

    pub fn iter(&self) -> impl Iterator<Item = (&str, &WireValue)> {
        self.fields
            .iter()
            .map(|(name, value)| (name.as_str(), value))
    }

    pub fn len(&self) -> usize {
        self.fields.len()
    }

    pub fn is_empty(&self) -> bool {
        self.fields.is_empty()
    }
}

/// Why a text value is not a usable finite decimal.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DecimalParseError {
    /// Not a decimal lexeme (bad digits, punctuation or exponent).
    Invalid,
    /// A `NaN`/`Infinity` spelling, which is not a finite number.
    NonFinite,
}

/// An exact decimal carried as coefficient digits and a base-ten exponent, so the value
/// is `(-1)^negative * digits * 10^exponent`. No binary float is involved at any step.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecimalLexeme {
    negative: bool,
    digits: String,
    exponent: i32,
}

fn ascii_trim(text: &str) -> &str {
    text.trim_matches(|c: char| c.is_ascii_whitespace())
}

/// Parse digit characters, allowing a single underscore strictly between two digits.
fn digit_run(text: &str) -> Option<String> {
    let chars: Vec<char> = text.chars().collect();
    if chars.is_empty() {
        return None;
    }
    let mut out = String::with_capacity(chars.len());
    for (index, ch) in chars.iter().enumerate() {
        if *ch == '_' {
            if index == 0
                || index + 1 == chars.len()
                || !chars[index - 1].is_ascii_digit()
                || !chars[index + 1].is_ascii_digit()
            {
                return None;
            }
        } else if ch.is_ascii_digit() {
            out.push(*ch);
        } else {
            return None;
        }
    }
    Some(out)
}

/// Parse a signed integer exponent (underscores between digits are accepted, as in
/// Python's `Decimal`). The result saturates at `i32` bounds; extreme exponents are
/// rejected later by the range check rather than wrapping here.
fn parse_exponent(text: &str) -> Option<i32> {
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
        if value > i32::MAX as i64 {
            value = i32::MAX as i64;
        }
    }
    Some(if negative {
        -(value as i32)
    } else {
        value as i32
    })
}

fn is_non_finite_spelling(text: &str) -> bool {
    matches!(
        text,
        "nan"
            | "+nan"
            | "-nan"
            | "snan"
            | "+snan"
            | "-snan"
            | "inf"
            | "+inf"
            | "-inf"
            | "infinity"
            | "+infinity"
            | "-infinity"
    )
}

impl DecimalLexeme {
    /// Parse the decimal lexemes this edge accepts: surrounding ASCII whitespace, an
    /// optional sign, digits with underscores between them, an optional fraction and an
    /// optional exponent. Non-finite spellings are reported distinctly so the caller can
    /// emit the right stable code.
    pub fn parse(text: &str) -> Result<Self, DecimalParseError> {
        let trimmed = ascii_trim(text);
        if trimmed.is_empty() {
            return Err(DecimalParseError::Invalid);
        }
        if is_non_finite_spelling(&trimmed.to_ascii_lowercase()) {
            return Err(DecimalParseError::NonFinite);
        }
        let (negative, rest) = match trimmed.as_bytes().first() {
            Some(b'+') => (false, &trimmed[1..]),
            Some(b'-') => (true, &trimmed[1..]),
            _ => (false, trimmed),
        };
        if rest.is_empty() {
            return Err(DecimalParseError::Invalid);
        }

        // Split the exponent letter off before touching the mantissa.
        let exp_at = rest.find(['e', 'E']);
        let (mantissa, exponent) = match exp_at {
            Some(index) => {
                let exponent =
                    parse_exponent(&rest[index + 1..]).ok_or(DecimalParseError::Invalid)?;
                (&rest[..index], exponent)
            }
            None => (rest, 0),
        };

        let (whole, fraction) = match mantissa.find('.') {
            Some(index) => {
                let after = &mantissa[index + 1..];
                if after.contains('.') {
                    return Err(DecimalParseError::Invalid);
                }
                (&mantissa[..index], Some(after))
            }
            None => (mantissa, None),
        };

        let mut digits = match (whole.is_empty(), fraction) {
            (true, None) => return Err(DecimalParseError::Invalid),
            (true, Some(fraction)) => digit_run(fraction).ok_or(DecimalParseError::Invalid)?,
            (false, None) => digit_run(whole).ok_or(DecimalParseError::Invalid)?,
            (false, Some(fraction)) => {
                let whole = digit_run(whole).ok_or(DecimalParseError::Invalid)?;
                let fraction = digit_run(fraction).ok_or(DecimalParseError::Invalid)?;
                format!("{whole}{fraction}")
            }
        };

        let fraction_len = fraction.map_or(0, |part| part.len());
        let effective = (i64::from(exponent)).saturating_sub(fraction_len as i64);

        let normalized = digits.trim_start_matches('0');
        digits = if normalized.is_empty() {
            "0".to_string()
        } else {
            normalized.to_string()
        };

        Ok(Self {
            negative,
            digits,
            exponent: effective.clamp(i32::MIN as i64, i32::MAX as i64) as i32,
        })
    }

    /// A value is positive only when it is neither negative nor zero. Pydantic's
    /// `gt=0` check happens before cent quantisation, so this decides `greater_than`.
    pub fn is_positive(&self) -> bool {
        !self.negative && self.digits != "0"
    }

    /// The adjusted exponent: the power of ten of the leading digit. Used both for the
    /// scientific-notation threshold and for the `f64` overflow sentinel.
    fn adjusted(&self) -> i64 {
        self.digits.len() as i64 - 1 + i64::from(self.exponent)
    }

    /// True when a JSON floating literal of this magnitude would become infinity in a
    /// binary float (the Python decoder's `1e400` case). Exact text is kept regardless.
    pub fn overflows_f64(&self) -> bool {
        self.adjusted() > 308
    }

    /// The canonical text, matching Python `str(Decimal(...))`: fixed notation when the
    /// exponent is not positive and the adjusted exponent is at least -6, scientific
    /// notation otherwise. Trailing zeros (and therefore scale) are preserved.
    pub fn canonical(&self) -> String {
        let sign = if self.negative { "-" } else { "" };
        let adjusted = self.adjusted();
        if i64::from(self.exponent) <= 0 && adjusted >= -6 {
            if self.digits == "0" {
                return if self.exponent == 0 {
                    format!("{sign}0")
                } else {
                    format!(
                        "{sign}0.{}",
                        "0".repeat(self.exponent.unsigned_abs() as usize)
                    )
                };
            }
            let point = self.digits.len() as i64 + i64::from(self.exponent);
            if point <= 0 {
                format!(
                    "{sign}0.{}{}",
                    "0".repeat(point.unsigned_abs() as usize),
                    self.digits
                )
            } else if point >= self.digits.len() as i64 {
                format!(
                    "{sign}{}{}",
                    self.digits,
                    "0".repeat((point - self.digits.len() as i64) as usize)
                )
            } else {
                let point = point as usize;
                format!("{sign}{}.{}", &self.digits[..point], &self.digits[point..])
            }
        } else {
            let mantissa = if self.digits.len() == 1 {
                self.digits.clone()
            } else {
                format!("{}.{}", &self.digits[..1], &self.digits[1..])
            };
            let sign_exp = if adjusted >= 0 { "+" } else { "" };
            format!("{sign}{mantissa}E{sign_exp}{adjusted}")
        }
    }

    /// Round half-even to two fractional digits, as `Decimal.quantize(Decimal("0.01"))`
    /// does in Python's default context. `None` means the result would need more than
    /// 28 coefficient digits, which is the original's "outside the supported range".
    pub fn quantize_cents(&self) -> Option<Self> {
        let target = -2i64;
        let current = i64::from(self.exponent);
        let mut digits = self.digits.clone();

        if current > target {
            let pad = (current - target) as usize;
            if digits.len().saturating_add(pad) > 28 {
                return None;
            }
            digits.push_str(&"0".repeat(pad));
        } else if current < target {
            let drop = (target - current) as usize;
            let kept_len = digits.len().saturating_sub(drop);
            let kept = digits[..kept_len].to_string();
            let tail: String = if drop > digits.len() {
                format!("{}{}", "0".repeat(drop - digits.len()), digits)
            } else {
                digits[digits.len() - drop..].to_string()
            };
            let round_up = if tail > half_string(drop) {
                true
            } else if tail == half_string(drop) {
                // Banker's rounding: a tie rounds up only from an odd kept digit.
                kept.as_bytes()
                    .last()
                    .is_some_and(|digit| (digit - b'0') % 2 == 1)
            } else {
                false
            };
            let mut rounded = if kept.is_empty() {
                "0".to_string()
            } else {
                kept
            };
            if round_up {
                rounded = string_increment(&rounded);
            }
            digits = rounded;
        }

        let coefficient = digits.trim_start_matches('0');
        if !coefficient.is_empty() && coefficient.len() > 28 {
            return None;
        }
        Some(Self {
            negative: self.negative,
            digits,
            exponent: -2,
        })
    }
}

/// `"5"` followed by `count - 1` zeros: the half-way point for a dropped run of `count`
/// digits.
fn half_string(count: usize) -> String {
    let mut value = String::with_capacity(count);
    value.push('5');
    value.push_str(&"0".repeat(count.saturating_sub(1)));
    value
}

/// Add one to a non-negative decimal digit string, growing it on carry (`999` → `1000`).
fn string_increment(digits: &str) -> String {
    let mut bytes = digits.as_bytes().to_vec();
    let mut index = bytes.len();
    loop {
        if index == 0 {
            bytes.insert(0, b'1');
            break;
        }
        index -= 1;
        if bytes[index] == b'9' {
            bytes[index] = b'0';
        } else {
            bytes[index] += 1;
            break;
        }
    }
    String::from_utf8(bytes).unwrap_or_default()
}

/// A versioned wire envelope. The kind and integer version are separate fields so a
/// decoder can report which one it does not support instead of guessing from a string.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VersionedEnvelope<T> {
    pub kind: &'static str,
    pub version: u32,
    pub payload: T,
}

/// Why an envelope was refused before its payload was read.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EnvelopeError {
    UnsupportedKind {
        expected: &'static str,
        got: &'static str,
    },
    UnsupportedVersion {
        expected: u32,
        got: u32,
    },
}

impl<T> VersionedEnvelope<T> {
    /// Wrap a payload under one declared kind and version.
    pub fn new(kind: &'static str, version: u32, payload: T) -> Self {
        Self {
            kind,
            version,
            payload,
        }
    }

    /// The full versioned schema id, e.g. `ezchain.paper-order/1`.
    pub fn schema_id(&self) -> String {
        format!("{}/{}", self.kind, self.version)
    }

    /// Accept only the exact kind and version this decoder implements.
    pub fn decode(
        expected_kind: &'static str,
        expected_version: u32,
        kind: &'static str,
        version: u32,
        payload: T,
    ) -> Result<Self, EnvelopeError> {
        if kind != expected_kind {
            return Err(EnvelopeError::UnsupportedKind {
                expected: expected_kind,
                got: kind,
            });
        }
        if version != expected_version {
            return Err(EnvelopeError::UnsupportedVersion {
                expected: expected_version,
                got: version,
            });
        }
        Ok(Self::new(kind, version, payload))
    }
}

impl<T: Clone> VersionedEnvelope<T> {
    /// Encode the envelope as a field map with two reserved keys (`schema`, `version`)
    /// ahead of the payload fields.
    pub fn to_field_map<F>(&self, payload_fields: F) -> FieldMap
    where
        F: FnOnce(&T) -> FieldMap,
    {
        let mut map = FieldMap::with(vec![
            ("schema", WireValue::Str(self.schema_id())),
            ("version", WireValue::Int(self.version.to_string())),
        ]);
        for (name, value) in payload_fields(&self.payload).iter() {
            map.insert(name, value.clone());
        }
        map
    }
}

/// Pull the reserved envelope keys out of a field map, leaving the payload fields for a
/// record decoder. The schema id must be exactly `kind/version`.
pub fn split_envelope(
    map: &FieldMap,
    expected_kind: &'static str,
    expected_version: u32,
) -> Result<FieldMap, EnvelopeError> {
    let kind = match map.get("schema") {
        Some(WireValue::Str(value)) => value,
        _ => {
            return Err(EnvelopeError::UnsupportedKind {
                expected: expected_kind,
                got: "",
            })
        }
    };
    let expected = format!("{expected_kind}/{expected_version}");
    if kind.as_str() != expected.as_str() {
        return Err(EnvelopeError::UnsupportedKind {
            expected: expected_kind,
            got: "",
        });
    }
    let mut payload = FieldMap::new();
    for (name, value) in map.iter() {
        if name != "schema" && name != "version" {
            payload.insert(name, value.clone());
        }
    }
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn field_map_preserves_order_and_reports_extras() {
        let mut map = FieldMap::with(vec![("b", WireValue::Bool(true))]);
        map.insert("a", WireValue::Str("1".into()));
        map.insert("strategy_id", WireValue::Str("s".into()));
        assert_eq!(map.extras(&["a", "b"]), vec!["strategy_id".to_string()]);
        assert_eq!(map.get("a"), Some(&WireValue::Str("1".into())));
        assert_eq!(map.len(), 3);
    }

    #[test]
    fn decimal_parse_canonical_and_positivity() {
        let cases = [
            ("12.5", "12.5", true),
            ("00025.000", "25.000", true),
            ("+3", "3", true),
            ("2.500E1", "25.00", true),
            ("1e400", "1E+400", true),
            ("0.0000001", "1E-7", true),
            ("0.000001", "0.000001", true),
            ("1_000.50", "1000.50", true),
            (" 25 ", "25", true),
            ("0", "0", false),
            ("-2", "-2", false),
        ];
        for (text, canonical, positive) in cases {
            let value = DecimalLexeme::parse(text).expect(text);
            assert_eq!(value.canonical(), canonical, "canonical of {text:?}");
            assert_eq!(value.is_positive(), positive, "positivity of {text:?}");
        }
    }

    #[test]
    fn decimal_parse_rejects_invalid_and_non_finite() {
        assert_eq!(
            DecimalLexeme::parse("NaN"),
            Err(DecimalParseError::NonFinite)
        );
        assert_eq!(
            DecimalLexeme::parse("-Infinity"),
            Err(DecimalParseError::NonFinite)
        );
        for bad in ["", ".", "1.2.3", "not-a-number", "1__0", "_10", "10_"] {
            assert_eq!(
                DecimalLexeme::parse(bad),
                Err(DecimalParseError::Invalid),
                "{bad:?}"
            );
        }
    }

    #[test]
    fn quantize_cents_uses_half_even_and_a_28_digit_bound() {
        let cases = [
            ("25", "25.00"),
            ("25.555", "25.56"),
            ("2.345", "2.34"),
            ("25.005", "25.00"),
            ("25.015", "25.02"),
            ("25.000", "25.00"),
            ("2.5e1", "25.00"),
        ];
        for (input, expected) in cases {
            let value = DecimalLexeme::parse(input).expect(input);
            let quantized = value.quantize_cents().expect(input);
            assert_eq!(quantized.canonical(), expected, "quantize of {input:?}");
        }
        // 0.001 rounds down to zero (the caller then reports "must be > 0").
        let tiny = DecimalLexeme::parse("0.001")
            .unwrap()
            .quantize_cents()
            .unwrap();
        assert_eq!(tiny.canonical(), "0.00");
        // 1e25 fits at 28 coefficient digits; 1e26 needs 29 and is refused.
        assert!(DecimalLexeme::parse("1e25")
            .unwrap()
            .quantize_cents()
            .is_some());
        assert!(DecimalLexeme::parse("1e26")
            .unwrap()
            .quantize_cents()
            .is_none());
    }

    #[test]
    fn envelope_decodes_only_the_declared_kind_and_version() {
        let envelope = VersionedEnvelope::new("ezchain.paper-order", 1, "payload");
        assert_eq!(envelope.schema_id(), "ezchain.paper-order/1");
        assert_eq!(
            VersionedEnvelope::decode("ezchain.paper-order", 1, "ezchain.paper-order", 1, 7),
            Ok(VersionedEnvelope::new("ezchain.paper-order", 1, 7))
        );
        assert_eq!(
            VersionedEnvelope::decode("ezchain.paper-order", 1, "ezchain.other", 1, 7),
            Err(EnvelopeError::UnsupportedKind {
                expected: "ezchain.paper-order",
                got: "ezchain.other",
            })
        );
        assert_eq!(
            VersionedEnvelope::decode("ezchain.paper-order", 1, "ezchain.paper-order", 2, 7),
            Err(EnvelopeError::UnsupportedVersion {
                expected: 1,
                got: 2,
            })
        );
    }

    #[test]
    fn envelope_field_map_round_trip() {
        let envelope = VersionedEnvelope::new("ezchain.paper-order", 1, "body".to_string());
        let map = envelope.to_field_map(|payload| {
            FieldMap::with(vec![("payload", WireValue::Str(payload.clone()))])
        });
        assert_eq!(
            map.get("schema"),
            Some(&WireValue::Str("ezchain.paper-order/1".into()))
        );
        assert_eq!(map.get("version"), Some(&WireValue::Int("1".into())));
        let payload = split_envelope(&map, "ezchain.paper-order", 1).unwrap();
        assert_eq!(payload.get("payload"), Some(&WireValue::Str("body".into())));
        assert_eq!(payload.len(), 1);
    }
}
