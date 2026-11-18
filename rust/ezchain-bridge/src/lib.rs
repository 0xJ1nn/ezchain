//! In-process PyO3 bridge from Python callers to already-ported Rust kernels.
//!
//! The bridge is deliberately thin: it converts Python values to kernel inputs, calls
//! the pure kernel, and converts the result (or its typed error) back. It owns no
//! behaviour of its own. The Python implementation stays the behavioural oracle until
//! the whole surface has passed parity (ADR-0049/0050/0052).
//!
//! Money values cross this boundary only as strings (decimal spellings) or ints; a
//! Python float is refused at the boundary with the original `FloatMoneyError`. The
//! money kernels are pure decimal arithmetic: no I/O, no keys, no ledger writes.
//! Python remains the oracle and the ledger remains the sole accountant.

#![forbid(unsafe_code)]

use std::cmp::Ordering;

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyFloat, PyInt, PyString, PyTuple, PyType};

use ezchain_decimal::{
    compare as compare_kernel, floor_money as floor_money_kernel,
    from_minor_units as from_minor_units_kernel, from_raw as from_raw_kernel,
    quantize_money as quantize_money_kernel, to_minor_units as to_minor_units_kernel,
    to_raw as to_raw_kernel, ConversionError, Decimal, MinorUnits, MoneyError, ParseError,
};
use ezchain_rpc::scopes::ScopeError;

const MONEY_MODULE: &str = "ezchain_core.money";
const ADAPTER_ERRORS_MODULE: &str = "ezchain_adapters.errors";
const DECIMAL_MODULE: &str = "decimal";
const NOT_FOUND_MESSAGE: &str = "unknown token decimals";

/// The stable reason code a stale read of `source_class` reports.
///
/// Mirrors `ezchain_core.freshness.stale_reason` for Python callers.
#[pyfunction]
#[pyo3(signature = (source_class))]
fn stale_reason(source_class: &str) -> &'static str {
    ezchain_core::freshness::stale_reason(source_class)
}

/// The `(code, message)` pair the Python original passes to `ApiKeyError`.
///
/// Kept separate from the FFI code so the exact strings are covered by Rust unit tests
/// without needing a Python interpreter.
fn api_key_error_parts(error: &ScopeError) -> (&'static str, String) {
    match error {
        ScopeError::Required => (
            "API_KEY_SCOPES_REQUIRED",
            "at least one scope is required".to_owned(),
        ),
        ScopeError::Unknown(unknown) => (
            "API_KEY_SCOPE_UNKNOWN",
            format!(
                "unknown scope(s): {}; allowed: admin, read, trade",
                unknown.join(", ")
            ),
        ),
    }
}

/// Raise the *original* `ApiKeyError` class, not a look-alike.
///
/// The class lives in the Python module that owns the oracle. Importing it here keeps
/// `except ApiKeyError` and the exception's `code`/`message` attributes identical for
/// callers. A missing module is a construction failure, not a silent substitute: the
/// bridge reports it as a `RuntimeError` naming the PYTHONPATH requirement.
fn api_key_error(py: Python<'_>, error: ScopeError) -> PyErr {
    let (code, message) = api_key_error_parts(&error);
    match py
        .import("ezchain_api.services.api_keys")
        .and_then(|module| module.getattr("ApiKeyError"))
        .and_then(|class| Ok(class.cast_into::<PyType>()?))
    {
        Ok(class) => PyErr::from_type(class, (code, message)),
        Err(import_error) => PyRuntimeError::new_err(format!(
            "ezchain-bridge cannot import ezchain_api.services.api_keys.ApiKeyError \
             to report {code} ({message}); set PYTHONPATH to the app's src directories: \
             {import_error}"
        )),
    }
}

/// Normalise a scope grant, returning a tuple and raising `ApiKeyError` on refusal.
///
/// Mirrors `ezchain_api.services.api_keys.normalize_scopes` for Python callers.
#[pyfunction]
#[pyo3(signature = (scopes=None))]
fn normalize_scopes<'py>(
    py: Python<'py>,
    scopes: Option<&Bound<'py, PyAny>>,
) -> PyResult<Bound<'py, PyTuple>> {
    let grants: Option<Vec<String>> = match scopes {
        None => None,
        Some(value) if value.is_none() => None,
        Some(value) => Some(value.extract::<Vec<String>>()?),
    };
    match ezchain_rpc::scopes::normalize_scopes(grants.as_deref()) {
        Ok(normalized) => Ok(PyTuple::new(py, normalized)?),
        Err(error) => Err(api_key_error(py, error)),
    }
}

// ---------------------------------------------------------------------------
// Money boundary: Python values -> kernel inputs, typed errors back.
// ---------------------------------------------------------------------------

/// Construct a Python exception of an imported class, or fail closed with a
/// `RuntimeError` naming the PYTHONPATH requirement if the oracle module is missing.
///
/// Keeping this in one place means every money refusal raises the *original*
/// exception class (so `except MoneyError` / `except NotFound` keeps working), never a
/// look-alike defined by the bridge.
fn py_error(py: Python<'_>, module: &str, class_name: &str, message: &str) -> PyErr {
    match py
        .import(module)
        .and_then(|module| module.getattr(class_name))
        .and_then(|class| Ok(class.cast_into::<PyType>()?))
    {
        Ok(class) => PyErr::from_type(class, (message.to_owned(),)),
        Err(import_error) => PyRuntimeError::new_err(format!(
            "ezchain-bridge cannot import {module}.{class_name} to report {message}; \
             set PYTHONPATH to the app's src directories: {import_error}"
        )),
    }
}

/// Python type name as `type(value).__name__` spells it (e.g. `NoneType`).
fn type_name(value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(value.get_type().name()?.to_cow()?.into_owned())
}

/// `(module, class, message)` for a kernel `MoneyError` at a money boundary.
///
/// The strings are the exact class paths and `str(exc)` recorded from `money.py`, so a
/// Python caller sees no semantic change. Kept separate from the FFI so Rust tests can
/// assert the messages without a Python interpreter.
fn money_error_parts(
    error: &MoneyError,
    field: &str,
    amount: &str,
) -> (&'static str, &'static str, String) {
    match error {
        MoneyError::NonFinite => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field} must be finite"),
        ),
        MoneyError::BoolInput => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field} must be exact Decimal, not bool"),
        ),
        MoneyError::FloatInput => (
            MONEY_MODULE,
            "FloatMoneyError",
            format!(
                "{field} must be exact Decimal, not float; a float has already lost the amount"
            ),
        ),
        MoneyError::WrongType => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field} must be Decimal or int, not str"),
        ),
        MoneyError::SubQuantum => (
            MONEY_MODULE,
            "SubQuantumError",
            format!("{field}={amount} is not a whole number of 1E-8 minor units"),
        ),
        MoneyError::EmptyProduct => (
            MONEY_MODULE,
            "MoneyError",
            "exact_product needs at least one factor".to_owned(),
        ),
        MoneyError::DivisionByZero => (
            DECIMAL_MODULE,
            "DivisionByZero",
            "[<class 'decimal.DivisionByZero'>]".to_owned(),
        ),
        MoneyError::NegativeFeeBps => (
            "builtins",
            "ValueError",
            "fee_bps cannot be negative".to_owned(),
        ),
        MoneyError::NonPositiveNotional => (
            "builtins",
            "ValueError",
            "notional must be positive".to_owned(),
        ),
        MoneyError::Overflow => (
            DECIMAL_MODULE,
            "Overflow",
            "[<class 'decimal.Overflow'>]".to_owned(),
        ),
        MoneyError::InvalidOperation => (
            DECIMAL_MODULE,
            "InvalidOperation",
            "[<class 'decimal.InvalidOperation'>]".to_owned(),
        ),
        MoneyError::OutsideMoneyRange => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field} is outside the supported money range"),
        ),
        MoneyError::PrecisionOutOfRange => (
            "builtins",
            "ValueError",
            "precision out of range".to_owned(),
        ),
    }
}

/// `to_minor_units` catches `InvalidOperation`/`Overflow` and re-raises with the value
/// in the message; quantize/floor do not. Keep the two spellings distinct.
fn to_minor_error_parts(
    error: &MoneyError,
    field: &str,
    amount: &str,
) -> (&'static str, &'static str, String) {
    match error {
        MoneyError::OutsideMoneyRange => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field}={amount} is outside the supported money range"),
        ),
        _ => money_error_parts(error, field, amount),
    }
}

/// A decimal spelling that cannot be parsed: `Decimal(s)` in the oracle raises
/// `decimal.InvalidOperation` with these exact `str` values.
fn parse_error_parts(field: &str, error: &ParseError) -> (&'static str, &'static str, String) {
    match error {
        ParseError::NonFinite => (
            MONEY_MODULE,
            "MoneyError",
            format!("{field} must be finite"),
        ),
        ParseError::Syntax => (
            DECIMAL_MODULE,
            "InvalidOperation",
            "[<class 'decimal.ConversionSyntax'>]".to_owned(),
        ),
        ParseError::ExpOutOfRange => (
            DECIMAL_MODULE,
            "InvalidOperation",
            "[<class 'decimal.InvalidOperation'>]".to_owned(),
        ),
    }
}

/// A refusal from a conversion helper. `NotFound` is the adapter's own class.
fn conversion_error_parts(error: &ConversionError) -> (&'static str, &'static str, String) {
    match error {
        ConversionError::UnknownTokenDecimals => (
            ADAPTER_ERRORS_MODULE,
            "NotFound",
            NOT_FOUND_MESSAGE.to_owned(),
        ),
        ConversionError::DivisionByZero => (
            DECIMAL_MODULE,
            "DivisionByZero",
            "[<class 'decimal.DivisionByZero'>]".to_owned(),
        ),
        ConversionError::InvalidOperation => (
            DECIMAL_MODULE,
            "InvalidOperation",
            "[<class 'decimal.InvalidOperation'>]".to_owned(),
        ),
        ConversionError::Overflow => (
            DECIMAL_MODULE,
            "Overflow",
            "[<class 'decimal.Overflow'>]".to_owned(),
        ),
    }
}

/// Convert a Python money value (a decimal string or an int) into the kernel's
/// `Decimal`, applying `money._as_decimal`'s guards at the boundary.
///
/// A float is never accepted: it is refused with the original `FloatMoneyError` before
/// any kernel runs. Strings are the caller's way of handing the bridge an exact
/// `Decimal` literal; the oracle does `Decimal(s)` before calling the same kernel.
fn parse_money_value<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    field: &str,
) -> PyResult<Decimal> {
    // bool is a subclass of int in Python, so it must be checked first.
    if value.is_instance_of::<PyBool>() {
        let (module, class, message) = money_error_parts(&MoneyError::BoolInput, field, "");
        return Err(py_error(py, module, class, &message));
    }
    if value.is_instance_of::<PyFloat>() {
        let (module, class, message) = money_error_parts(&MoneyError::FloatInput, field, "");
        return Err(py_error(py, module, class, &message));
    }
    if value.is_instance_of::<PyInt>() {
        // `Decimal(int)` is exact; `str(int)` is always a valid decimal literal.
        let text = value.str()?.to_cow()?.into_owned();
        return Ok(Decimal::parse(&text).expect("a Python int's str is a valid decimal literal"));
    }
    if let Ok(text) = value.cast::<PyString>() {
        return Decimal::parse(&text.to_cow()?).map_err(|error| {
            let (module, class, message) = parse_error_parts(field, &error);
            py_error(py, module, class, &message)
        });
    }
    Err(py_error(
        py,
        MONEY_MODULE,
        "MoneyError",
        &format!("{field} must be Decimal or int, not {}", type_name(value)?),
    ))
}

/// Convert a Python int into `MinorUnits`, mirroring `money.from_minor_units`' guard.
/// The magnitude is arbitrary precision, so the digits are taken from `str(int)` rather
/// than a fixed-width extraction.
fn minor_from_int<'py>(py: Python<'py>, value: &Bound<'py, PyAny>) -> PyResult<MinorUnits> {
    if value.is_instance_of::<PyBool>() || !value.is_instance_of::<PyInt>() {
        return Err(py_error(
            py,
            MONEY_MODULE,
            "MoneyError",
            &format!("minor units must be int, not {}", type_name(value)?),
        ));
    }
    let text = value.str()?.to_cow()?.into_owned();
    MinorUnits::parse(&text).ok_or_else(|| {
        py_error(
            py,
            MONEY_MODULE,
            "MoneyError",
            &format!("minor units must be int, not {text}"),
        )
    })
}

/// Build an arbitrary-precision Python `int` from `MinorUnits` (via `int(str)`), so a
/// 38-digit count survives the boundary instead of being truncated to `i64`.
fn minor_to_py_int<'py>(py: Python<'py>, minor: &MinorUnits) -> PyResult<Bound<'py, PyInt>> {
    let digits = minor.magnitude.to_digits_be();
    let signed = if minor.sign {
        format!("-{digits}")
    } else {
        digits
    };
    Ok(py
        .get_type::<PyInt>()
        .call1((signed,))?
        .cast_into::<PyInt>()?)
}

/// A token-decimals count. Any value outside the kernel's `0..=36` range becomes the
/// adapter's `NotFound`, matching `quotes.to_raw`/`from_raw`.
fn decimals_arg(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<i32> {
    let not_found = || py_error(py, ADAPTER_ERRORS_MODULE, "NotFound", NOT_FOUND_MESSAGE);
    let number: i64 = if value.is_instance_of::<PyBool>() {
        if value.extract::<bool>()? {
            1
        } else {
            0
        }
    } else if value.is_instance_of::<PyInt>() {
        value
            .str()?
            .to_cow()?
            .parse::<i64>()
            .map_err(|_| not_found())?
    } else {
        // Floats and other types are outside the recorded corpus; fail closed.
        return Err(not_found());
    };
    if !(i32::MIN as i64..=i32::MAX as i64).contains(&number) {
        return Err(not_found());
    }
    Ok(number as i32)
}

// ---------------------------------------------------------------------------
// Exposed money surface. Every kernel here already passed its own parity gate.
// ---------------------------------------------------------------------------

/// Parse a decimal spelling and return `str(Decimal(s))` (round-trip proof).
#[pyfunction]
#[pyo3(signature = (value))]
fn decimal_str(py: Python<'_>, value: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(parse_money_value(py, value, "amount")?.str_repr())
}

/// Numeric comparison of two decimal spellings: `-1`, `0` or `1`.
#[pyfunction]
#[pyo3(signature = (a, b))]
fn decimal_compare(py: Python<'_>, a: &Bound<'_, PyAny>, b: &Bound<'_, PyAny>) -> PyResult<i8> {
    let left = parse_money_value(py, a, "a")?;
    let right = parse_money_value(py, b, "b")?;
    Ok(match compare_kernel(&left, &right) {
        Ordering::Less => -1,
        Ordering::Equal => 0,
        Ordering::Greater => 1,
    })
}

/// `money.quantize_money` at the ledger's 1e-8 quantum, half-even.
#[pyfunction]
#[pyo3(signature = (value, field = "amount"))]
fn quantize_money(py: Python<'_>, value: &Bound<'_, PyAny>, field: &str) -> PyResult<String> {
    let amount = parse_money_value(py, value, field)?;
    match quantize_money_kernel(&amount) {
        Ok(result) => Ok(result.str_repr()),
        Err(error) => {
            let (module, class, message) = money_error_parts(&error, field, &amount.str_repr());
            Err(py_error(py, module, class, &message))
        }
    }
}

/// `money.floor_money` at the ledger's 1e-8 quantum, toward negative infinity.
#[pyfunction]
#[pyo3(signature = (value, field = "amount"))]
fn floor_money(py: Python<'_>, value: &Bound<'_, PyAny>, field: &str) -> PyResult<String> {
    let amount = parse_money_value(py, value, field)?;
    match floor_money_kernel(&amount) {
        Ok(result) => Ok(result.str_repr()),
        Err(error) => {
            let (module, class, message) = money_error_parts(&error, field, &amount.str_repr());
            Err(py_error(py, module, class, &message))
        }
    }
}

/// `money.to_minor_units`: exact integer count of 1e-8 minor units.
#[pyfunction]
#[pyo3(signature = (value, field = "amount"))]
fn to_minor_units<'py>(
    py: Python<'py>,
    value: &Bound<'py, PyAny>,
    field: &str,
) -> PyResult<Bound<'py, PyInt>> {
    let amount = parse_money_value(py, value, field)?;
    match to_minor_units_kernel(&amount) {
        Ok(minor) => minor_to_py_int(py, &minor),
        Err(error) => {
            let (module, class, message) = to_minor_error_parts(&error, field, &amount.str_repr());
            Err(py_error(py, module, class, &message))
        }
    }
}

/// `money.from_minor_units`: the inverse scaling, for reporting.
#[pyfunction]
#[pyo3(signature = (minor))]
fn from_minor_units(py: Python<'_>, minor: &Bound<'_, PyAny>) -> PyResult<String> {
    Ok(from_minor_units_kernel(&minor_from_int(py, minor)?).str_repr())
}

/// `quotes.to_raw`: scale a token amount to raw integer units, truncate toward zero.
#[pyfunction]
#[pyo3(signature = (amount, decimals))]
fn to_raw<'py>(
    py: Python<'py>,
    amount: &Bound<'py, PyAny>,
    decimals: &Bound<'py, PyAny>,
) -> PyResult<Bound<'py, PyInt>> {
    let value = parse_money_value(py, amount, "amount")?;
    let dec = decimals_arg(py, decimals)?;
    match to_raw_kernel(&value, dec) {
        Ok(raw) => minor_to_py_int(py, &raw),
        Err(error) => {
            let (module, class, message) = conversion_error_parts(&error);
            Err(py_error(py, module, class, &message))
        }
    }
}

/// `quotes.from_raw`: inverse of `to_raw` under the ambient context.
#[pyfunction]
#[pyo3(signature = (raw, decimals))]
fn from_raw<'py>(
    py: Python<'py>,
    raw: &Bound<'py, PyAny>,
    decimals: &Bound<'py, PyAny>,
) -> PyResult<String> {
    let units = minor_from_int(py, raw)?;
    let dec = decimals_arg(py, decimals)?;
    match from_raw_kernel(&units, dec) {
        Ok(value) => Ok(value.str_repr()),
        Err(error) => {
            let (module, class, message) = conversion_error_parts(&error);
            Err(py_error(py, module, class, &message))
        }
    }
}

/// The importable Python module: `import ezchain_bridge`.
#[pymodule]
fn ezchain_bridge(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(stale_reason, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_scopes, module)?)?;
    module.add_function(wrap_pyfunction!(decimal_str, module)?)?;
    module.add_function(wrap_pyfunction!(decimal_compare, module)?)?;
    module.add_function(wrap_pyfunction!(quantize_money, module)?)?;
    module.add_function(wrap_pyfunction!(floor_money, module)?)?;
    module.add_function(wrap_pyfunction!(to_minor_units, module)?)?;
    module.add_function(wrap_pyfunction!(from_minor_units, module)?)?;
    module.add_function(wrap_pyfunction!(to_raw, module)?)?;
    module.add_function(wrap_pyfunction!(from_raw, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn required_error_matches_the_oracle() {
        assert_eq!(
            api_key_error_parts(&ScopeError::Required),
            (
                "API_KEY_SCOPES_REQUIRED",
                "at least one scope is required".to_owned()
            )
        );
    }

    #[test]
    fn unknown_error_uses_the_original_message_layout() {
        let grant = ["read".to_owned(), "zz".to_owned(), "aa".to_owned()];
        let error = ezchain_rpc::scopes::normalize_scopes(Some(&grant)).unwrap_err();
        assert_eq!(
            api_key_error_parts(&error),
            (
                "API_KEY_SCOPE_UNKNOWN",
                "unknown scope(s): aa, zz; allowed: admin, read, trade".to_owned()
            )
        );
    }

    #[test]
    fn money_error_parts_match_money_py() {
        assert_eq!(
            money_error_parts(&MoneyError::NonFinite, "amount", ""),
            (
                MONEY_MODULE,
                "MoneyError",
                "amount must be finite".to_owned()
            )
        );
        assert_eq!(
            money_error_parts(&MoneyError::FloatInput, "amount", ""),
            (
                MONEY_MODULE,
                "FloatMoneyError",
                "amount must be exact Decimal, not float; a float has already lost the amount"
                    .to_owned()
            )
        );
        assert_eq!(
            money_error_parts(&MoneyError::BoolInput, "balance", ""),
            (
                MONEY_MODULE,
                "MoneyError",
                "balance must be exact Decimal, not bool".to_owned()
            )
        );
        assert_eq!(
            money_error_parts(&MoneyError::SubQuantum, "amount", "9E-9"),
            (
                MONEY_MODULE,
                "SubQuantumError",
                "amount=9E-9 is not a whole number of 1E-8 minor units".to_owned()
            )
        );
        assert_eq!(
            money_error_parts(&MoneyError::OutsideMoneyRange, "amount", "1E+999999"),
            (
                MONEY_MODULE,
                "MoneyError",
                "amount is outside the supported money range".to_owned()
            )
        );
        assert_eq!(
            to_minor_error_parts(&MoneyError::OutsideMoneyRange, "amount", "1E+999999"),
            (
                MONEY_MODULE,
                "MoneyError",
                "amount=1E+999999 is outside the supported money range".to_owned()
            )
        );
    }

    #[test]
    fn parse_and_conversion_error_parts_match_the_originals() {
        assert_eq!(
            parse_error_parts("amount", &ParseError::NonFinite),
            (
                MONEY_MODULE,
                "MoneyError",
                "amount must be finite".to_owned()
            )
        );
        assert_eq!(
            parse_error_parts("amount", &ParseError::Syntax),
            (
                DECIMAL_MODULE,
                "InvalidOperation",
                "[<class 'decimal.ConversionSyntax'>]".to_owned()
            )
        );
        assert_eq!(
            parse_error_parts("amount", &ParseError::ExpOutOfRange),
            (
                DECIMAL_MODULE,
                "InvalidOperation",
                "[<class 'decimal.InvalidOperation'>]".to_owned()
            )
        );
        assert_eq!(
            conversion_error_parts(&ConversionError::UnknownTokenDecimals),
            (
                ADAPTER_ERRORS_MODULE,
                "NotFound",
                NOT_FOUND_MESSAGE.to_owned()
            )
        );
    }

    #[test]
    fn exposed_parse_and_format_path_round_trips() {
        // The bridge parses with Decimal::parse and formats with str_repr; these are
        // the spellings the Python parity corpus asserts against str(Decimal(...)).
        for (input, expected) in [
            ("0.1", "0.1"),
            ("-0.0", "-0.0"),
            ("1E+3", "1E+3"),
            ("0.00000001", "1E-8"),
            ("1E-7", "1E-7"),
            ("1E-6", "0.000001"),
        ] {
            assert_eq!(
                Decimal::parse(input).unwrap().str_repr(),
                expected,
                "{input}"
            );
        }
    }
}
