//! JSON-safety normalisation, ported from `ezchain_api.errors.json_safe`.
//!
//! The Python original makes a rejected value serialisable before it is echoed in a 422
//! validation envelope. It is pure: no request, database or money arithmetic. This module
//! records every branch of that function as cases (ADR-0049/0050 `record → diff → gate`).
//!
//! Two behaviours are load-bearing and easy to get wrong:
//!
//! 1. Non-finite Python floats stringify as `inf`, `-inf`, `nan` (lowercase). Rust's
//!    `Display` for `f64::NAN` is `NaN`, so the conversion is explicit rather than
//!    `to_string()`.
//! 2. **Every** `Decimal` becomes a string, finite or not. The non-finite branch returns
//!    `str(value)`, but a finite `Decimal` is not one of the primitive scalar types in the
//!    later `isinstance` check, so it falls through to the same `str(value)` tail. A finite
//!    `Decimal("1.25")` therefore serialises as `"1.25"`, not as a JSON number.
//!
//! The Rust value model mirrors the Python types the function distinguishes. `Object` keys
//! are values because Python coerces them with `str(key)`; `Decimal` and `Other` carry the
//! exact `str()` text so no float/decimal formatting is invented here.

/// A JSON-shaped value, mirroring the Python types `json_safe` inspects.
#[derive(Debug, Clone, PartialEq)]
pub enum JsonValue {
    /// Python `None`.
    Null,
    /// Python `bool`.
    Bool(bool),
    /// Python `int`.
    Int(i64),
    /// Python `float` (finite or non-finite).
    Float(f64),
    /// Python `str`.
    Str(String),
    /// Python `list` or `tuple` — both serialise to a JSON array.
    Array(Vec<JsonValue>),
    /// Python `dict`; keys are coerced with `str(key)` by `json_safe`.
    Object(Vec<(JsonValue, JsonValue)>),
    /// Python `decimal.Decimal`, carrying its exact `str()` text (finite or not).
    Decimal(String),
    /// Any other Python object, carrying its exact `str()` text.
    Other(String),
}

/// Python `str()` of a float, for the non-finite values `json_safe` stringifies.
fn py_float_str(value: f64) -> String {
    if value.is_nan() {
        "nan".to_string()
    } else if value == f64::INFINITY {
        "inf".to_string()
    } else if value == f64::NEG_INFINITY {
        "-inf".to_string()
    } else {
        value.to_string()
    }
}

/// Python `str()` for the scalar keys an error envelope can carry.
fn py_str(value: &JsonValue) -> String {
    match value {
        JsonValue::Null => "None".to_string(),
        JsonValue::Bool(true) => "True".to_string(),
        JsonValue::Bool(false) => "False".to_string(),
        JsonValue::Int(i) => i.to_string(),
        JsonValue::Float(f) => py_float_str(*f),
        JsonValue::Str(s) | JsonValue::Decimal(s) | JsonValue::Other(s) => s.clone(),
        // Composite keys do not occur in the validation envelopes this port serves; the
        // representation below keeps them deterministic rather than inventing a new type.
        JsonValue::Array(items) => {
            format!(
                "[{}]",
                items.iter().map(py_repr).collect::<Vec<_>>().join(", ")
            )
        }
        JsonValue::Object(items) => format!(
            "{{{}}}",
            items
                .iter()
                .map(|(k, v)| format!("{}: {}", py_repr(k), py_repr(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
    }
}

/// A Python-ish `repr` used only when a composite value appears as a dict key.
fn py_repr(value: &JsonValue) -> String {
    match value {
        JsonValue::Str(s) => format!("'{}'", s),
        _ => py_str(value),
    }
}

/// Make a value safe to embed in a JSON response, exactly as `json_safe` does.
pub fn json_safe(value: &JsonValue) -> JsonValue {
    match value {
        JsonValue::Float(f) if !f.is_finite() => JsonValue::Str(py_float_str(*f)),
        // A finite Decimal reaches this tail because Decimal is not a primitive scalar;
        // a non-finite Decimal reaches it via the dedicated non-finite branch. Same result.
        JsonValue::Decimal(s) | JsonValue::Other(s) => JsonValue::Str(s.clone()),
        JsonValue::Array(items) => JsonValue::Array(items.iter().map(json_safe).collect()),
        JsonValue::Object(items) => JsonValue::Object(
            items
                .iter()
                .map(|(key, item)| (JsonValue::Str(py_str(key)), json_safe(item)))
                .collect(),
        ),
        other => other.clone(),
    }
}

#[cfg(test)]
mod tests {
    use super::{json_safe, JsonValue};

    // Recorded Python cases: (label, input, json_safe(input)).
    // Source: ezchain_api.errors.json_safe, run against the worktree oracle with
    // the project venv (Python 3.11). Expected values are that function's outputs.
    fn check(label: &str, input: JsonValue, expected: JsonValue) {
        assert_eq!(json_safe(&input), expected, "recorded case {label}");
    }

    fn s(value: &str) -> JsonValue {
        JsonValue::Str(value.to_string())
    }

    #[test]
    fn non_finite_floats_use_lowercase_python_spelling() {
        check("inf", JsonValue::Float(f64::INFINITY), s("inf"));
        check("-inf", JsonValue::Float(f64::NEG_INFINITY), s("-inf"));
        check("nan", JsonValue::Float(f64::NAN), s("nan"));
    }

    #[test]
    fn every_decimal_becomes_a_string_even_when_finite() {
        // The finite case is the subtle one: Decimal is not a primitive scalar in Python,
        // so it falls through to str(value) just like the non-finite case.
        check("Decimal NaN", JsonValue::Decimal("NaN".into()), s("NaN"));
        check(
            "Decimal Infinity",
            JsonValue::Decimal("Infinity".into()),
            s("Infinity"),
        );
        check(
            "Decimal -Infinity",
            JsonValue::Decimal("-Infinity".into()),
            s("-Infinity"),
        );
        check(
            "Decimal finite",
            JsonValue::Decimal("1.25".into()),
            s("1.25"),
        );
    }

    #[test]
    fn primitive_scalars_pass_through_unchanged() {
        check("None", JsonValue::Null, JsonValue::Null);
        check("str", s("x"), s("x"));
        check("int", JsonValue::Int(7), JsonValue::Int(7));
        check("bool true", JsonValue::Bool(true), JsonValue::Bool(true));
        check("bool false", JsonValue::Bool(false), JsonValue::Bool(false));
        check("finite float", JsonValue::Float(1.5), JsonValue::Float(1.5));
    }

    #[test]
    fn arbitrary_objects_use_their_str() {
        check("object", JsonValue::Other("Weird!".into()), s("Weird!"));
    }

    #[test]
    fn tuples_and_lists_become_arrays_and_recurse() {
        check(
            "tuple with inf",
            JsonValue::Array(vec![JsonValue::Int(1), JsonValue::Float(f64::INFINITY)]),
            JsonValue::Array(vec![JsonValue::Int(1), s("inf")]),
        );
        check(
            "list with nan",
            JsonValue::Array(vec![JsonValue::Float(f64::NAN), s("a")]),
            JsonValue::Array(vec![s("nan"), s("a")]),
        );
    }

    #[test]
    fn dict_keys_are_stringified_and_values_recurse() {
        check(
            "dict with int key",
            JsonValue::Object(vec![
                (JsonValue::Int(1), JsonValue::Float(f64::INFINITY)),
                (
                    s("x"),
                    JsonValue::Array(vec![JsonValue::Decimal("NaN".into())]),
                ),
            ]),
            JsonValue::Object(vec![
                (s("1"), s("inf")),
                (s("x"), JsonValue::Array(vec![s("NaN")])),
            ]),
        );
    }

    #[test]
    fn nested_structures_recurse_to_fixed_point() {
        check(
            "nested dict/tuple/-inf",
            JsonValue::Object(vec![(
                s("a"),
                JsonValue::Object(vec![(
                    s("b"),
                    JsonValue::Array(vec![JsonValue::Float(f64::NEG_INFINITY)]),
                )]),
            )]),
            JsonValue::Object(vec![(
                s("a"),
                JsonValue::Object(vec![(s("b"), JsonValue::Array(vec![s("-inf")]))]),
            )]),
        );
    }

    #[test]
    fn json_safe_is_idempotent() {
        // json_safe(json_safe(x)) == json_safe(x) — it reaches a fixed point in one pass.
        let input = JsonValue::Object(vec![
            (JsonValue::Int(2), JsonValue::Float(f64::NAN)),
            (s("d"), JsonValue::Decimal("1.25".into())),
        ]);
        let once = json_safe(&input);
        assert_eq!(json_safe(&once), once);
    }
}
