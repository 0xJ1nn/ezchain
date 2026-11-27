//! Recorded cases from the Python oracle `ezchain_core.strategy` (scratch harness)
//! and its tests in `tests/engine/test_strategy_evaluator.py` and
//! `tests/graph/test_strategy_graph.py`.
//!
//! Every branch of `_interval`, `_lookback`, `_reject_unknown`, `validate_params` and
//! `interval_minutes` is recorded here with the exact code and message the Python original
//! produces. ADR-0049/0050 parity gate: these cases are the oracle until Rust passes them.

use std::collections::BTreeSet;

use ezchain_core::strategy_params::{
    interval, interval_minutes, lookback, reject_unknown, validate_params, Lookback, ParamValue,
    Params, ParamsInput, StrategyValidationError, ValidatedParams,
};

fn s(value: &str) -> ParamValue {
    ParamValue::Str(value.to_string())
}

fn i(value: i64) -> ParamValue {
    ParamValue::Int(value)
}

fn params(items: &[(&str, ParamValue)]) -> Params {
    items
        .iter()
        .map(|(key, value)| ((*key).to_string(), value.clone()))
        .collect()
}

fn assert_err<T: std::fmt::Debug>(
    result: Result<T, StrategyValidationError>,
    code: &str,
    message: &str,
) {
    let error = result.expect_err("expected the recorded rejection");
    assert_eq!(error.code, code);
    assert_eq!(error.message, message);
}

const RANGE: &str = "STRATEGY_PARAM_RANGE";
const TYPE: &str = "STRATEGY_PARAM_TYPE";
const MISSING: &str = "STRATEGY_PARAM_MISSING";
const UNKNOWN: &str = "STRATEGY_PARAM_UNKNOWN";
const LOOKBACK_ERR: &str = "STRATEGY_LOOKBACK_UNSUPPORTED";
const KIND_ERR: &str = "STRATEGY_KIND_UNSUPPORTED";
const GRAPH_ERR: &str = "STRATEGY_GRAPH_INVALID";

fn out_of_range() -> &'static str {
    "interval_minutes must be between 1 and 10080"
}

// ---- _interval -------------------------------------------------------------

#[test]
fn interval_defaults_to_sixty_when_missing_or_null() {
    assert_eq!(interval(&Params::new()), Ok(60));
    assert_eq!(
        interval(&params(&[("interval_minutes", ParamValue::Null)])),
        Ok(60)
    );
}

#[test]
fn interval_accepts_the_inclusive_boundaries_one_and_10080() {
    assert_eq!(interval(&params(&[("interval_minutes", i(1))])), Ok(1));
    assert_eq!(interval(&params(&[("interval_minutes", i(60))])), Ok(60));
    assert_eq!(
        interval(&params(&[("interval_minutes", i(10_080))])),
        Ok(10_080)
    );
}

#[test]
fn interval_rejects_values_outside_one_to_10080() {
    for value in [0, -1, 10_081] {
        assert_err(
            interval(&params(&[("interval_minutes", i(value))])),
            RANGE,
            out_of_range(),
        );
    }
    // A huge but integral exponent is still an integer, then out of range.
    assert_err(
        interval(&params(&[("interval_minutes", s("1e999"))])),
        RANGE,
        out_of_range(),
    );
}

#[test]
fn interval_rejects_non_integral_values() {
    for value in [s("1.5"), s("60.5"), s("1e-2"), s(".5"), s("1_0.5_0")] {
        assert_err(
            interval(&params(&[("interval_minutes", value)])),
            RANGE,
            "interval_minutes must be a whole number of minutes",
        );
    }
    assert_err(
        interval(&params(&[("interval_minutes", ParamValue::Float(1.5))])),
        RANGE,
        "interval_minutes must be a whole number of minutes",
    );
}

#[test]
fn interval_parses_the_same_decimal_lexemes_as_python() {
    // Rust's f64 `to_string` spells an integral float `60` where Python spells it `60.0`;
    // both parses give the same value, as the recorded Python case requires.
    assert_eq!(
        interval(&params(&[("interval_minutes", ParamValue::Float(60.0))])),
        Ok(60)
    );
    for (text, expected) in [
        ("60", 60),
        ("60.0", 60),
        ("60.00", 60),
        ("1e2", 100),
        ("1E+2", 100),
        ("+60", 60),
        ("5.", 5),
        (" 60 ", 60),
        // `Decimal` ignores underscores anywhere, even repeated or leading.
        ("1_0", 10),
        ("_10", 10),
        ("1__0", 10),
        ("1_0e1", 100),
    ] {
        assert_eq!(
            interval(&params(&[("interval_minutes", s(text))])),
            Ok(expected),
            "lexeme {text}"
        );
    }
}

#[test]
fn interval_rejects_bad_and_non_finite_numbers() {
    assert_err(
        interval(&params(&[("interval_minutes", s("abc"))])),
        TYPE,
        "interval_minutes must be a number",
    );
    assert_err(
        interval(&params(&[("interval_minutes", s("60,0"))])),
        TYPE,
        "interval_minutes must be a number",
    );
    assert_err(
        interval(&params(&[("interval_minutes", ParamValue::Bool(true))])),
        TYPE,
        "interval_minutes must be a number, got bool",
    );
    for value in [
        ParamValue::Float(f64::NAN),
        ParamValue::Float(f64::INFINITY),
        s("NaN"),
    ] {
        assert_err(
            interval(&params(&[("interval_minutes", value)])),
            TYPE,
            "interval_minutes must be finite",
        );
    }
}

// ---- _lookback -------------------------------------------------------------

#[test]
fn lookback_defaults_to_one_hour_and_accepts_all_four_windows() {
    assert_eq!(lookback(&Params::new()), Ok(Lookback::H1));
    for (text, expected) in [
        ("5m", Lookback::M5),
        ("1h", Lookback::H1),
        ("6h", Lookback::H6),
        ("24h", Lookback::H24),
    ] {
        assert_eq!(lookback(&params(&[("lookback", s(text))])), Ok(expected));
    }
    assert_eq!(Lookback::M5.as_str(), "5m");
    assert_eq!(Lookback::H24.as_str(), "24h");
}

#[test]
fn lookback_rejects_every_unsupported_value_including_explicit_none() {
    let message = "lookback must be one of: 5m, 1h, 6h, 24h";
    for value in [s("1d"), s("1H"), s("24H"), s(""), ParamValue::Null, i(5)] {
        assert_err(
            lookback(&params(&[("lookback", value)])),
            LOOKBACK_ERR,
            message,
        );
    }
}

// ---- _reject_unknown -------------------------------------------------------

#[test]
fn reject_unknown_accepts_a_subset_of_the_allowed_keys() {
    let allowed: BTreeSet<&str> = ["a", "b"].into_iter().collect();
    assert_eq!(reject_unknown(&params(&[("a", i(1))]), &allowed), Ok(()));
}

#[test]
fn reject_unknown_lists_every_unknown_key_sorted() {
    let allowed: BTreeSet<&str> = ["a", "b"].into_iter().collect();
    assert_err(
        reject_unknown(&params(&[("z", i(1))]), &allowed),
        UNKNOWN,
        "unknown parameter(s): z",
    );
    assert_err(
        reject_unknown(&params(&[("z", i(1)), ("y", i(2)), ("a", i(3))]), &allowed),
        UNKNOWN,
        "unknown parameter(s): y, z",
    );
    assert_err(
        reject_unknown(
            &params(&[("b", i(1)), ("a", i(2)), ("c", i(3))]),
            &BTreeSet::new(),
        ),
        UNKNOWN,
        "unknown parameter(s): a, b, c",
    );
}

// ---- validate_params: defaults ---------------------------------------------

#[test]
fn defaults_match_the_python_typed_params() {
    let momentum = validate_params("momentum", &Params::new()).unwrap();
    let ValidatedParams::Momentum(p) = momentum else {
        panic!("momentum kind");
    };
    assert_eq!(p.shared.interval_minutes, 60);
    assert_eq!(p.shared.order_usd.as_str(), "100");
    assert_eq!(p.shared.max_position_usd.as_str(), "500");
    assert_eq!(p.lookback, Lookback::H1);
    assert_eq!(p.entry_change_pct.as_str(), "5");
    assert_eq!(p.exit_change_pct.as_str(), "-3");
    assert_eq!(p.take_profit_pct.as_str(), "20");
    assert_eq!(p.stop_loss_pct.as_str(), "8");

    let dip = validate_params("dip", &Params::new()).unwrap();
    let ValidatedParams::Dip(p) = dip else {
        panic!("dip kind");
    };
    assert_eq!(p.lookback, Lookback::H1);
    assert_eq!(p.entry_change_pct.as_str(), "5");

    let dca = validate_params("dca", ParamsInput::None).unwrap();
    assert_eq!(dca.interval_minutes(), 60);
    assert_eq!(dca.shared().order_usd.as_str(), "100");
    assert_eq!(dca.shared().max_position_usd.as_str(), "500");

    let plugin = validate_params("plugin", &Params::new()).unwrap();
    let ValidatedParams::Plugin(p) = plugin else {
        panic!("plugin kind");
    };
    assert!(p.config.is_empty());
}

#[test]
fn an_interval_set_in_any_kind_is_reported_by_interval_minutes() {
    let p = params(&[("interval_minutes", i(15))]);
    assert_eq!(interval_minutes("dca", &p), Ok(15));
    assert_eq!(interval_minutes("momentum", &Params::new()), Ok(60));
    assert_eq!(
        interval_minutes("dca", &params(&[("interval_minutes", i(0))])),
        Err(StrategyValidationError {
            code: RANGE,
            message: out_of_range().to_string(),
        })
    );
}

// ---- validate_params: the recorded rejection matrix ------------------------

type MomentumCase<'a> = (&'a [(&'a str, ParamValue)], &'a str, &'a str);

#[test]
fn momentum_rejects_the_recorded_invalid_params() {
    let cases: &[MomentumCase<'_>] = &[
        (
            &[("lookback", s("1d"))],
            LOOKBACK_ERR,
            "lookback must be one of: 5m, 1h, 6h, 24h",
        ),
        (
            &[("entry_change_pct", i(0))],
            RANGE,
            "entry_change_pct must be > 0",
        ),
        (
            &[("exit_change_pct", i(3))],
            RANGE,
            "exit_change_pct must be < 0 for momentum",
        ),
        (
            &[("exit_change_pct", i(0))],
            RANGE,
            "exit_change_pct must be < 0 for momentum",
        ),
        (
            &[("stop_los_pct", i(8))],
            UNKNOWN,
            "unknown parameter(s): stop_los_pct",
        ),
        (
            &[("stop_loss_pct", i(0))],
            RANGE,
            "stop_loss_pct must be > 0",
        ),
        (
            &[("order_usd", ParamValue::Bool(true))],
            TYPE,
            "order_usd must be a number, got bool",
        ),
        // Shared fields are parsed before unknown-key rejection.
        (
            &[("order_usd", i(-1)), ("stop_los_pct", i(8))],
            RANGE,
            "order_usd must be > 0",
        ),
    ];
    for (items, code, message) in cases {
        assert_err(validate_params("momentum", &params(items)), code, message);
    }
    assert!(validate_params("momentum", &params(&[("exit_change_pct", i(-3))])).is_ok());
}

#[test]
fn dip_and_dca_reject_the_recorded_invalid_params() {
    assert_err(
        validate_params("dip", &params(&[("order_usd", i(-1))])),
        RANGE,
        "order_usd must be > 0",
    );
    assert_err(
        validate_params("dca", &params(&[("interval_minutes", i(0))])),
        RANGE,
        out_of_range(),
    );
    assert_err(
        validate_params(
            "dca",
            &params(&[("interval_minutes", ParamValue::Float(1.5))]),
        ),
        RANGE,
        "interval_minutes must be a whole number of minutes",
    );
    assert_eq!(
        interval_minutes("dca", &params(&[("interval_minutes", i(10_080))])),
        Ok(10_080)
    );
    assert_err(
        validate_params("dca", &params(&[("interval_minutes", i(10_081))])),
        RANGE,
        out_of_range(),
    );
    assert_err(
        validate_params("dca", &params(&[("max_position_usd", s("abc"))])),
        TYPE,
        "max_position_usd must be a number",
    );
}

#[test]
fn threshold_requires_and_orders_both_bounds() {
    assert_err(
        validate_params("threshold", &Params::new()),
        MISSING,
        "buy_below_usd is required",
    );
    assert_err(
        validate_params("threshold", &params(&[("buy_below_usd", i(2))])),
        MISSING,
        "sell_above_usd is required",
    );
    assert_err(
        validate_params(
            "threshold",
            &params(&[("buy_below_usd", i(-1)), ("sell_above_usd", i(3))]),
        ),
        RANGE,
        "buy_below_usd must be > 0",
    );
    assert_err(
        validate_params(
            "threshold",
            &params(&[("buy_below_usd", i(2)), ("sell_above_usd", i(2))]),
        ),
        RANGE,
        "sell_above_usd must be > buy_below_usd",
    );
    // Exact decimal comparison, including exponent spellings.
    assert_err(
        validate_params(
            "threshold",
            &params(&[("buy_below_usd", s("1e2")), ("sell_above_usd", s("1E+2"))]),
        ),
        RANGE,
        "sell_above_usd must be > buy_below_usd",
    );
    assert!(validate_params(
        "threshold",
        &params(&[("buy_below_usd", s("1.5")), ("sell_above_usd", s("1.6"))])
    )
    .is_ok());
    let valid = validate_params(
        "threshold",
        &params(&[("buy_below_usd", i(2)), ("sell_above_usd", i(3))]),
    )
    .unwrap();
    let ValidatedParams::Threshold(p) = valid else {
        panic!("threshold kind");
    };
    assert_eq!(p.buy_below_usd.as_str(), "2");
    assert_eq!(p.sell_above_usd.as_str(), "3");
    assert_err(
        validate_params(
            "threshold",
            &params(&[
                ("buy_below_usd", i(1)),
                ("sell_above_usd", i(2)),
                ("timeout", i(5)),
            ]),
        ),
        UNKNOWN,
        "unknown parameter(s): timeout",
    );
}

#[test]
fn plugin_validates_config_shape_and_unknown_keys() {
    let with_config = params(&[("config", ParamValue::Object(Params::new()))]);
    assert!(validate_params("plugin", &with_config).is_ok());
    assert_err(
        validate_params("plugin", &params(&[("config", s("not an object"))])),
        TYPE,
        "config must be an object",
    );
    assert_err(
        validate_params("plugin", &params(&[("config", ParamValue::Null)])),
        TYPE,
        "config must be an object",
    );
    assert_err(
        validate_params(
            "plugin",
            &params(&[("configs", ParamValue::Object(Params::new()))]),
        ),
        UNKNOWN,
        "unknown parameter(s): configs",
    );
}

/// An opaque graph document. Rust never inspects it: Python's `parse_document` and
/// `validate_graph` own document validity (see the module docs).
fn graph_doc() -> ParamValue {
    ParamValue::Object(Params::from([("schema".to_string(), s("ezchain.graph/1"))]))
}

#[test]
fn graph_defers_the_document_but_checks_scalar_fields() {
    assert_err(
        validate_params("graph", &Params::new()),
        GRAPH_ERR,
        "graph is required",
    );
    assert_err(
        validate_params("graph", &params(&[("graph", ParamValue::Null)])),
        GRAPH_ERR,
        "graph is required",
    );
    assert_err(
        validate_params("graph", &params(&[("graph", graph_doc()), ("bogus", i(1))])),
        UNKNOWN,
        "unknown parameter(s): bogus",
    );
    assert_err(
        validate_params(
            "graph",
            &params(&[("graph", graph_doc()), ("interval_minutes", i(0))]),
        ),
        RANGE,
        out_of_range(),
    );
    // Python returns 30 here when the document is valid; Rust checks the scalar part and
    // defers the document check to the Python validator.
    assert_eq!(
        interval_minutes(
            "graph",
            &params(&[("graph", graph_doc()), ("interval_minutes", i(30))])
        ),
        Ok(30)
    );
    let deferred = validate_params("graph", &params(&[("graph", graph_doc())])).unwrap();
    let ValidatedParams::GraphDeferred(p) = deferred else {
        panic!("graph kind stays deferred to the Python validator");
    };
    assert_eq!(p.document, graph_doc());
    assert_eq!(p.shared.interval_minutes, 60);
}

#[test]
fn unsupported_kind_and_non_object_params_are_rejected() {
    assert_err(
        validate_params("grid", &Params::new()),
        KIND_ERR,
        "unsupported kind: grid",
    );
    for type_name in ["list", "str", "int", "bool"] {
        assert_err(
            validate_params("dca", ParamsInput::Other(type_name)),
            TYPE,
            "params must be an object",
        );
    }
    let extra = params(&[("order_usd", ParamValue::Float(f64::NAN))]);
    assert_err(
        validate_params("dca", &extra),
        TYPE,
        "order_usd must be finite",
    );
    assert_err(
        validate_params("dca", &params(&[("order_usd", s("NaN"))])),
        TYPE,
        "order_usd must be finite",
    );
    assert_err(
        validate_params("dca", &params(&[("order_usd", ParamValue::Array)])),
        TYPE,
        "order_usd must be a number, got list",
    );
    assert_err(
        validate_params(
            "dca",
            &params(&[("order_usd", ParamValue::Object(Params::new()))]),
        ),
        TYPE,
        "order_usd must be a number, got dict",
    );
    assert_err(
        validate_params(
            "dca",
            &params(&[("order_usd", ParamValue::Other("tuple".to_string()))]),
        ),
        TYPE,
        "order_usd must be a number, got tuple",
    );
}
