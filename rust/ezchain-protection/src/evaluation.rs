//! Pure trigger decision ported from `ezchain_core.protection.evaluate_arm`.
//!
//! The branch order and every returned reason are reproduced exactly. Decimal
//! arithmetic stays in the Python control plane: the caller supplies already
//! computed trigger prices as opaque validated lexemes, and this module only
//! compares them with the current mark (and tracks the trailing peak).

use std::cmp::Ordering;
use std::collections::BTreeMap;

use crate::lexeme::{constant, DecimalLexeme};
use crate::validation::{ArmKind, ArmParams};

/// Arm lifecycle status, spelled as in the Python oracle.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ArmStatus {
    Armed,
    Triggered,
    Cancelled,
    Expired,
}

impl ArmStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Armed => "armed",
            Self::Triggered => "triggered",
            Self::Cancelled => "cancelled",
            Self::Expired => "expired",
        }
    }
}

/// The two outcomes of a pure evaluation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Hold,
    Trigger,
}

impl Action {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Hold => "hold",
            Self::Trigger => "trigger",
        }
    }
}

/// Every reason the Python evaluator can return, spelled exactly.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Reason {
    NotArmed,
    Expired,
    PriceUnknown,
    MarkStale,
    NoPosition,
    InvalidArm,
    Armed,
    StopLoss,
    TakeProfit,
    TakeProfitLevel,
    TrailingStop,
}

impl Reason {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NotArmed => "NOT_ARMED",
            Self::Expired => "EXPIRED",
            Self::PriceUnknown => "PRICE_UNKNOWN",
            Self::MarkStale => "MARK_STALE",
            Self::NoPosition => "NO_POSITION",
            Self::InvalidArm => "INVALID_ARM",
            Self::Armed => "ARMED",
            Self::StopLoss => "STOP_LOSS",
            Self::TakeProfit => "TAKE_PROFIT",
            Self::TakeProfitLevel => "TAKE_PROFIT_LEVEL",
            Self::TrailingStop => "TRAILING_STOP",
        }
    }
}

/// Persisted arm state needed by the pure decision.
pub struct ProtectionArm<'a> {
    pub status: ArmStatus,
    pub params: ArmParams,
    pub peak_usd: Option<DecimalLexeme>,
    pub triggered_level_indexes: &'a [usize],
    /// Epoch nanoseconds. `None` means no expiry. The caller converts Python
    /// datetimes; only the `now >= expires_at` comparison happens here.
    pub expires_at_ns: Option<i128>,
}

/// Current observation plus opaque, caller-computed thresholds.
pub struct EvaluateInput<'a> {
    /// Current mark; `None` maps to `PRICE_UNKNOWN`.
    pub price_usd: Option<&'a DecimalLexeme>,
    pub position_quantity: &'a DecimalLexeme,
    pub position_avg_cost_usd: &'a DecimalLexeme,
    pub stale: bool,
    /// Epoch nanoseconds; `None` disables the expiry check.
    pub now_ns: Option<i128>,
    /// Opaque threshold for a single-threshold arm whose Python threshold needs
    /// decimal arithmetic (percent loss, percent gain, trailing distance/percent).
    /// `None` reproduces Python's `INVALID_ARM` when the threshold is absent.
    /// Ignored for price-based single arms, whose threshold is in `params`.
    pub computed_trigger_price: Option<&'a DecimalLexeme>,
    /// Opaque thresholds for ladder levels, indexed by level. A missing entry
    /// (`None`) reproduces Python's `INVALID_ARM` for a gain-based level.
    /// Price-based levels use `level.price_usd` from `params` instead.
    pub computed_level_triggers: &'a [Option<&'a DecimalLexeme>],
}

/// The pure decision. `inputs` mirrors Python's `ArmEvaluation.inputs` replay map.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ArmEvaluation {
    pub action: Action,
    pub reason: Reason,
    pub sell_fraction: DecimalLexeme,
    pub level_index: Option<usize>,
    pub peak_usd: Option<DecimalLexeme>,
    pub inputs: BTreeMap<String, Option<String>>,
}

fn hold(
    reason: Reason,
    peak: Option<DecimalLexeme>,
    inputs: BTreeMap<String, Option<String>>,
) -> ArmEvaluation {
    ArmEvaluation {
        action: Action::Hold,
        reason,
        sell_fraction: constant("0"),
        level_index: None,
        peak_usd: peak,
        inputs,
    }
}

fn trigger(
    reason: Reason,
    sell_fraction: DecimalLexeme,
    level_index: Option<usize>,
    peak: Option<DecimalLexeme>,
    inputs: BTreeMap<String, Option<String>>,
) -> ArmEvaluation {
    ArmEvaluation {
        action: Action::Trigger,
        reason,
        sell_fraction,
        level_index,
        peak_usd: peak,
        inputs,
    }
}

fn add_trigger(inputs: &mut BTreeMap<String, Option<String>>, threshold: &DecimalLexeme) {
    inputs.insert("trigger_price_usd".into(), Some(threshold.as_str().into()));
}

/// Port of `evaluate_arm(arm, ...)`. Never places an order.
pub fn evaluate_arm(arm: &ProtectionArm<'_>, input: &EvaluateInput<'_>) -> ArmEvaluation {
    let mut inputs = BTreeMap::new();
    inputs.insert("kind".into(), Some(arm.params.kind.as_str().into()));
    inputs.insert("status".into(), Some(arm.status.as_str().into()));
    inputs.insert(
        "price_usd".into(),
        input.price_usd.map(|p| p.as_str().to_owned()),
    );
    inputs.insert(
        "position_quantity".into(),
        Some(input.position_quantity.as_str().into()),
    );
    inputs.insert(
        "position_avg_cost_usd".into(),
        Some(input.position_avg_cost_usd.as_str().into()),
    );
    inputs.insert(
        "peak_usd".into(),
        arm.peak_usd.as_ref().map(|p| p.as_str().into()),
    );

    // Fail-closed gates, in the same order as Python.
    if arm.status != ArmStatus::Armed {
        return hold(Reason::NotArmed, arm.peak_usd.clone(), inputs);
    }
    if let (Some(expires), Some(now)) = (arm.expires_at_ns, input.now_ns) {
        if now >= expires {
            return hold(Reason::Expired, arm.peak_usd.clone(), inputs);
        }
    }
    let Some(price) = input.price_usd else {
        return hold(Reason::PriceUnknown, arm.peak_usd.clone(), inputs);
    };
    if input.stale {
        return hold(Reason::MarkStale, arm.peak_usd.clone(), inputs);
    }
    let zero = constant("0");
    if input.position_quantity.cmp_lexeme(&zero) != Ordering::Greater {
        return hold(Reason::NoPosition, arm.peak_usd.clone(), inputs);
    }

    let params = &arm.params;
    match params.kind {
        ArmKind::StopLoss => {
            let threshold = if let Some(price_usd) = &params.price_usd {
                price_usd
            } else if params.loss_pct.is_some() {
                // Python: only computes the threshold when avg_cost > 0.
                match input.computed_trigger_price {
                    Some(t) => t,
                    None => return hold(Reason::InvalidArm, arm.peak_usd.clone(), inputs),
                }
            } else {
                return hold(Reason::InvalidArm, arm.peak_usd.clone(), inputs);
            };
            add_trigger(&mut inputs, threshold);
            if price.cmp_lexeme(threshold) != Ordering::Greater {
                trigger(
                    Reason::StopLoss,
                    params.sell_fraction.clone(),
                    None,
                    arm.peak_usd.clone(),
                    inputs,
                )
            } else {
                hold(Reason::Armed, arm.peak_usd.clone(), inputs)
            }
        }
        ArmKind::TakeProfit => {
            if !params.levels.is_empty() {
                for (index, level) in params.levels.iter().enumerate() {
                    if arm.triggered_level_indexes.contains(&index) {
                        continue;
                    }
                    let threshold = if let Some(price_usd) = &level.price_usd {
                        price_usd
                    } else {
                        match input.computed_level_triggers.get(index).and_then(|t| *t) {
                            Some(t) => t,
                            None => return hold(Reason::InvalidArm, arm.peak_usd.clone(), inputs),
                        }
                    };
                    inputs.insert(
                        format!("level_{index}_trigger_usd"),
                        Some(threshold.as_str().into()),
                    );
                    if price.cmp_lexeme(threshold) != Ordering::Less {
                        return trigger(
                            Reason::TakeProfitLevel,
                            level.fraction.clone(),
                            Some(index),
                            arm.peak_usd.clone(),
                            inputs,
                        );
                    }
                }
                return hold(Reason::Armed, arm.peak_usd.clone(), inputs);
            }

            let threshold = if let Some(price_usd) = &params.price_usd {
                price_usd
            } else if params.gain_pct.is_some() {
                match input.computed_trigger_price {
                    Some(t) => t,
                    None => return hold(Reason::InvalidArm, arm.peak_usd.clone(), inputs),
                }
            } else {
                return hold(Reason::InvalidArm, arm.peak_usd.clone(), inputs);
            };
            add_trigger(&mut inputs, threshold);
            if price.cmp_lexeme(threshold) != Ordering::Less {
                trigger(
                    Reason::TakeProfit,
                    params.sell_fraction.clone(),
                    None,
                    arm.peak_usd.clone(),
                    inputs,
                )
            } else {
                hold(Reason::Armed, arm.peak_usd.clone(), inputs)
            }
        }
        ArmKind::TrailingStop => {
            // Fairness: only an above-water price moves the peak. Both checks
            // are comparisons, so no decimal arithmetic is needed here.
            let above_water = if input.position_avg_cost_usd.cmp_lexeme(&zero) == Ordering::Greater
            {
                price.cmp_lexeme(input.position_avg_cost_usd) == Ordering::Greater
            } else {
                true
            };
            let peak = if above_water {
                match &arm.peak_usd {
                    None => Some(price.clone()),
                    Some(existing) => {
                        if price.cmp_lexeme(existing) == Ordering::Greater {
                            Some(price.clone())
                        } else {
                            Some(existing.clone())
                        }
                    }
                }
            } else {
                arm.peak_usd.clone()
            };
            inputs.insert("peak_usd".into(), peak.as_ref().map(|p| p.as_str().into()));
            let Some(peak) = peak else {
                return hold(Reason::Armed, None, inputs);
            };

            let has_param = params.trailing_pct.is_some() || params.trailing_price_usd.is_some();
            if !has_param {
                return hold(Reason::InvalidArm, Some(peak), inputs);
            }
            let Some(threshold) = input.computed_trigger_price else {
                return hold(Reason::InvalidArm, Some(peak), inputs);
            };
            add_trigger(&mut inputs, threshold);
            if price.cmp_lexeme(threshold) != Ordering::Greater {
                trigger(
                    Reason::TrailingStop,
                    params.sell_fraction.clone(),
                    None,
                    Some(peak),
                    inputs,
                )
            } else {
                hold(Reason::Armed, Some(peak), inputs)
            }
        }
    }
}
