//! Typed wire record for the manual paper-order request (`PaperOrderIn`).
//!
//! The Python schema in `apps/api/src/ezchain_api/schemas.py` is the oracle and the
//! published field set in `tests/contract/snapshots/openapi.json` is the contract. This
//! module ports every validation rule and every recorded error case of that schema:
//! `extra="forbid"`, field lengths, the `buy`/`sell` literal, cent quantisation with
//! half-even rounding and a 28-digit bound, the finite-guards on both money fields, the
//! ASCII/control rules on the idempotency key, and the sell-only model validator.
//!
//! Money (`amount_usd`, `quantity`) is carried as a validated decimal *lexeme* — text,
//! never a float. Validation errors carry the stable Pydantic `type` code and field path
//! so the RPC boundary can reproduce the Python 422 envelope exactly.
//!
//! No serde, no I/O, no keys, no money arithmetic beyond exact decimal rounding.

use crate::wire::{
    code, split_envelope, DecimalLexeme, DecimalParseError, EnvelopeError, FieldMap,
    ValidationError, VersionedEnvelope, WireValue,
};

/// The versioned schema kind and version this decoder implements. The full id is
/// `ezchain.paper-order/1`.
pub const KIND: &str = "ezchain.paper-order";
pub const VERSION: u32 = 1;

/// The published field set, in declaration order. `additionalProperties: false` in the
/// contract snapshot means anything outside this set is refused, not dropped.
const FIELDS: [&str; 10] = [
    "chain",
    "token_address",
    "side",
    "amount_usd",
    "idempotency_key",
    "quantity",
    "reduce_only",
    "risk_profile",
    "trade_risk_profile",
    "profile",
];
#[cfg(test)]
const REQUIRED: [&str; 4] = ["chain", "token_address", "side", "idempotency_key"];

/// The two sides of a manual order. There is no third state: an unknown or misspelt
/// side is refused, never folded into one of these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Side {
    Buy,
    Sell,
}

impl Side {
    pub fn as_str(self) -> &'static str {
        match self {
            Side::Buy => "buy",
            Side::Sell => "sell",
        }
    }
}

/// A validated manual paper-order request. Both money fields are canonical decimal
/// lexemes (text); `amount_usd` is quantised to cents, `quantity` keeps its exact scale.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PaperOrderIn {
    pub chain: String,
    pub token_address: String,
    pub side: Side,
    pub amount_usd: Option<String>,
    pub idempotency_key: String,
    pub quantity: Option<String>,
    pub reduce_only: bool,
    pub risk_profile: Option<String>,
    pub trade_risk_profile: Option<String>,
    pub profile: Option<String>,
}

/// Why a versioned field map could not be decoded.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecodeError {
    Envelope(EnvelopeError),
    Validation(Vec<ValidationError>),
}

fn string_field(
    map: &FieldMap,
    field: &str,
    min: usize,
    max: usize,
    errors: &mut Vec<ValidationError>,
) -> Option<String> {
    match map.get(field) {
        None => {
            errors.push(ValidationError::field(
                code::MISSING,
                field,
                "Field required",
            ));
            None
        }
        Some(WireValue::Str(value)) => {
            let length = value.chars().count();
            if length < min {
                errors.push(ValidationError::field(
                    code::STRING_TOO_SHORT,
                    field,
                    format!("String should have at least {min} characters"),
                ));
                None
            } else if length > max {
                errors.push(ValidationError::field(
                    code::STRING_TOO_LONG,
                    field,
                    format!("String should have at most {max} characters"),
                ));
                None
            } else {
                Some(value.clone())
            }
        }
        Some(_) => {
            errors.push(ValidationError::field(
                code::STRING_TYPE,
                field,
                "Input should be a valid string",
            ));
            None
        }
    }
}

fn optional_string_field(
    map: &FieldMap,
    field: &str,
    errors: &mut Vec<ValidationError>,
) -> Option<String> {
    match map.get(field) {
        None | Some(WireValue::Null) => None,
        Some(WireValue::Str(value)) => Some(value.clone()),
        Some(_) => {
            errors.push(ValidationError::field(
                code::STRING_TYPE,
                field,
                "Input should be a valid string",
            ));
            None
        }
    }
}

fn side_field(map: &FieldMap, errors: &mut Vec<ValidationError>) -> Option<Side> {
    match map.get("side") {
        None => {
            errors.push(ValidationError::field(
                code::MISSING,
                "side",
                "Field required",
            ));
            None
        }
        Some(WireValue::Str(value)) if value == "buy" => Some(Side::Buy),
        Some(WireValue::Str(value)) if value == "sell" => Some(Side::Sell),
        Some(_) => {
            errors.push(ValidationError::field(
                code::LITERAL_ERROR,
                "side",
                "Input should be 'buy' or 'sell'",
            ));
            None
        }
    }
}

fn decimal_field(
    map: &FieldMap,
    field: &str,
    to_cents: bool,
    errors: &mut Vec<ValidationError>,
) -> Option<DecimalLexeme> {
    let value = match map.get(field) {
        None | Some(WireValue::Null) => return None,
        Some(value) => value,
    };

    let lexeme = match value {
        WireValue::Str(text) | WireValue::Int(text) => match DecimalLexeme::parse(text) {
            Ok(lexeme) => lexeme,
            Err(DecimalParseError::NonFinite) => {
                errors.push(ValidationError::field(
                    code::FINITE_NUMBER,
                    field,
                    "Input should be a finite number",
                ));
                return None;
            }
            Err(DecimalParseError::Invalid) => {
                errors.push(ValidationError::field(
                    code::DECIMAL_PARSING,
                    field,
                    "Input should be a valid decimal",
                ));
                return None;
            }
        },
        WireValue::Float(text) => match DecimalLexeme::parse(text) {
            Ok(lexeme) if !lexeme.overflows_f64() => lexeme,
            _ => {
                // A float that is NaN/Infinity or overflows is refused by the Python
                // `finite_*` before-validators with a field-specific ValueError.
                errors.push(ValidationError::field(
                    code::VALUE_ERROR,
                    field,
                    format!("Value error, {field} must be finite"),
                ));
                return None;
            }
        },
        WireValue::Bool(_) => {
            errors.push(ValidationError::field(
                code::DECIMAL_TYPE,
                field,
                "Decimal input should be an integer, float, string or Decimal object",
            ));
            return None;
        }
        WireValue::Null => unreachable!("null was handled before the match"),
    };

    // Pydantic's `gt=0` runs before the after-validator that quantises to cents.
    if !lexeme.is_positive() {
        errors.push(ValidationError::field(
            code::GREATER_THAN,
            field,
            "Input should be greater than 0",
        ));
        return None;
    }

    if !to_cents {
        return Some(lexeme);
    }
    let quantized = match lexeme.quantize_cents() {
        Some(value) => value,
        None => {
            errors.push(ValidationError::field(
                code::VALUE_ERROR,
                field,
                format!("Value error, {field} is outside the supported range"),
            ));
            return None;
        }
    };
    if !quantized.is_positive() {
        errors.push(ValidationError::field(
            code::VALUE_ERROR,
            field,
            format!("Value error, {field} must be > 0"),
        ));
        return None;
    }
    Some(quantized)
}

fn bool_field(map: &FieldMap, field: &str, errors: &mut Vec<ValidationError>) -> Option<bool> {
    match map.get(field) {
        None => Some(false),
        Some(WireValue::Bool(value)) => Some(*value),
        Some(WireValue::Str(value)) => match value.to_ascii_lowercase().as_str() {
            "true" | "1" | "on" | "yes" => Some(true),
            "false" | "0" | "off" | "no" => Some(false),
            _ => {
                errors.push(ValidationError::field(
                    code::BOOL_PARSING,
                    field,
                    "Input should be a valid boolean, unable to interpret input",
                ));
                None
            }
        },
        Some(WireValue::Int(value)) => match value.as_str() {
            "1" => Some(true),
            "0" => Some(false),
            _ => {
                errors.push(ValidationError::field(
                    code::BOOL_PARSING,
                    field,
                    "Input should be a valid boolean, unable to interpret input",
                ));
                None
            }
        },
        Some(_) => {
            errors.push(ValidationError::field(
                code::BOOL_TYPE,
                field,
                "Input should be a valid boolean",
            ));
            None
        }
    }
}

impl PaperOrderIn {
    /// Validate a field map into the typed record. Field errors are collected in the
    /// Python declaration order, unknown fields are reported last, and the model-level
    /// validator runs only when every field was valid (as in Pydantic).
    pub fn from_field_map(map: &FieldMap) -> Result<Self, Vec<ValidationError>> {
        let mut errors = Vec::new();

        let chain = string_field(map, "chain", 2, 40, &mut errors);
        let token_address = string_field(map, "token_address", 8, 160, &mut errors);
        let side = side_field(map, &mut errors);
        let amount_usd = decimal_field(map, "amount_usd", true, &mut errors);

        let idempotency_key = string_field(map, "idempotency_key", 8, 120, &mut errors);
        if let Some(key) = &idempotency_key {
            if !key.is_ascii() {
                errors.push(ValidationError::field(
                    code::VALUE_ERROR,
                    "idempotency_key",
                    "Value error, idempotency_key must be ASCII",
                ));
            } else if key.chars().any(|c| (c as u32) < 32 || c == '\u{7f}') {
                errors.push(ValidationError::field(
                    code::VALUE_ERROR,
                    "idempotency_key",
                    "Value error, idempotency_key must not contain control characters",
                ));
            }
        }

        let quantity = decimal_field(map, "quantity", false, &mut errors);
        let reduce_only = bool_field(map, "reduce_only", &mut errors);
        let risk_profile = optional_string_field(map, "risk_profile", &mut errors);
        let trade_risk_profile = optional_string_field(map, "trade_risk_profile", &mut errors);
        let profile = optional_string_field(map, "profile", &mut errors);

        for extra in map.extras(&FIELDS) {
            errors.push(ValidationError::field(
                code::EXTRA_FORBIDDEN,
                &extra,
                "Extra inputs are not permitted",
            ));
        }

        if errors.is_empty() {
            let side = side.expect("side validated");
            let reduce_only = reduce_only.expect("reduce_only validated");
            if side == Side::Buy && (reduce_only || quantity.is_some()) {
                errors.push(ValidationError::model(
                    code::VALUE_ERROR,
                    "Value error, reduce_only and quantity are only valid for a sell",
                ));
            } else if side == Side::Sell && amount_usd.is_none() && quantity.is_none() {
                errors.push(ValidationError::model(
                    code::VALUE_ERROR,
                    "Value error, a sell needs amount_usd or quantity",
                ));
            }
        }

        if !errors.is_empty() {
            return Err(errors);
        }

        Ok(Self {
            chain: chain.expect("chain validated"),
            token_address: token_address.expect("token_address validated"),
            side: side.expect("side validated"),
            amount_usd: amount_usd.map(|value| value.canonical()),
            idempotency_key: idempotency_key.expect("idempotency_key validated"),
            quantity: quantity.map(|value| value.canonical()),
            reduce_only: reduce_only.expect("reduce_only validated"),
            risk_profile,
            trade_risk_profile,
            profile,
        })
    }

    /// Explicit conversion back to a field map. Money is emitted as string lexemes, the
    /// same representation the Python response uses (`Decimal` is never a JSON number).
    pub fn to_field_map(&self) -> FieldMap {
        let money = |value: &Option<String>| match value {
            Some(text) => WireValue::Str(text.clone()),
            None => WireValue::Null,
        };
        FieldMap::with(vec![
            ("chain", WireValue::Str(self.chain.clone())),
            ("token_address", WireValue::Str(self.token_address.clone())),
            ("side", WireValue::Str(self.side.as_str().to_string())),
            ("amount_usd", money(&self.amount_usd)),
            (
                "idempotency_key",
                WireValue::Str(self.idempotency_key.clone()),
            ),
            ("quantity", money(&self.quantity)),
            ("reduce_only", WireValue::Bool(self.reduce_only)),
            (
                "risk_profile",
                self.risk_profile
                    .as_ref()
                    .map_or(WireValue::Null, |v| WireValue::Str(v.clone())),
            ),
            (
                "trade_risk_profile",
                self.trade_risk_profile
                    .as_ref()
                    .map_or(WireValue::Null, |v| WireValue::Str(v.clone())),
            ),
            (
                "profile",
                self.profile
                    .as_ref()
                    .map_or(WireValue::Null, |v| WireValue::Str(v.clone())),
            ),
        ])
    }

    /// Wrap the record in its versioned envelope.
    pub fn to_envelope(&self) -> VersionedEnvelope<PaperOrderIn> {
        VersionedEnvelope::new(KIND, VERSION, self.clone())
    }

    /// Decode a versioned field map: check `schema`/`version` before validating fields.
    pub fn decode_envelope(map: &FieldMap) -> Result<VersionedEnvelope<PaperOrderIn>, DecodeError> {
        let payload = split_envelope(map, KIND, VERSION).map_err(DecodeError::Envelope)?;
        let record = PaperOrderIn::from_field_map(&payload).map_err(DecodeError::Validation)?;
        Ok(VersionedEnvelope::new(KIND, VERSION, record))
    }
}

/// Encode a record as a versioned field map with `schema` and `version` keys.
pub fn encode_envelope(record: PaperOrderIn) -> FieldMap {
    record
        .to_envelope()
        .to_field_map(|order| order.to_field_map())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn base() -> FieldMap {
        FieldMap::with(vec![
            ("chain", WireValue::Str("solana".into())),
            ("token_address", WireValue::Str("abcdefgh1234".into())),
            ("side", WireValue::Str("buy".into())),
            ("amount_usd", WireValue::Str("25".into())),
            ("idempotency_key", WireValue::Str("valid-key-123".into())),
        ])
    }

    fn errors(map: &FieldMap) -> Vec<ValidationError> {
        PaperOrderIn::from_field_map(map).expect_err("record should be refused")
    }

    /// Assert the full ordered error list: (code, field path, message). An empty field
    /// path denotes a record-level error.
    fn assert_errors(map: &FieldMap, expected: &[(&str, &[&str], &str)]) {
        let actual = errors(map);
        let simplified: Vec<(&str, Vec<&str>, &str)> = actual
            .iter()
            .map(|error| {
                (
                    error.code,
                    error.loc.iter().map(String::as_str).collect(),
                    error.message.as_str(),
                )
            })
            .collect();
        let wanted: Vec<(&str, Vec<&str>, &str)> = expected
            .iter()
            .map(|(code, loc, message)| (*code, loc.to_vec(), *message))
            .collect();
        assert_eq!(simplified, wanted);
    }

    fn assert_ok(map: &FieldMap) -> PaperOrderIn {
        PaperOrderIn::from_field_map(map).expect("record should validate")
    }

    // ---- recorded valid cases (the oracle's model_dump_json output) ----

    #[test]
    fn parity_valid_buy_and_sell_cases() {
        let minimal = assert_ok(&base());
        assert_eq!(minimal.amount_usd.as_deref(), Some("25.00"));
        assert_eq!(minimal.quantity, None);
        assert!(!minimal.reduce_only);

        let dec = assert_ok(&{
            let mut m = base();
            m.insert("amount_usd", WireValue::Str("25.555".into()));
            m
        });
        assert_eq!(dec.amount_usd.as_deref(), Some("25.56"));
        assert_eq!(
            assert_ok(&{
                let mut m = base();
                m.insert("amount_usd", WireValue::Int("25".into()));
                m
            })
            .amount_usd
            .as_deref(),
            Some("25.00")
        );
        assert_eq!(
            assert_ok(&{
                let mut m = base();
                m.insert("amount_usd", WireValue::Float("25.5".into()));
                m
            })
            .amount_usd
            .as_deref(),
            Some("25.50")
        );

        let mut no_amount = base();
        no_amount.remove("amount_usd");
        assert_eq!(assert_ok(&no_amount).amount_usd, None);

        let mut profiles = base();
        profiles.insert("risk_profile", WireValue::Str("low".into()));
        profiles.insert("trade_risk_profile", WireValue::Str("mid".into()));
        profiles.insert("profile", WireValue::Str("high".into()));
        let profiles = assert_ok(&profiles);
        assert_eq!(profiles.risk_profile.as_deref(), Some("low"));
        assert_eq!(profiles.trade_risk_profile.as_deref(), Some("mid"));
        assert_eq!(profiles.profile.as_deref(), Some("high"));

        let mut sell = base();
        sell.insert("side", WireValue::Str("sell".into()));
        assert_eq!(assert_ok(&sell).side, Side::Sell);

        let mut qty = base();
        qty.insert("side", WireValue::Str("sell".into()));
        qty.insert("amount_usd", WireValue::Null);
        qty.insert("quantity", WireValue::Str("12.5".into()));
        assert_eq!(assert_ok(&qty).quantity.as_deref(), Some("12.5"));

        let mut qty_float = base();
        qty_float.insert("side", WireValue::Str("sell".into()));
        qty_float.insert("amount_usd", WireValue::Null);
        qty_float.insert("quantity", WireValue::Float("3.5".into()));
        assert_eq!(assert_ok(&qty_float).quantity.as_deref(), Some("3.5"));

        let mut reduce = base();
        reduce.insert("side", WireValue::Str("sell".into()));
        reduce.insert("reduce_only", WireValue::Bool(true));
        assert!(assert_ok(&reduce).reduce_only);
    }

    // ---- extra fields: 422 extra_forbidden, named in loc ----

    #[test]
    fn parity_extra_fields_are_refused_not_dropped() {
        for field in ["strategy_id", "actor", "chainn"] {
            let mut map = base();
            map.insert(field, WireValue::Str("x".into()));
            assert_errors(
                &map,
                &[(
                    code::EXTRA_FORBIDDEN,
                    &[field],
                    "Extra inputs are not permitted",
                )],
            );
        }
        let mut both = base();
        both.insert("strategy_id", WireValue::Str("s".into()));
        both.insert("actor", WireValue::Str("x".into()));
        assert_errors(
            &both,
            &[
                (
                    code::EXTRA_FORBIDDEN,
                    &["strategy_id"],
                    "Extra inputs are not permitted",
                ),
                (
                    code::EXTRA_FORBIDDEN,
                    &["actor"],
                    "Extra inputs are not permitted",
                ),
            ],
        );
    }

    // ---- required strings and length bounds ----

    #[test]
    fn parity_required_and_bounded_string_fields() {
        let mut missing = base();
        missing.remove("chain");
        assert_errors(&missing, &[(code::MISSING, &["chain"], "Field required")]);

        for (value, code, message) in [
            (
                "s",
                code::STRING_TOO_SHORT,
                "String should have at least 2 characters",
            ),
            (
                "",
                code::STRING_TOO_SHORT,
                "String should have at least 2 characters",
            ),
            (
                &"c".repeat(41),
                code::STRING_TOO_LONG,
                "String should have at most 40 characters",
            ),
        ] {
            let mut map = base();
            map.insert("chain", WireValue::Str(value.to_string()));
            assert_errors(&map, &[(code, &["chain"], message)]);
        }
        let mut not_string = base();
        not_string.insert("chain", WireValue::Int("5".into()));
        assert_errors(
            &not_string,
            &[(
                code::STRING_TYPE,
                &["chain"],
                "Input should be a valid string",
            )],
        );

        let mut short_token = base();
        short_token.insert("token_address", WireValue::Str("short".into()));
        assert_errors(
            &short_token,
            &[(
                code::STRING_TOO_SHORT,
                &["token_address"],
                "String should have at least 8 characters",
            )],
        );
        let mut long_token = base();
        long_token.insert("token_address", WireValue::Str("a".repeat(161)));
        assert_errors(
            &long_token,
            &[(
                code::STRING_TOO_LONG,
                &["token_address"],
                "String should have at most 160 characters",
            )],
        );
        let mut missing_token = base();
        missing_token.remove("token_address");
        assert_errors(
            &missing_token,
            &[(code::MISSING, &["token_address"], "Field required")],
        );
    }

    #[test]
    fn parity_idempotency_key_rules() {
        let mut missing = base();
        missing.remove("idempotency_key");
        assert_errors(
            &missing,
            &[(code::MISSING, &["idempotency_key"], "Field required")],
        );

        let mut short = base();
        short.insert("idempotency_key", WireValue::Str("short".into()));
        assert_errors(
            &short,
            &[(
                code::STRING_TOO_SHORT,
                &["idempotency_key"],
                "String should have at least 8 characters",
            )],
        );
        let mut long = base();
        long.insert("idempotency_key", WireValue::Str("k".repeat(121)));
        assert_errors(
            &long,
            &[(
                code::STRING_TOO_LONG,
                &["idempotency_key"],
                "String should have at most 120 characters",
            )],
        );

        for value in ["idem-ünicode-123", "日本語テキスト123"] {
            let mut map = base();
            map.insert("idempotency_key", WireValue::Str(value.into()));
            assert_errors(
                &map,
                &[(
                    code::VALUE_ERROR,
                    &["idempotency_key"],
                    "Value error, idempotency_key must be ASCII",
                )],
            );
        }
        for value in [
            "key-with-\u{1}-control",
            "key-with-\u{7f}-del",
            "key\twith\ttab",
        ] {
            let mut map = base();
            map.insert("idempotency_key", WireValue::Str(value.into()));
            assert_errors(
                &map,
                &[(
                    code::VALUE_ERROR,
                    &["idempotency_key"],
                    "Value error, idempotency_key must not contain control characters",
                )],
            );
        }
        // Space is ASCII and not a control byte, so it is accepted.
        let mut space = base();
        space.insert("idempotency_key", WireValue::Str("key with space".into()));
        assert_ok(&space);
    }

    #[test]
    fn parity_side_literal() {
        let mut missing = base();
        missing.remove("side");
        assert_errors(&missing, &[(code::MISSING, &["side"], "Field required")]);
        for value in ["hold", "BUY", ""] {
            let mut map = base();
            map.insert("side", WireValue::Str(value.into()));
            assert_errors(
                &map,
                &[(
                    code::LITERAL_ERROR,
                    &["side"],
                    "Input should be 'buy' or 'sell'",
                )],
            );
        }
        let mut not_string = base();
        not_string.insert("side", WireValue::Int("1".into()));
        assert_errors(
            &not_string,
            &[(
                code::LITERAL_ERROR,
                &["side"],
                "Input should be 'buy' or 'sell'",
            )],
        );
    }

    // ---- amount_usd: the money path's validation rules ----

    #[test]
    fn parity_amount_rejects_non_positive_and_non_finite() {
        for text in ["0", "-1", "0.001"] {
            let mut map = base();
            map.insert("amount_usd", WireValue::Str(text.into()));
            let expected = if text == "0.001" {
                (code::VALUE_ERROR, "Value error, amount_usd must be > 0")
            } else {
                (code::GREATER_THAN, "Input should be greater than 0")
            };
            assert_errors(&map, &[(expected.0, &["amount_usd"], expected.1)]);
        }
        for (value, code, message) in [
            (
                WireValue::Str("NaN".into()),
                code::FINITE_NUMBER,
                "Input should be a finite number",
            ),
            (
                WireValue::Str("Infinity".into()),
                code::FINITE_NUMBER,
                "Input should be a finite number",
            ),
            (
                WireValue::Str("not-a-number".into()),
                code::DECIMAL_PARSING,
                "Input should be a valid decimal",
            ),
            (
                WireValue::Str("1e400".into()),
                code::VALUE_ERROR,
                "Value error, amount_usd is outside the supported range",
            ),
            (
                WireValue::Int("100000000000000000000000000".into()),
                code::VALUE_ERROR,
                "Value error, amount_usd is outside the supported range",
            ),
            (
                WireValue::Bool(true),
                code::DECIMAL_TYPE,
                "Decimal input should be an integer, float, string or Decimal object",
            ),
            (
                WireValue::Float("inf".into()),
                code::VALUE_ERROR,
                "Value error, amount_usd must be finite",
            ),
            (
                WireValue::Float("NaN".into()),
                code::VALUE_ERROR,
                "Value error, amount_usd must be finite",
            ),
            (
                WireValue::Float("1e400".into()),
                code::VALUE_ERROR,
                "Value error, amount_usd must be finite",
            ),
        ] {
            let mut map = base();
            map.insert("amount_usd", value);
            assert_errors(&map, &[(code, &["amount_usd"], message)]);
        }
    }

    #[test]
    fn parity_amount_half_even_and_accepted_lexemes() {
        for (text, expected) in [
            ("2.345", "2.34"),
            ("25.005", "25.00"),
            ("25.015", "25.02"),
            ("2.5e1", "25.00"),
            ("+25", "25.00"),
            ("00025.000", "25.00"),
            ("1_000.50", "1000.50"),
            (" 25 ", "25.00"),
            ("1e25", "10000000000000000000000000.00"),
        ] {
            let mut map = base();
            map.insert("amount_usd", WireValue::Str(text.into()));
            assert_eq!(
                assert_ok(&map).amount_usd.as_deref(),
                Some(expected),
                "{text}"
            );
        }
    }

    // ---- quantity: finite, positive, canonical text, no cent quantisation ----

    #[test]
    fn parity_quantity_rules() {
        for (value, code, message) in [
            (
                WireValue::Str("0".into()),
                code::GREATER_THAN,
                "Input should be greater than 0",
            ),
            (
                WireValue::Str("-2".into()),
                code::GREATER_THAN,
                "Input should be greater than 0",
            ),
            (
                WireValue::Str("NaN".into()),
                code::FINITE_NUMBER,
                "Input should be a finite number",
            ),
            (
                WireValue::Str("Infinity".into()),
                code::FINITE_NUMBER,
                "Input should be a finite number",
            ),
            (
                WireValue::Str("abc".into()),
                code::DECIMAL_PARSING,
                "Input should be a valid decimal",
            ),
            (
                WireValue::Bool(true),
                code::DECIMAL_TYPE,
                "Decimal input should be an integer, float, string or Decimal object",
            ),
            (
                WireValue::Float("inf".into()),
                code::VALUE_ERROR,
                "Value error, quantity must be finite",
            ),
            (
                WireValue::Float("NaN".into()),
                code::VALUE_ERROR,
                "Value error, quantity must be finite",
            ),
        ] {
            let mut map = base();
            map.insert("side", WireValue::Str("sell".into()));
            map.insert("amount_usd", WireValue::Null);
            map.insert("quantity", value);
            assert_errors(&map, &[(code, &["quantity"], message)]);
        }
        // Unlike amount_usd, quantity has no cent/range quantisation: 1e400 is finite.
        for (text, expected) in [
            ("12.5", "12.5"),
            ("12.50", "12.50"),
            ("00012.5", "12.5"),
            ("2.500E1", "25.00"),
            ("0.0000001", "1E-7"),
            ("0.000001", "0.000001"),
            ("1.2300", "1.2300"),
            ("1e-3", "0.001"),
            ("+3", "3"),
            ("1e400", "1E+400"),
        ] {
            let mut map = base();
            map.insert("side", WireValue::Str("sell".into()));
            map.insert("amount_usd", WireValue::Null);
            map.insert("quantity", WireValue::Str(text.into()));
            assert_eq!(
                assert_ok(&map).quantity.as_deref(),
                Some(expected),
                "{text}"
            );
        }
    }

    // ---- the sell-only model validator ----

    #[test]
    fn parity_sell_only_fields() {
        // 1 = reduce_only, 2 = quantity, 3 = both.
        for which in [1u8, 2, 3] {
            let mut map = base();
            if which == 1 || which == 3 {
                map.insert("reduce_only", WireValue::Bool(true));
            }
            if which == 2 || which == 3 {
                map.insert("quantity", WireValue::Str("12.5".into()));
            }
            assert_errors(
                &map,
                &[(
                    code::VALUE_ERROR,
                    &[],
                    "Value error, reduce_only and quantity are only valid for a sell",
                )],
            );
        }
        let mut no_size = base();
        no_size.insert("side", WireValue::Str("sell".into()));
        no_size.insert("amount_usd", WireValue::Null);
        assert_errors(
            &no_size,
            &[(
                code::VALUE_ERROR,
                &[],
                "Value error, a sell needs amount_usd or quantity",
            )],
        );

        // A sell with both amount and quantity is allowed (the service bounds the sell).
        let mut both = base();
        both.insert("side", WireValue::Str("sell".into()));
        both.insert("quantity", WireValue::Str("5".into()));
        let both = assert_ok(&both);
        assert_eq!(both.amount_usd.as_deref(), Some("25.00"));
        assert_eq!(both.quantity.as_deref(), Some("5"));
    }

    #[test]
    fn parity_bool_coercion_and_rejection() {
        for (value, expected) in [
            (WireValue::Str("yes".into()), true),
            (WireValue::Str("TRUE".into()), true),
            (WireValue::Str("no".into()), false),
            (WireValue::Int("1".into()), true),
            (WireValue::Int("0".into()), false),
        ] {
            let mut map = base();
            map.insert("side", WireValue::Str("sell".into()));
            map.insert("reduce_only", value);
            assert_eq!(assert_ok(&map).reduce_only, expected);
        }
        for value in [
            WireValue::Str("maybe".into()),
            WireValue::Str("2".into()),
            WireValue::Int("2".into()),
        ] {
            let mut map = base();
            map.insert("reduce_only", value);
            assert_errors(
                &map,
                &[(
                    code::BOOL_PARSING,
                    &["reduce_only"],
                    "Input should be a valid boolean, unable to interpret input",
                )],
            );
        }
        for value in [WireValue::Float("1.5".into()), WireValue::Null] {
            let mut map = base();
            map.insert("reduce_only", value);
            assert_errors(
                &map,
                &[(
                    code::BOOL_TYPE,
                    &["reduce_only"],
                    "Input should be a valid boolean",
                )],
            );
        }
    }

    #[test]
    fn parity_multi_error_order_and_model_validator_skip() {
        let mut multi = base();
        multi.insert("chain", WireValue::Str("s".into()));
        multi.insert("side", WireValue::Str("hold".into()));
        multi.insert("amount_usd", WireValue::Str("0".into()));
        assert_errors(
            &multi,
            &[
                (
                    code::STRING_TOO_SHORT,
                    &["chain"],
                    "String should have at least 2 characters",
                ),
                (
                    code::LITERAL_ERROR,
                    &["side"],
                    "Input should be 'buy' or 'sell'",
                ),
                (
                    code::GREATER_THAN,
                    &["amount_usd"],
                    "Input should be greater than 0",
                ),
            ],
        );

        let mut extra_plus_field = base();
        extra_plus_field.insert("chain", WireValue::Str("s".into()));
        extra_plus_field.insert("strategy_id", WireValue::Str("x".into()));
        assert_errors(
            &extra_plus_field,
            &[
                (
                    code::STRING_TOO_SHORT,
                    &["chain"],
                    "String should have at least 2 characters",
                ),
                (
                    code::EXTRA_FORBIDDEN,
                    &["strategy_id"],
                    "Extra inputs are not permitted",
                ),
            ],
        );

        let mut model_skipped = base();
        model_skipped.insert("chain", WireValue::Str("s".into()));
        model_skipped.insert("reduce_only", WireValue::Bool(true));
        // Only the field error is reported; the contradictory-buy model error is skipped.
        assert_errors(
            &model_skipped,
            &[(
                code::STRING_TOO_SHORT,
                &["chain"],
                "String should have at least 2 characters",
            )],
        );

        let mut missing_two = base();
        missing_two.remove("chain");
        missing_two.remove("side");
        assert_errors(
            &missing_two,
            &[
                (code::MISSING, &["chain"], "Field required"),
                (code::MISSING, &["side"], "Field required"),
            ],
        );
    }

    // ---- field map conversion, contract snapshot shape, and versioned envelope ----

    #[test]
    fn field_map_round_trip_preserves_the_published_shape() {
        let order = assert_ok(&base());
        let map = order.to_field_map();
        let names: Vec<&str> = map.iter().map(|(name, _)| name).collect();
        assert_eq!(names, FIELDS);
        for required in REQUIRED {
            assert!(map.get(required).is_some());
        }
        assert!(map.extras(&FIELDS).is_empty());
        assert_eq!(PaperOrderIn::from_field_map(&map), Ok(order));

        let mut with_profiles = base();
        with_profiles.insert("risk_profile", WireValue::Str("low".into()));
        with_profiles.insert("reduce_only", WireValue::Bool(true));
        with_profiles.insert("side", WireValue::Str("sell".into()));
        let order = assert_ok(&with_profiles);
        assert_eq!(
            PaperOrderIn::from_field_map(&order.to_field_map()),
            Ok(order)
        );
    }

    #[test]
    fn envelope_round_trip_and_version_rejection() {
        let order = assert_ok(&base());
        let map = encode_envelope(order.clone());
        assert_eq!(
            map.get("schema"),
            Some(&WireValue::Str("ezchain.paper-order/1".into()))
        );
        assert_eq!(map.get("version"), Some(&WireValue::Int("1".into())));
        let decoded = PaperOrderIn::decode_envelope(&map).expect("envelope decodes");
        assert_eq!(decoded.schema_id(), "ezchain.paper-order/1");
        assert_eq!(decoded.payload, order);

        let mut wrong_version = map.clone();
        wrong_version.insert("schema", WireValue::Str("ezchain.paper-order/2".into()));
        assert!(matches!(
            PaperOrderIn::decode_envelope(&wrong_version),
            Err(DecodeError::Envelope(EnvelopeError::UnsupportedKind { .. }))
        ));

        let mut wrong_kind = map;
        wrong_kind.insert("schema", WireValue::Str("ezchain.other/1".into()));
        assert!(matches!(
            PaperOrderIn::decode_envelope(&wrong_kind),
            Err(DecodeError::Envelope(EnvelopeError::UnsupportedKind { .. }))
        ));

        // An envelope with a valid header but an invalid payload reports validation errors.
        let mut invalid_payload = encode_envelope(order);
        invalid_payload.insert("idempotency_key", WireValue::Str("short".into()));
        assert!(matches!(
            PaperOrderIn::decode_envelope(&invalid_payload),
            Err(DecodeError::Validation(_))
        ));
    }
}
