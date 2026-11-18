//! Port of `ezchain_core.protection.validate_arm`, branch for branch.
//!
//! Every Python rejection code is reproduced exactly. Money values are carried
//! as decimal lexemes; the one Python check that needs decimal *addition*
//! (ladder fractions summing to <= 1) is an opaque operand supplied by the
//! caller so this crate never performs arithmetic (see crate docs).

use std::cmp::Ordering;

use crate::lexeme::{constant, DecimalLexeme, LexemeError};

/// Stable rejection codes, spelled exactly as in the Python oracle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ValidationCode {
    ParamType,
    ParamUnknown,
    ParamMissing,
    ParamRange,
    KindUnsupported,
}

impl ValidationCode {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::ParamType => "PROTECTION_PARAM_TYPE",
            Self::ParamUnknown => "PROTECTION_PARAM_UNKNOWN",
            Self::ParamMissing => "PROTECTION_PARAM_MISSING",
            Self::ParamRange => "PROTECTION_PARAM_RANGE",
            Self::KindUnsupported => "PROTECTION_KIND_UNSUPPORTED",
        }
    }
}

/// A typed validation rejection with the original code and message.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ProtectionValidationError {
    pub code: ValidationCode,
    pub message: String,
}

impl ProtectionValidationError {
    fn new(code: ValidationCode, message: impl Into<String>) -> Self {
        Self {
            code,
            message: message.into(),
        }
    }
}

impl std::fmt::Display for ProtectionValidationError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code.as_str(), self.message)
    }
}

impl std::error::Error for ProtectionValidationError {}

/// The three arm kinds the Python validator accepts.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArmKind {
    StopLoss,
    TrailingStop,
    TakeProfit,
}

impl ArmKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::StopLoss => "stop_loss",
            Self::TrailingStop => "trailing_stop",
            Self::TakeProfit => "take_profit",
        }
    }
}

/// One take-profit ladder rung after validation.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TakeProfitLevel {
    pub price_usd: Option<DecimalLexeme>,
    pub gain_pct: Option<DecimalLexeme>,
    pub fraction: DecimalLexeme,
}

/// Parsed and validated arm parameters (the Python `ArmParams`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArmParams {
    pub kind: ArmKind,
    pub price_usd: Option<DecimalLexeme>,
    pub loss_pct: Option<DecimalLexeme>,
    pub gain_pct: Option<DecimalLexeme>,
    pub trailing_pct: Option<DecimalLexeme>,
    pub trailing_price_usd: Option<DecimalLexeme>,
    pub sell_fraction: DecimalLexeme,
    pub levels: Vec<TakeProfitLevel>,
}

/// A caller-supplied parameter value. `Str` is an (as yet unvalidated) decimal
/// lexeme. `Null` is Python `None`. `NonNumeric` is anything else; the inner
/// string is Python's `type(raw).__name__` and is used in the error message.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ParamValue<'a> {
    Str(&'a str),
    Null,
    NonNumeric(&'a str),
}

/// A mapping-like parameter object. `is_object = false` models Python's
/// non-mapping input (e.g. the integer `5`).
#[derive(Clone, Copy, Debug)]
pub struct ParamsInput<'a> {
    pub is_object: bool,
    pub entries: &'a [(&'a str, ParamValue<'a>)],
}

/// The `levels` key of a take-profit arm.
#[derive(Clone, Copy, Debug)]
pub enum LevelsInput<'a> {
    /// Key absent or explicitly null: the single price/gain arm.
    Absent,
    /// Key present but not a non-empty list.
    NotList,
    List(&'a [LevelInput<'a>]),
}

/// One ladder entry before validation.
#[derive(Clone, Copy, Debug)]
pub struct LevelInput<'a> {
    pub is_object: bool,
    pub entries: &'a [(&'a str, ParamValue<'a>)],
}

/// Input to `validate_arm`.
pub struct ValidateInput<'a> {
    pub kind: &'a str,
    /// `None` models Python's `params is None` and is treated as `{}`.
    pub params: Option<ParamsInput<'a>>,
    /// Structured `levels` value, when the `levels` key is present. Kept
    /// separate from `params` because a ladder is a list of objects.
    pub levels: LevelsInput<'a>,
    /// Opaque caller-computed sum of the ladder fractions, as a decimal lexeme.
    /// Ignored unless `levels` is a non-empty list.
    pub fraction_sum: &'a str,
}

fn get<'a>(params: &ParamsInput<'a>, key: &str) -> Option<ParamValue<'a>> {
    params
        .entries
        .iter()
        .find(|(k, _)| *k == key)
        .map(|(_, v)| *v)
}

fn reject_unknown(
    params: &ParamsInput<'_>,
    allowed: &[&str],
) -> Result<(), ProtectionValidationError> {
    let mut unknown: Vec<&str> = params
        .entries
        .iter()
        .map(|(k, _)| *k)
        .filter(|k| !allowed.contains(k))
        .collect();
    unknown.sort_unstable();
    unknown.dedup();
    if unknown.is_empty() {
        Ok(())
    } else {
        Err(ProtectionValidationError::new(
            ValidationCode::ParamUnknown,
            format!("unknown parameter(s): {}", unknown.join(", ")),
        ))
    }
}

fn decimal(value: ParamValue<'_>, key: &str) -> Result<DecimalLexeme, ProtectionValidationError> {
    match value {
        ParamValue::Str(s) => DecimalLexeme::parse(s).map_err(|e| match e {
            LexemeError::NonFinite => ProtectionValidationError::new(
                ValidationCode::ParamType,
                format!("{key} must be finite"),
            ),
            LexemeError::Syntax => ProtectionValidationError::new(
                ValidationCode::ParamType,
                format!("{key} must be a number"),
            ),
        }),
        ParamValue::NonNumeric(ty) => Err(ProtectionValidationError::new(
            ValidationCode::ParamType,
            format!("{key} must be a number, got {ty}"),
        )),
        ParamValue::Null => Err(ProtectionValidationError::new(
            ValidationCode::ParamType,
            format!("{key} must be a number, got NoneType"),
        )),
    }
}

fn optional_decimal(
    params: &ParamsInput<'_>,
    key: &str,
) -> Result<Option<DecimalLexeme>, ProtectionValidationError> {
    match get(params, key) {
        None | Some(ParamValue::Null) => Ok(None),
        Some(value) => decimal(value, key).map(Some),
    }
}

fn positive(value: &DecimalLexeme, key: &str) -> Result<(), ProtectionValidationError> {
    if value.cmp_lexeme(&constant("0")) != Ordering::Greater {
        Err(ProtectionValidationError::new(
            ValidationCode::ParamRange,
            format!("{key} must be > 0"),
        ))
    } else {
        Ok(())
    }
}

fn at_most_one(value: &DecimalLexeme, key: &str) -> Result<(), ProtectionValidationError> {
    if value.cmp_lexeme(&constant("1")) == Ordering::Greater {
        Err(ProtectionValidationError::new(
            ValidationCode::ParamRange,
            format!("{key} must be <= 1"),
        ))
    } else {
        Ok(())
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Basis {
    Price,
    Gain,
}

fn parse_levels(
    levels: LevelsInput<'_>,
    fraction_sum: &str,
) -> Result<Vec<TakeProfitLevel>, ProtectionValidationError> {
    let entries = match levels {
        LevelsInput::List(v) if !v.is_empty() => v,
        LevelsInput::Absent | LevelsInput::NotList | LevelsInput::List(_) => {
            return Err(ProtectionValidationError::new(
                ValidationCode::ParamMissing,
                "levels must be a non-empty list for a ladder",
            ))
        }
    };

    let mut parsed = Vec::with_capacity(entries.len());
    let mut basis: Option<Basis> = None;
    let mut last_threshold: Option<DecimalLexeme> = None;

    for (index, entry) in entries.iter().enumerate() {
        let entry_params = ParamsInput {
            is_object: entry.is_object,
            entries: entry.entries,
        };
        if !entry.is_object {
            return Err(ProtectionValidationError::new(
                ValidationCode::ParamType,
                format!("levels[{index}] must be an object"),
            ));
        }
        reject_unknown(&entry_params, &["price_usd", "gain_pct", "fraction"])?;

        let fraction_value = get(&entry_params, "fraction").ok_or_else(|| {
            ProtectionValidationError::new(
                ValidationCode::ParamMissing,
                format!("levels[{index}].fraction is required"),
            )
        })?;
        let fraction_key = format!("levels[{index}].fraction");
        let fraction = decimal(fraction_value, &fraction_key)?;
        positive(&fraction, "fraction")?;
        at_most_one(&fraction, &format!("levels[{index}].fraction"))?;

        let price = optional_decimal(&entry_params, "price_usd")?;
        let gain = optional_decimal(&entry_params, "gain_pct")?;
        if price.is_none() == gain.is_none() {
            return Err(ProtectionValidationError::new(
                ValidationCode::ParamMissing,
                format!("levels[{index}] needs exactly one of price_usd, gain_pct"),
            ));
        }
        let this_basis = if price.is_some() {
            Basis::Price
        } else {
            Basis::Gain
        };
        match basis {
            None => basis = Some(this_basis),
            Some(existing) if existing != this_basis => {
                return Err(ProtectionValidationError::new(
                    ValidationCode::ParamRange,
                    "ladder levels must share one price basis",
                ))
            }
            Some(_) => {}
        }

        let threshold = price
            .as_ref()
            .or(gain.as_ref())
            .expect("exactly one present");
        positive(threshold, &format!("levels[{index}] threshold"))?;
        if let Some(last) = &last_threshold {
            if threshold.cmp_lexeme(last) != Ordering::Greater {
                return Err(ProtectionValidationError::new(
                    ValidationCode::ParamRange,
                    "ladder levels must be strictly increasing",
                ));
            }
        }
        last_threshold = Some(threshold.clone());
        parsed.push(TakeProfitLevel {
            price_usd: price,
            gain_pct: gain,
            fraction,
        });
    }

    // Fraction sum: the only check needing decimal addition in Python. The
    // caller performs that addition and hands us the opaque result lexeme.
    let sum = decimal(ParamValue::Str(fraction_sum), "fraction sum")?;
    if sum.cmp_lexeme(&constant("1")) == Ordering::Greater {
        return Err(ProtectionValidationError::new(
            ValidationCode::ParamRange,
            "ladder fractions must sum to <= 1",
        ));
    }
    Ok(parsed)
}

/// Port of `validate_arm(kind, params)`.
pub fn validate_arm(input: ValidateInput<'_>) -> Result<ArmParams, ProtectionValidationError> {
    let empty: &[(&str, ParamValue<'_>)] = &[];
    let params = input.params.unwrap_or(ParamsInput {
        is_object: true,
        entries: empty,
    });
    if !params.is_object {
        return Err(ProtectionValidationError::new(
            ValidationCode::ParamType,
            "params must be an object",
        ));
    }

    let kind = match input.kind {
        "stop_loss" => ArmKind::StopLoss,
        "trailing_stop" => ArmKind::TrailingStop,
        "take_profit" => ArmKind::TakeProfit,
        other => {
            return Err(ProtectionValidationError::new(
                ValidationCode::KindUnsupported,
                format!("unsupported kind: {other}"),
            ))
        }
    };

    // Python validates sell_fraction before dispatching on kind.
    let mut sell_fraction = constant("1");
    if let Some(value) = get(&params, "sell_fraction") {
        if !matches!(value, ParamValue::Null) {
            sell_fraction = decimal(value, "sell_fraction")?;
            positive(&sell_fraction, "sell_fraction")?;
            at_most_one(&sell_fraction, "sell_fraction")?;
        }
    }

    match kind {
        ArmKind::StopLoss => {
            reject_unknown(&params, &["price_usd", "loss_pct", "sell_fraction"])?;
            let price = optional_decimal(&params, "price_usd")?;
            let loss = optional_decimal(&params, "loss_pct")?;
            if price.is_none() == loss.is_none() {
                return Err(ProtectionValidationError::new(
                    ValidationCode::ParamMissing,
                    "stop-loss needs exactly one of price_usd, loss_pct",
                ));
            }
            if let Some(price) = &price {
                positive(price, "price_usd")?;
            }
            if let Some(loss) = &loss {
                positive(loss, "loss_pct")?;
                if loss.cmp_lexeme(&constant("100")) == Ordering::Greater {
                    return Err(ProtectionValidationError::new(
                        ValidationCode::ParamRange,
                        "loss_pct must be <= 100",
                    ));
                }
            }
            Ok(ArmParams {
                kind,
                price_usd: price,
                loss_pct: loss,
                gain_pct: None,
                trailing_pct: None,
                trailing_price_usd: None,
                sell_fraction,
                levels: Vec::new(),
            })
        }
        ArmKind::TrailingStop => {
            reject_unknown(
                &params,
                &["trailing_pct", "trailing_price_usd", "sell_fraction"],
            )?;
            let pct = optional_decimal(&params, "trailing_pct")?;
            let distance = optional_decimal(&params, "trailing_price_usd")?;
            if pct.is_none() == distance.is_none() {
                return Err(ProtectionValidationError::new(
                    ValidationCode::ParamMissing,
                    "trailing stop needs exactly one of trailing_pct, trailing_price_usd",
                ));
            }
            if let Some(pct) = &pct {
                positive(pct, "trailing_pct")?;
                if pct.cmp_lexeme(&constant("100")) != Ordering::Less {
                    return Err(ProtectionValidationError::new(
                        ValidationCode::ParamRange,
                        "trailing_pct must be < 100",
                    ));
                }
            }
            if let Some(distance) = &distance {
                positive(distance, "trailing_price_usd")?;
            }
            Ok(ArmParams {
                kind,
                price_usd: None,
                loss_pct: None,
                gain_pct: None,
                trailing_pct: pct,
                trailing_price_usd: distance,
                sell_fraction,
                levels: Vec::new(),
            })
        }
        ArmKind::TakeProfit => {
            let levels_present = matches!(
                get(&params, "levels"),
                Some(ParamValue::Str(_)) | Some(ParamValue::NonNumeric(_))
            );
            if levels_present {
                reject_unknown(&params, &["levels"])?;
                return Ok(ArmParams {
                    kind,
                    price_usd: None,
                    loss_pct: None,
                    gain_pct: None,
                    trailing_pct: None,
                    trailing_price_usd: None,
                    sell_fraction: constant("1"),
                    levels: parse_levels(input.levels, input.fraction_sum)?,
                });
            }
            reject_unknown(&params, &["price_usd", "gain_pct", "sell_fraction"])?;
            let price = optional_decimal(&params, "price_usd")?;
            let gain = optional_decimal(&params, "gain_pct")?;
            if price.is_none() == gain.is_none() {
                return Err(ProtectionValidationError::new(
                    ValidationCode::ParamMissing,
                    "take-profit needs exactly one of price_usd, gain_pct (or levels)",
                ));
            }
            if let Some(price) = &price {
                positive(price, "price_usd")?;
            }
            if let Some(gain) = &gain {
                positive(gain, "gain_pct")?;
            }
            Ok(ArmParams {
                kind,
                price_usd: price,
                loss_pct: None,
                gain_pct: gain,
                trailing_pct: None,
                trailing_price_usd: None,
                sell_fraction,
                levels: Vec::new(),
            })
        }
    }
}
