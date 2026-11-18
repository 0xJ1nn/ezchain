//! Parity cases recorded from `ezchain_core.protection` (Python oracle).
//! Generated from the recorded oracle; do not edit by hand.
#![allow(clippy::unwrap_used)]

use std::collections::BTreeMap;

use ezchain_protection::*;

// ---------- validate_arm ----------
#[test]
fn validation_000() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: None,
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "stop-loss needs exactly one of price_usd, loss_pct"
    );
}

#[test]
fn validation_001() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: false,
            entries: &[],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_TYPE");
    assert_eq!(err.message, "params must be an object");
}

#[test]
fn validation_002() {
    let input = ValidateInput {
        kind: "bogus",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_KIND_UNSUPPORTED");
    assert_eq!(err.message, "unsupported kind: bogus");
}

#[test]
fn validation_003() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("0.90")),
                ("sell_fraction", ParamValue::Str("0.5")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "stop_loss");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), Some("0.90"));
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "0.5");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_004() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("0.90")),
                ("loss_pct", ParamValue::Str("8")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "stop-loss needs exactly one of price_usd, loss_pct"
    );
}

#[test]
fn validation_005() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "stop-loss needs exactly one of price_usd, loss_pct"
    );
}

#[test]
fn validation_006() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("unknown", ParamValue::Str("1")),
                ("price_usd", ParamValue::Str("0.90")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_UNKNOWN");
    assert_eq!(err.message, "unknown parameter(s): unknown");
}

#[test]
fn validation_007() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::Str("0"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "price_usd must be > 0");
}

#[test]
fn validation_008() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::Str("-1"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "price_usd must be > 0");
}

#[test]
fn validation_009() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("loss_pct", ParamValue::Str("0"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "loss_pct must be > 0");
}

#[test]
fn validation_010() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("loss_pct", ParamValue::Str("100.1"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "loss_pct must be <= 100");
}

#[test]
fn validation_011() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("loss_pct", ParamValue::Str("8"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "stop_loss");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), Some("8"));
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_012() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::Str("x"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_TYPE");
    assert_eq!(err.message, "price_usd must be a number");
}

#[test]
fn validation_013() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::Str("NaN"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_TYPE");
    assert_eq!(err.message, "price_usd must be finite");
}

#[test]
fn validation_014() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::NonNumeric("bool"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_TYPE");
    assert_eq!(err.message, "price_usd must be a number, got bool");
}

#[test]
fn validation_015() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("0.90")),
                ("sell_fraction", ParamValue::Str("0")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "sell_fraction must be > 0");
}

#[test]
fn validation_016() {
    let input = ValidateInput {
        kind: "stop_loss",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("0.90")),
                ("sell_fraction", ParamValue::Str("1.5")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "sell_fraction must be <= 1");
}

#[test]
fn validation_017() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("trailing_pct", ParamValue::Str("5"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "trailing_stop");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), Some("5"));
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_018() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("trailing_pct", ParamValue::Str("100"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "trailing_pct must be < 100");
}

#[test]
fn validation_019() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("trailing_pct", ParamValue::Str("0"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "trailing_pct must be > 0");
}

#[test]
fn validation_020() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("trailing_price_usd", ParamValue::Str("0.2"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "trailing_stop");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(
        p.trailing_price_usd.as_ref().map(|d| d.as_str()),
        Some("0.2")
    );
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_021() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("trailing_pct", ParamValue::Str("5")),
                ("trailing_price_usd", ParamValue::Str("0.2")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "trailing stop needs exactly one of trailing_pct, trailing_price_usd"
    );
}

#[test]
fn validation_022() {
    let input = ValidateInput {
        kind: "trailing_stop",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "trailing stop needs exactly one of trailing_pct, trailing_price_usd"
    );
}

#[test]
fn validation_023() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("price_usd", ParamValue::Str("1.2"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "take_profit");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), Some("1.2"));
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_024() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("gain_pct", ParamValue::Str("20"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "take_profit");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), Some("20"));
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 0);
}

#[test]
fn validation_025() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("gain_pct", ParamValue::Str("0"))],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "gain_pct must be > 0");
}

#[test]
fn validation_026() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "take-profit needs exactly one of price_usd, gain_pct (or levels)"
    );
}

#[test]
fn validation_027() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("1.2")),
                ("gain_pct", ParamValue::Str("20")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "take-profit needs exactly one of price_usd, gain_pct (or levels)"
    );
}

#[test]
fn validation_028() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[]),
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(err.message, "levels must be a non-empty list for a ladder");
}

#[test]
fn validation_029() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("20")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("40")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
        ]),
        fraction_sum: "1.0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "take_profit");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "1");
    assert_eq!(p.levels.len(), 2);
    assert_eq!(p.levels[0].price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(
        p.levels[0].gain_pct.as_ref().map(|d| d.as_str()),
        Some("20")
    );
    assert_eq!(p.levels[0].fraction.as_str(), "0.5");
    assert_eq!(p.levels[1].price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(
        p.levels[1].gain_pct.as_ref().map(|d| d.as_str()),
        Some("40")
    );
    assert_eq!(p.levels[1].fraction.as_str(), "0.5");
}

#[test]
fn validation_030() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("20")),
                    ("fraction", ParamValue::Str("0.6")),
                ],
            },
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("40")),
                    ("fraction", ParamValue::Str("0.6")),
                ],
            },
        ]),
        fraction_sum: "1.2",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "ladder fractions must sum to <= 1");
}

#[test]
fn validation_031() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[("gain_pct", ParamValue::Str("20"))],
        }]),
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(err.message, "levels[0].fraction is required");
}

#[test]
fn validation_032() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[
                ("gain_pct", ParamValue::Str("20")),
                ("fraction", ParamValue::Str("0")),
            ],
        }]),
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "fraction must be > 0");
}

#[test]
fn validation_033() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[
                ("gain_pct", ParamValue::Str("20")),
                ("fraction", ParamValue::Str("1.5")),
            ],
        }]),
        fraction_sum: "1.5",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "levels[0].fraction must be <= 1");
}

#[test]
fn validation_034() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[
                ("gain_pct", ParamValue::Str("20")),
                ("fraction", ParamValue::Str("0.5")),
                ("x", ParamValue::NonNumeric("int")),
            ],
        }]),
        fraction_sum: "0.5",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_UNKNOWN");
    assert_eq!(err.message, "unknown parameter(s): x");
}

#[test]
fn validation_035() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[
            LevelInput {
                is_object: true,
                entries: &[("price_usd", ParamValue::Str("1.2"))],
            },
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("40")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
        ]),
        fraction_sum: "0.5",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(err.message, "levels[0].fraction is required");
}

#[test]
fn validation_036() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("40")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("20")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
        ]),
        fraction_sum: "1.0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "ladder levels must be strictly increasing");
}

#[test]
fn validation_037() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("20")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
            LevelInput {
                is_object: true,
                entries: &[
                    ("gain_pct", ParamValue::Str("20")),
                    ("fraction", ParamValue::Str("0.5")),
                ],
            },
        ]),
        fraction_sum: "1.0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_RANGE");
    assert_eq!(err.message, "ladder levels must be strictly increasing");
}

#[test]
fn validation_038() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("1.2")),
                ("gain_pct", ParamValue::Str("20")),
                ("fraction", ParamValue::Str("0.5")),
            ],
        }]),
        fraction_sum: "0.5",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(
        err.message,
        "levels[0] needs exactly one of price_usd, gain_pct"
    );
}

#[test]
fn validation_039() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str("not-a-list"))],
        }),
        levels: LevelsInput::NotList,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_MISSING");
    assert_eq!(err.message, "levels must be a non-empty list for a ladder");
}

#[test]
fn validation_040() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[("levels", ParamValue::Str(""))],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: false,
            entries: &[],
        }]),
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_TYPE");
    assert_eq!(err.message, "levels[0] must be an object");
}

#[test]
fn validation_041() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("levels", ParamValue::Str("")),
                ("sell_fraction", ParamValue::Str("0.5")),
            ],
        }),
        levels: LevelsInput::List(&[LevelInput {
            is_object: true,
            entries: &[
                ("gain_pct", ParamValue::Str("20")),
                ("fraction", ParamValue::Str("0.5")),
            ],
        }]),
        fraction_sum: "0.5",
    };
    let result = validate_arm(input);
    let err = result.expect_err("expected a typed rejection");
    assert_eq!(err.code.as_str(), "PROTECTION_PARAM_UNKNOWN");
    assert_eq!(err.message, "unknown parameter(s): sell_fraction");
}

#[test]
fn validation_042() {
    let input = ValidateInput {
        kind: "take_profit",
        params: Some(ParamsInput {
            is_object: true,
            entries: &[
                ("price_usd", ParamValue::Str("1.2")),
                ("sell_fraction", ParamValue::Str("0.5")),
            ],
        }),
        levels: LevelsInput::Absent,
        fraction_sum: "0",
    };
    let result = validate_arm(input);
    let p = result.unwrap();
    assert_eq!(p.kind.as_str(), "take_profit");
    assert_eq!(p.price_usd.as_ref().map(|d| d.as_str()), Some("1.2"));
    assert_eq!(p.loss_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.gain_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_pct.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.trailing_price_usd.as_ref().map(|d| d.as_str()), None);
    assert_eq!(p.sell_fraction.as_str(), "0.5");
    assert_eq!(p.levels.len(), 0);
}

// ---------- evaluate_arm ----------
#[test]
fn evaluation_000_not_armed() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Cancelled,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.5").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "NOT_ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.5".to_owned())),
        ("status".to_owned(), Some("cancelled".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_001_expired() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: Some(0),
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.5").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "EXPIRED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.5".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_002_expiry_naive_now() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: Some(0),
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.5").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "EXPIRED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.5".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_003_price_unknown() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = None;
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "PRICE_UNKNOWN", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), None),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_004_stale() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.85").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: true,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "MARK_STALE", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_005_no_position() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("0").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.85").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "NO_POSITION", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("0".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_006_negative_position() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("-5").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.85").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "NO_POSITION", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("-5".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_007_sl_price_trigger() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.85").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "STOP_LOSS", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.90".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_008_sl_price_at_threshold() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.90").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "STOP_LOSS", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.90".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.90".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_009_sl_price_hold() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: Some(DecimalLexeme::parse("0.90").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("0.95").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.95".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.90".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_010_sl_pct_trigger() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: None,
        loss_pct: Some(DecimalLexeme::parse("8").unwrap()),
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("0.91").unwrap());
    let computed = Some(DecimalLexeme::parse("0.92").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "STOP_LOSS", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.91".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.92".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_011_sl_pct_trigger_now_above() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: None,
        loss_pct: Some(DecimalLexeme::parse("8").unwrap()),
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("0.9199").unwrap());
    let computed = Some(DecimalLexeme::parse("0.92").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "STOP_LOSS", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.9199".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.92".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_012_sl_pct_hold() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: None,
        loss_pct: Some(DecimalLexeme::parse("8").unwrap()),
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("0.93").unwrap());
    let computed = Some(DecimalLexeme::parse("0.92").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.93".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.92".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_013_sl_pct_invalid_arm() {
    let params = ArmParams {
        kind: ArmKind::StopLoss,
        price_usd: None,
        loss_pct: Some(DecimalLexeme::parse("8").unwrap()),
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("0").unwrap();
    let price = Some(DecimalLexeme::parse("0.5").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "INVALID_ARM", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("stop_loss".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("0".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.5".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_014_tp_price_trigger() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: Some(DecimalLexeme::parse("1.2").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("1.25").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.25".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_015_tp_price_hold() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: Some(DecimalLexeme::parse("1.2").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("1.1").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.1".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_016_tp_gain_trigger() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.25").unwrap());
    let computed = Some(DecimalLexeme::parse("1.2").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.25".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_017_tp_gain_at() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.2").unwrap());
    let computed = Some(DecimalLexeme::parse("1.2").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.2".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_018_tp_gain_hold() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.1").unwrap());
    let computed = Some(DecimalLexeme::parse("1.2").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.1".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_019_tp_gain_invalid_arm() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("0").unwrap();
    let price = Some(DecimalLexeme::parse("2").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "INVALID_ARM", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("0".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("2".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_020_tp_partial_sell() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: Some(DecimalLexeme::parse("1.2").unwrap()),
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("0.5").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1.00").unwrap();
    let price = Some(DecimalLexeme::parse("1.3").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0.5", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1.00".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.3".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("1.2".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_021_tp_ladder_level0() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("40").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.25").unwrap());
    let computed = None;
    let lvl_0 = Some(DecimalLexeme::parse("1.2").unwrap());
    let lvl_1 = Some(DecimalLexeme::parse("1.4").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT_LEVEL", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0.5", "sell_fraction");
    assert_eq!(r.level_index, Some(0), "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("level_0_trigger_usd".to_owned(), Some("1.2".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.25".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_022_tp_ladder_level1_only() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("40").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[0];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.45").unwrap());
    let computed = None;
    let lvl_0 = Some(DecimalLexeme::parse("1.2").unwrap());
    let lvl_1 = Some(DecimalLexeme::parse("1.4").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT_LEVEL", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0.5", "sell_fraction");
    assert_eq!(r.level_index, Some(1), "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("level_1_trigger_usd".to_owned(), Some("1.4".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.45".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_023_tp_ladder_hold_both() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("40").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.1").unwrap());
    let computed = None;
    let lvl_0 = Some(DecimalLexeme::parse("1.2").unwrap());
    let lvl_1 = Some(DecimalLexeme::parse("1.4").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("level_0_trigger_usd".to_owned(), Some("1.2".to_owned())),
        ("level_1_trigger_usd".to_owned(), Some("1.4".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.1".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_024_tp_ladder_all_fired() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("40").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[0, 1];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("2").unwrap());
    let computed = None;
    let lvl_0 = Some(DecimalLexeme::parse("1.2").unwrap());
    let lvl_1 = Some(DecimalLexeme::parse("1.4").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("2".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_025_tp_ladder_invalid_arm() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("20").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: None,
                gain_pct: Some(DecimalLexeme::parse("40").unwrap()),
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("0").unwrap();
    let price = Some(DecimalLexeme::parse("2").unwrap());
    let computed = None;
    let lvl_0 = None;
    let lvl_1 = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "INVALID_ARM", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("0".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("2".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_026_tp_ladder_price_level0() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: Some(DecimalLexeme::parse("1.2").unwrap()),
                gain_pct: None,
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: Some(DecimalLexeme::parse("1.4").unwrap()),
                gain_pct: None,
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.25").unwrap());
    let computed = None;
    let lvl_0 = None;
    let lvl_1 = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT_LEVEL", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0.5", "sell_fraction");
    assert_eq!(r.level_index, Some(0), "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("level_0_trigger_usd".to_owned(), Some("1.2".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.25".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_027_tp_ladder_price_level1() {
    let params = ArmParams {
        kind: ArmKind::TakeProfit,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![
            TakeProfitLevel {
                price_usd: Some(DecimalLexeme::parse("1.2").unwrap()),
                gain_pct: None,
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
            TakeProfitLevel {
                price_usd: Some(DecimalLexeme::parse("1.4").unwrap()),
                gain_pct: None,
                fraction: DecimalLexeme::parse("0.5").unwrap(),
            },
        ],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("1").unwrap();
    let price = Some(DecimalLexeme::parse("1.45").unwrap());
    let computed = None;
    let lvl_0 = None;
    let lvl_1 = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[lvl_0.as_ref(), lvl_1.as_ref()];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TAKE_PROFIT_LEVEL", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0.5", "sell_fraction");
    assert_eq!(r.level_index, Some(0), "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("take_profit".to_owned())),
        ("level_0_trigger_usd".to_owned(), Some("1.2".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("1".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("1.45".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_028_trail_submerged_9() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("9").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("9".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_029_trail_submerged_8_5() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("8.5").unwrap());
    let computed = None;
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), None, "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), None),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("8.5".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_030_trail_above_water_12() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("12").unwrap());
    let computed = Some(DecimalLexeme::parse("11.40").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("12"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("12".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("12".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("11.40".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_031_trail_trigger_from_peak() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = Some(DecimalLexeme::parse("12").unwrap());
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("10").unwrap());
    let computed = Some(DecimalLexeme::parse("11.40").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TRAILING_STOP", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("12"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("12".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("10".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("11.40".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_032_trail_hold_from_peak() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = Some(DecimalLexeme::parse("12").unwrap());
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("11.5").unwrap());
    let computed = Some(DecimalLexeme::parse("11.40").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("12"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("12".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("11.5".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("11.40".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_033_trail_peak_carry() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = Some(DecimalLexeme::parse("12").unwrap());
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("13").unwrap());
    let computed = Some(DecimalLexeme::parse("12.35").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("13"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("13".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("13".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("12.35".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_034_trail_dist_trigger() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: Some(DecimalLexeme::parse("2").unwrap()),
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = Some(DecimalLexeme::parse("12").unwrap());
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("10").unwrap());
    let computed = Some(DecimalLexeme::parse("10").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "trigger", "action");
    assert_eq!(r.reason.as_str(), "TRAILING_STOP", "reason");
    assert_eq!(r.sell_fraction.as_str(), "1", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("12"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("12".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("10".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("10".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_035_trail_dist_hold() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: None,
        trailing_price_usd: Some(DecimalLexeme::parse("2").unwrap()),
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = Some(DecimalLexeme::parse("12").unwrap());
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("10").unwrap();
    let price = Some(DecimalLexeme::parse("10.1").unwrap());
    let computed = Some(DecimalLexeme::parse("10").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(r.peak_usd.as_ref().map(|d| d.as_str()), Some("12"), "peak");
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("12".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("10".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("10.1".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("10".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}

#[test]
fn evaluation_036_trail_no_cost_above() {
    let params = ArmParams {
        kind: ArmKind::TrailingStop,
        price_usd: None,
        loss_pct: None,
        gain_pct: None,
        trailing_pct: Some(DecimalLexeme::parse("5").unwrap()),
        trailing_price_usd: None,
        sell_fraction: DecimalLexeme::parse("1").unwrap(),
        levels: vec![],
    };
    let peak = None;
    let triggered: &[usize] = &[];
    let arm = ProtectionArm {
        status: ArmStatus::Armed,
        params,
        peak_usd: peak.clone(),
        triggered_level_indexes: triggered,
        expires_at_ns: None,
    };
    let qty = DecimalLexeme::parse("100").unwrap();
    let cost = DecimalLexeme::parse("0").unwrap();
    let price = Some(DecimalLexeme::parse("0.85").unwrap());
    let computed = Some(DecimalLexeme::parse("0.8075").unwrap());
    let level_triggers: &[Option<&DecimalLexeme>] = &[];
    let input = EvaluateInput {
        price_usd: price.as_ref(),
        position_quantity: &qty,
        position_avg_cost_usd: &cost,
        stale: false,
        now_ns: Some(0),
        computed_trigger_price: computed.as_ref(),
        computed_level_triggers: level_triggers,
    };
    let r = evaluate_arm(&arm, &input);
    assert_eq!(r.action.as_str(), "hold", "action");
    assert_eq!(r.reason.as_str(), "ARMED", "reason");
    assert_eq!(r.sell_fraction.as_str(), "0", "sell_fraction");
    assert_eq!(r.level_index, None, "level_index");
    assert_eq!(
        r.peak_usd.as_ref().map(|d| d.as_str()),
        Some("0.85"),
        "peak"
    );
    let expected: BTreeMap<String, Option<String>> = [
        ("kind".to_owned(), Some("trailing_stop".to_owned())),
        ("peak_usd".to_owned(), Some("0.85".to_owned())),
        ("position_avg_cost_usd".to_owned(), Some("0".to_owned())),
        ("position_quantity".to_owned(), Some("100".to_owned())),
        ("price_usd".to_owned(), Some("0.85".to_owned())),
        ("status".to_owned(), Some("armed".to_owned())),
        ("trigger_price_usd".to_owned(), Some("0.8075".to_owned())),
    ]
    .into_iter()
    .collect();
    assert_eq!(r.inputs, expected, "replay inputs");
}
