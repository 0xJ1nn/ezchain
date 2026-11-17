//! Parity port of the pure parameter-validation helpers of `ezchain_core.strategy`.
//!
//! The Python original is the behavioural oracle: `_interval`, `_lookback`,
//! `_reject_unknown`, `validate_params` and `interval_minutes` are deterministic and move no
//! money. Every branch, error code and message is recorded in
//! `tests/strategy_params_parity.rs`; the Python sources are
//! `packages/core-py/src/ezchain_core/strategy.py` (lines 27–113 and 176–290) and its cases in
//! `tests/engine/test_strategy_evaluator.py` and `tests/graph/test_strategy_graph.py`.
//!
//! # Boundary representation
//!
//! Python's `_decimal` calls `Decimal(str(raw))`. Rust does not gain a decimal crate here, so
//! the boundary supplies the *same lexeme* Python hands to `Decimal`:
//!
//! * [`ParamValue::Int`] is stringified with `i64::to_string`;
//! * [`ParamValue::Float`] with `f64::to_string` — Rust and Python both produce the shortest
//!   round-tripping decimal, so the numeric value is identical even where the spelling differs
//!   (`1e-7` vs `0.0000001`, `60.0` vs `60`). The lexeme parser normalises both;
//! * [`ParamValue::Str`] is used verbatim, exactly as `Decimal(str("60.5"))` is.
//!
//! Validation then uses only integer/string reasoning on that lexeme: sign, digit count,
//! trailing zeros and decimal exponent. **No decimal arithmetic is performed** — nothing is
//! added, multiplied or rounded. Money-valued fields are returned as validated
//! [`DecimalText`] for the Python control plane to convert (ADR-0050 clause 4: a Rust decimal
//! with `money.py`'s per-operation precision does not exist yet).
//!
//! ## Deliberately left in Python
//!
//! * Graph document validation (`parse_document` / `validate_graph` from `ezchain_core.graph`).
//!   It is a separate kernel with its own schema and error vocabulary, not a scalar parameter;
//!   Rust checks the shared fields and that `graph` is present, and returns
//!   [`ValidatedParams::GraphDeferred`]. The caller must still run the Python validator before
//!   a graph document can gate money.
//! * All money arithmetic (`evaluate_strategy`, ledger math). It stays Python-side per
//!   ADR-0050 clause 4.

#![forbid(unsafe_code)]

use std::cmp::Ordering;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt;

/// The four accepted windows, in the order Python joins them in its error message.
pub const SUPPORTED_LOOKBACKS: [&str; 4] = ["5m", "1h", "6h", "24h"];
/// Python `DEFAULT_INTERVAL_MINUTES`.
pub const DEFAULT_INTERVAL_MINUTES: i64 = 60;
/// Python `MIN_INTERVAL_MINUTES`.
pub const MIN_INTERVAL_MINUTES: i64 = 1;
/// Python `MAX_INTERVAL_MINUTES`.
pub const MAX_INTERVAL_MINUTES: i64 = 10_080;

const DEFAULT_ORDER_USD: &str = "100";
const DEFAULT_MAX_POSITION_USD: &str = "500";
const DEFAULT_ENTRY_CHANGE_PCT: &str = "5";
const DEFAULT_EXIT_CHANGE_PCT: &str = "-3";
const DEFAULT_TAKE_PROFIT_PCT: &str = "20";
const DEFAULT_STOP_LOSS_PCT: &str = "8";

const COMMON_KEYS: [&str; 3] = ["order_usd", "interval_minutes", "max_position_usd"];
const EXIT_KEYS: [&str; 2] = ["take_profit_pct", "stop_loss_pct"];

/// A rejection from parameter validation. `code` is the stable Python `StrategyValidationError.code`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StrategyValidationError {
    pub code: &'static str,
    pub message: String,
}

impl fmt::Display for StrategyValidationError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for StrategyValidationError {}

fn err(code: &'static str, message: impl Into<String>) -> StrategyValidationError {
    StrategyValidationError {
        code,
        message: message.into(),
    }
}

/// A value in a strategy's parameter object.
///
/// Python accepts `int`, `float`, `str` and `Decimal` as numbers and rejects `bool` (an `int`
/// subclass) explicitly. `None` is the same as a missing key for `_decimal` but *not* for
/// `_lookback`, which is why [`ParamValue::Null`] is carried rather than dropped.
#[derive(Debug, Clone, PartialEq)]
pub enum ParamValue {
    Int(i64),
    Float(f64),
    /// A Python string; fed to `Decimal` verbatim by the Python original.
    Str(String),
    Bool(bool),
    /// Python `None`.
    Null,
    Array,
    Object(BTreeMap<String, ParamValue>),
    /// Any other Python type; carries `type(raw).__name__` for the type error.
    Other(String),
}

impl ParamValue {
    /// Python's `type(raw).__name__` for a value the numeric parser refuses.
    fn type_name(&self) -> &str {
        match self {
            ParamValue::Bool(_) => "bool",
            ParamValue::Array => "list",
            ParamValue::Object(_) => "dict",
            ParamValue::Other(name) => name,
            // Int/Float/Str/Null are handled before this is called.
            ParamValue::Int(_) | ParamValue::Float(_) | ParamValue::Str(_) | ParamValue::Null => {
                "object"
            }
        }
    }
}

/// A strategy parameter object. `BTreeMap` keeps key order deterministic.
pub type Params = BTreeMap<String, ParamValue>;

/// The top-level `params` argument of Python's `validate_params`:
/// `None` becomes `{}`, a mapping is validated, anything else is a type error.
#[derive(Debug, Clone, Copy)]
pub enum ParamsInput<'a> {
    None,
    Object(&'a Params),
    /// A non-mapping; carries Python's `type(raw).__name__`.
    Other(&'static str),
}

impl<'a> From<&'a Params> for ParamsInput<'a> {
    fn from(value: &'a Params) -> Self {
        ParamsInput::Object(value)
    }
}

impl<'a> From<Option<&'a Params>> for ParamsInput<'a> {
    fn from(value: Option<&'a Params>) -> Self {
        match value {
            Some(params) => ParamsInput::Object(params),
            None => ParamsInput::None,
        }
    }
}

/// One of the four supported lookback windows.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lookback {
    M5,
    H1,
    H6,
    H24,
}

impl Lookback {
    pub fn as_str(self) -> &'static str {
        match self {
            Lookback::M5 => "5m",
            Lookback::H1 => "1h",
            Lookback::H6 => "6h",
            Lookback::H24 => "24h",
        }
    }
}

/// A validated numeric scalar, kept as its exact decimal lexeme.
///
/// This carries no money type and performs no arithmetic. The Python control plane converts it
/// to `Decimal` when it needs the value; keeping the text here loses no precision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecimalText(pub String);

impl DecimalText {
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

/// The three fields shared by every kind (Python `StrategyParams`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SharedParams {
    pub order_usd: DecimalText,
    pub interval_minutes: i64,
    pub max_position_usd: DecimalText,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MomentumParams {
    pub shared: SharedParams,
    pub lookback: Lookback,
    pub entry_change_pct: DecimalText,
    pub exit_change_pct: DecimalText,
    pub take_profit_pct: DecimalText,
    pub stop_loss_pct: DecimalText,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DipParams {
    pub shared: SharedParams,
    pub lookback: Lookback,
    pub entry_change_pct: DecimalText,
    pub take_profit_pct: DecimalText,
    pub stop_loss_pct: DecimalText,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DcaParams {
    pub shared: SharedParams,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ThresholdParams {
    pub shared: SharedParams,
    pub buy_below_usd: DecimalText,
    pub sell_above_usd: DecimalText,
}

#[derive(Debug, Clone, PartialEq)]
pub struct PluginParams {
    pub shared: SharedParams,
    pub config: BTreeMap<String, ParamValue>,
}

/// A graph whose *scalar* fields passed validation. The document itself is still unverified:
/// the Python control plane must run `parse_document` and `validate_graph` on it.
#[derive(Debug, Clone, PartialEq)]
pub struct GraphDeferred {
    pub shared: SharedParams,
    /// The `graph` value exactly as supplied, for the Python validator.
    pub document: ParamValue,
}

/// The result of `validate_params`: Python's `AnyParams` union, plus the graph deferral.
#[derive(Debug, Clone, PartialEq)]
pub enum ValidatedParams {
    Momentum(MomentumParams),
    Dip(DipParams),
    Dca(DcaParams),
    Threshold(ThresholdParams),
    Plugin(PluginParams),
    GraphDeferred(GraphDeferred),
}

impl ValidatedParams {
    /// The shared fields, whatever the kind.
    pub fn shared(&self) -> &SharedParams {
        match self {
            ValidatedParams::Momentum(p) => &p.shared,
            ValidatedParams::Dip(p) => &p.shared,
            ValidatedParams::Dca(p) => &p.shared,
            ValidatedParams::Threshold(p) => &p.shared,
            ValidatedParams::Plugin(p) => &p.shared,
            ValidatedParams::GraphDeferred(p) => &p.shared,
        }
    }

    /// Python `validate_params(kind, params).interval_minutes`.
    pub fn interval_minutes(&self) -> i64 {
        self.shared().interval_minutes
    }
}

// ---------------------------------------------------------------------------
// Exact decimal *lexeme* handling: integer/string reasoning, no arithmetic.
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq)]
struct Num {
    negative: bool,
    /// Coefficient digits, kept verbatim (before `e` and with the point removed).
    digits: String,
    /// `value = sign * digits * 10^exp`.
    exp: i32,
    finite: bool,
}

fn parse_decimal(lexeme: &str) -> Option<Num> {
    // `Decimal` accepts surrounding ASCII whitespace.
    let s = lexeme.trim();
    if s.is_empty() {
        return None;
    }
    let (negative, rest) = match s.as_bytes()[0] {
        b'+' => (false, &s[1..]),
        b'-' => (true, &s[1..]),
        _ => (false, s),
    };
    let lower = rest.to_ascii_lowercase();
    if matches!(lower.as_str(), "nan" | "snan" | "inf" | "infinity") {
        return Some(Num {
            negative,
            digits: String::new(),
            exp: 0,
            finite: false,
        });
    }

    let (mantissa, exp) = match rest.find(['e', 'E']) {
        Some(index) => {
            let exponent = &rest[index + 1..];
            (&rest[..index], parse_exponent(exponent)?)
        }
        None => (rest, 0),
    };

    // `Decimal` ignores underscores anywhere in the mantissa and the exponent, even
    // leading, trailing and repeated ones (`Decimal("_10") == Decimal("10")`).
    let mantissa = mantissa.replace('_', "");
    let mut digits = String::new();
    let mut seen_dot = false;
    let mut fractional = 0_i32;
    for c in mantissa.chars() {
        match c {
            '0'..='9' => {
                digits.push(c);
                if seen_dot {
                    fractional += 1;
                }
            }
            '.' if !seen_dot => seen_dot = true,
            _ => return None,
        }
    }
    if digits.is_empty() {
        return None;
    }
    Some(Num {
        negative,
        digits,
        exp: exp - fractional,
        finite: true,
    })
}

fn parse_exponent(text: &str) -> Option<i32> {
    let text = text.replace('_', "");
    let (negative, digits) = match text.as_bytes().first() {
        Some(b'-') => (true, &text[1..]),
        Some(b'+') => (false, &text[1..]),
        _ => (false, text.as_str()),
    };
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let magnitude = digits.parse::<i32>().ok()?;
    Some(if negative { -magnitude } else { magnitude })
}

impl Num {
    fn significant(&self) -> &str {
        self.digits.trim_start_matches('0')
    }

    fn is_zero(&self) -> bool {
        self.finite && self.significant().is_empty()
    }

    fn is_negative(&self) -> bool {
        self.finite && self.negative && !self.is_zero()
    }

    fn is_positive(&self) -> bool {
        self.finite && !self.negative && !self.is_zero()
    }

    /// Python `value == value.to_integral_value()`.
    fn is_integral(&self) -> bool {
        if !self.finite {
            return false;
        }
        let significant = self.significant();
        if significant.is_empty() {
            return true;
        }
        let trailing = significant.bytes().rev().take_while(|b| *b == b'0').count() as i32;
        self.exp + trailing >= 0
    }

    /// Number of decimal digits before the point (0 for zero). Used for cheap range checks.
    fn magnitude(&self) -> i64 {
        let significant = self.significant();
        if significant.is_empty() {
            return 0;
        }
        significant.len() as i64 + self.exp as i64
    }

    /// Absolute-value comparison without building large powers of ten.
    fn cmp_abs(&self, other: &Self) -> Ordering {
        let a = self.significant();
        let b = other.significant();
        if a.is_empty() || b.is_empty() {
            return a.is_empty().cmp(&b.is_empty()).reverse();
        }
        let magnitude_a = a.len() as i64 + self.exp as i64;
        let magnitude_b = b.len() as i64 + other.exp as i64;
        if magnitude_a != magnitude_b {
            return magnitude_a.cmp(&magnitude_b);
        }
        // Same magnitude and same leading-digit count means the digit strings, padded on the
        // right with zeros to equal length, compare exactly like the two values.
        match a.len().cmp(&b.len()) {
            Ordering::Equal => a.cmp(b),
            Ordering::Less => {
                let padded = format!("{a}{}", "0".repeat(b.len() - a.len()));
                padded.as_str().cmp(b)
            }
            Ordering::Greater => {
                let padded = format!("{b}{}", "0".repeat(a.len() - b.len()));
                a.cmp(padded.as_str())
            }
        }
    }

    /// Signed comparison of two finite numbers.
    fn cmp_finite(&self, other: &Self) -> Ordering {
        if self.is_zero() || other.is_zero() {
            return self.is_zero().cmp(&other.is_zero());
        }
        match (self.is_negative(), other.is_negative()) {
            (true, false) => Ordering::Less,
            (false, true) => Ordering::Greater,
            (false, false) => self.cmp_abs(other),
            (true, true) => self.cmp_abs(other).reverse(),
        }
    }

    /// A plain signed decimal string for the exact value, with no exponent notation.
    /// The point is placed from the coefficient length and exponent, so `"60.0"` survives as
    /// `"60.0"` and a negative value keeps its sign.
    fn to_decimal_text(&self) -> String {
        let sign = if self.negative { "-" } else { "" };
        let point = self.digits.len() as i64 + self.exp as i64;
        if point <= 0 {
            format!("{sign}0.{}{}", "0".repeat((-point) as usize), self.digits)
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
    }

    /// Python `int(value)`, for a finite integral value in `i64` range.
    fn to_i64(&self) -> Option<i64> {
        if !self.finite || !self.is_integral() {
            return None;
        }
        let significant = self.significant();
        if significant.is_empty() {
            return Some(0);
        }
        let trailing = significant.bytes().rev().take_while(|b| *b == b'0').count();
        let mut text = String::from(&significant[..significant.len() - trailing]);
        for _ in 0..(self.exp as i64 + trailing as i64) {
            text.push('0');
        }
        let value = text.parse::<i64>().ok()?;
        Some(if self.negative { -value } else { value })
    }
}

// ---------------------------------------------------------------------------
// The five ported helpers.
// ---------------------------------------------------------------------------

/// Python `_decimal`: parse a numeric parameter, with `None`/missing falling back to `default`.
fn decimal(
    params: &Params,
    key: &str,
    default: Option<&str>,
) -> Result<Num, StrategyValidationError> {
    let lexeme = match params.get(key) {
        None | Some(ParamValue::Null) => match default {
            Some(value) => value.to_string(),
            None => return Err(err("STRATEGY_PARAM_MISSING", format!("{key} is required"))),
        },
        Some(
            value @ (ParamValue::Bool(_)
            | ParamValue::Array
            | ParamValue::Object(_)
            | ParamValue::Other(_)),
        ) => {
            return Err(err(
                "STRATEGY_PARAM_TYPE",
                format!("{key} must be a number, got {}", value.type_name()),
            ));
        }
        Some(ParamValue::Int(value)) => value.to_string(),
        Some(ParamValue::Float(value)) => value.to_string(),
        Some(ParamValue::Str(value)) => value.clone(),
    };
    let num = parse_decimal(&lexeme)
        .ok_or_else(|| err("STRATEGY_PARAM_TYPE", format!("{key} must be a number")))?;
    if !num.finite {
        return Err(err("STRATEGY_PARAM_TYPE", format!("{key} must be finite")));
    }
    Ok(num)
}

/// Python `_positive`.
fn positive(
    params: &Params,
    key: &str,
    default: &str,
) -> Result<DecimalText, StrategyValidationError> {
    let value = decimal(params, key, Some(default))?;
    if !value.is_positive() {
        return Err(err("STRATEGY_PARAM_RANGE", format!("{key} must be > 0")));
    }
    Ok(DecimalText(value.to_decimal_text()))
}

/// Python `_required_positive`.
fn required_positive(params: &Params, key: &str) -> Result<Num, StrategyValidationError> {
    let value = decimal(params, key, None)?;
    if !value.is_positive() {
        return Err(err("STRATEGY_PARAM_RANGE", format!("{key} must be > 0")));
    }
    Ok(value)
}

/// Python `_interval`: whole minutes in `1..=10080` (the boundary is inclusive).
pub fn interval(params: &Params) -> Result<i64, StrategyValidationError> {
    let value = decimal(
        params,
        "interval_minutes",
        Some(&DEFAULT_INTERVAL_MINUTES.to_string()),
    )?;
    if !value.is_integral() {
        return Err(err(
            "STRATEGY_PARAM_RANGE",
            "interval_minutes must be a whole number of minutes",
        ));
    }
    let range_error = || {
        err(
            "STRATEGY_PARAM_RANGE",
            format!(
                "interval_minutes must be between {MIN_INTERVAL_MINUTES} and {MAX_INTERVAL_MINUTES}"
            ),
        )
    };
    if value.is_negative() || value.magnitude() > 5 {
        return Err(range_error());
    }
    match value.to_i64() {
        Some(minutes) if (MIN_INTERVAL_MINUTES..=MAX_INTERVAL_MINUTES).contains(&minutes) => {
            Ok(minutes)
        }
        _ => Err(range_error()),
    }
}

/// Python `_lookback`. Unlike `_decimal`, an explicit `None` is *not* the default.
pub fn lookback(params: &Params) -> Result<Lookback, StrategyValidationError> {
    let unsupported = || {
        err(
            "STRATEGY_LOOKBACK_UNSUPPORTED",
            format!(
                "lookback must be one of: {}",
                SUPPORTED_LOOKBACKS.join(", ")
            ),
        )
    };
    match params.get("lookback") {
        None => Ok(Lookback::H1),
        Some(ParamValue::Str(value)) => match value.as_str() {
            "5m" => Ok(Lookback::M5),
            "1h" => Ok(Lookback::H1),
            "6h" => Ok(Lookback::H6),
            "24h" => Ok(Lookback::H24),
            _ => Err(unsupported()),
        },
        Some(_) => Err(unsupported()),
    }
}

/// Python `_reject_unknown`: the unknown keys, sorted, in one stable message.
pub fn reject_unknown(
    params: &Params,
    allowed: &BTreeSet<&str>,
) -> Result<(), StrategyValidationError> {
    let mut unknown: Vec<&str> = params
        .keys()
        .map(String::as_str)
        .filter(|key| !allowed.contains(key))
        .collect();
    unknown.sort_unstable();
    if unknown.is_empty() {
        Ok(())
    } else {
        Err(err(
            "STRATEGY_PARAM_UNKNOWN",
            format!("unknown parameter(s): {}", unknown.join(", ")),
        ))
    }
}

fn allowed<'a>(keys: &[&'a str]) -> BTreeSet<&'a str> {
    keys.iter().copied().collect()
}

/// Python `validate_params`. The check order is load-bearing: the shared fields are parsed
/// before the kind is dispatched, and within each kind the Python keyword-argument order is
/// preserved so the first failing field is reported.
pub fn validate_params<'a>(
    kind: &str,
    input: impl Into<ParamsInput<'a>>,
) -> Result<ValidatedParams, StrategyValidationError> {
    let empty = Params::new();
    let params = match input.into() {
        ParamsInput::None => &empty,
        ParamsInput::Object(params) => params,
        ParamsInput::Other(_) => {
            return Err(err("STRATEGY_PARAM_TYPE", "params must be an object"));
        }
    };

    let order_usd = positive(params, "order_usd", DEFAULT_ORDER_USD)?;
    let interval_minutes = interval(params)?;
    let max_position_usd = positive(params, "max_position_usd", DEFAULT_MAX_POSITION_USD)?;
    let shared = || SharedParams {
        order_usd: order_usd.clone(),
        interval_minutes,
        max_position_usd: max_position_usd.clone(),
    };

    match kind {
        "momentum" => {
            let mut keys = allowed(&COMMON_KEYS);
            keys.extend(EXIT_KEYS);
            keys.extend(["lookback", "entry_change_pct", "exit_change_pct"]);
            reject_unknown(params, &keys)?;

            let entry_change_pct = positive(params, "entry_change_pct", DEFAULT_ENTRY_CHANGE_PCT)?;
            let exit_change = decimal(params, "exit_change_pct", Some(DEFAULT_EXIT_CHANGE_PCT))?;
            if !exit_change.is_negative() {
                return Err(err(
                    "STRATEGY_PARAM_RANGE",
                    "exit_change_pct must be < 0 for momentum",
                ));
            }
            // Python evaluates these three in keyword-argument order at construction.
            let lookback = lookback(params)?;
            let take_profit_pct = positive(params, "take_profit_pct", DEFAULT_TAKE_PROFIT_PCT)?;
            let stop_loss_pct = positive(params, "stop_loss_pct", DEFAULT_STOP_LOSS_PCT)?;
            Ok(ValidatedParams::Momentum(MomentumParams {
                shared: shared(),
                lookback,
                entry_change_pct,
                exit_change_pct: DecimalText(exit_change.to_decimal_text()),
                take_profit_pct,
                stop_loss_pct,
            }))
        }
        "dip" => {
            let mut keys = allowed(&COMMON_KEYS);
            keys.extend(EXIT_KEYS);
            keys.extend(["lookback", "entry_change_pct"]);
            reject_unknown(params, &keys)?;

            let lookback = lookback(params)?;
            let entry_change_pct = positive(params, "entry_change_pct", DEFAULT_ENTRY_CHANGE_PCT)?;
            let take_profit_pct = positive(params, "take_profit_pct", DEFAULT_TAKE_PROFIT_PCT)?;
            let stop_loss_pct = positive(params, "stop_loss_pct", DEFAULT_STOP_LOSS_PCT)?;
            Ok(ValidatedParams::Dip(DipParams {
                shared: shared(),
                lookback,
                entry_change_pct,
                take_profit_pct,
                stop_loss_pct,
            }))
        }
        "dca" => {
            reject_unknown(params, &allowed(&COMMON_KEYS))?;
            Ok(ValidatedParams::Dca(DcaParams { shared: shared() }))
        }
        "threshold" => {
            let mut keys = allowed(&COMMON_KEYS);
            keys.extend(["buy_below_usd", "sell_above_usd"]);
            reject_unknown(params, &keys)?;

            let buy_below = required_positive(params, "buy_below_usd")?;
            let sell_above = required_positive(params, "sell_above_usd")?;
            if sell_above.cmp_finite(&buy_below) != Ordering::Greater {
                return Err(err(
                    "STRATEGY_PARAM_RANGE",
                    "sell_above_usd must be > buy_below_usd",
                ));
            }
            Ok(ValidatedParams::Threshold(ThresholdParams {
                shared: shared(),
                buy_below_usd: DecimalText(buy_below.to_decimal_text()),
                sell_above_usd: DecimalText(sell_above.to_decimal_text()),
            }))
        }
        "plugin" => {
            let mut keys = allowed(&COMMON_KEYS);
            keys.insert("config");
            reject_unknown(params, &keys)?;

            let config = match params.get("config") {
                None => BTreeMap::new(),
                Some(ParamValue::Object(map)) => map.clone(),
                Some(_) => {
                    return Err(err("STRATEGY_PARAM_TYPE", "config must be an object"));
                }
            };
            Ok(ValidatedParams::Plugin(PluginParams {
                shared: shared(),
                config,
            }))
        }
        "graph" => {
            let mut keys = allowed(&COMMON_KEYS);
            keys.insert("graph");
            reject_unknown(params, &keys)?;

            let document = match params.get("graph") {
                None | Some(ParamValue::Null) => {
                    return Err(err("STRATEGY_GRAPH_INVALID", "graph is required"));
                }
                Some(value) => value.clone(),
            };
            // Document parsing and graph validation remain in Python (see module docs).
            Ok(ValidatedParams::GraphDeferred(GraphDeferred {
                shared: shared(),
                document,
            }))
        }
        other => Err(err(
            "STRATEGY_KIND_UNSUPPORTED",
            format!("unsupported kind: {other}"),
        )),
    }
}

/// Python `interval_minutes`: validate and return the interval in one step.
pub fn interval_minutes<'a>(
    kind: &str,
    input: impl Into<ParamsInput<'a>>,
) -> Result<i64, StrategyValidationError> {
    Ok(validate_params(kind, input)?.interval_minutes())
}
