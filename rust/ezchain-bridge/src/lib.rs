//! In-process PyO3 bridge from Python callers to already-ported Rust kernels.
//!
//! The bridge is deliberately thin: it converts Python values to kernel inputs, calls
//! the pure kernel, and converts the result (or its typed error) back. It owns no
//! behaviour of its own. The Python implementation stays the behavioural oracle until
//! the whole surface has passed parity (ADR-0049/0050/0052).
//!
//! Money arithmetic, keys, I/O and ledger writes never cross this boundary.

#![forbid(unsafe_code)]

use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyTuple, PyType};

use ezchain_rpc::scopes::ScopeError;

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

/// The importable Python module: `import ezchain_bridge`.
#[pymodule]
fn ezchain_bridge(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(stale_reason, module)?)?;
    module.add_function(wrap_pyfunction!(normalize_scopes, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::api_key_error_parts;
    use ezchain_rpc::scopes::ScopeError;

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
}
