//! Pure adapter-error → HTTP envelope mapping, ported from `ezchain_api.errors`.
//!
//! The Python originals (`provider_budget_http_error`, `adapter_http_error` and
//! `candles_http_error`) only inspect an `AdapterError` and build a FastAPI `HTTPException`.
//! They do no I/O, key handling or money arithmetic. The Rust port returns a typed
//! [`HttpError`] with the same status code, detail dict and headers, so the RPC edge can
//! reproduce the Python wire response exactly (ADR-0049/0050 parity gate).
//!
//! Every reason code and every default/fallback branch is recorded as a case below. Two
//! Python behaviours are load-bearing:
//!
//! - `ProviderBudgetExhausted` subclasses `RateLimited`, so `adapter_http_error` tests it
//!   *first* (429 + `Retry-After`) while `candles_http_error`'s `RateLimited | UpstreamError`
//!   branch catches it *before* the adapter fallback (502 `UPSTREAM_UNREACHABLE`, never 429).
//! - `candles_http_error` rewrites `NotFound` → `CANDLES_NOT_FOUND` and
//!   `RateLimited`/`UpstreamError` → `UPSTREAM_UNREACHABLE`; the `detail` key is present in
//!   the latter body even when the source detail is `None` (null on the wire).

use crate::json_safe::JsonValue;

/// The Python exception class an adapter error came from. This is what `isinstance` tests
/// in the originals, so the dispatch order below mirrors those tests exactly.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdapterErrorKind {
    /// `AdapterError` itself — the default/fallback case.
    AdapterError,
    NotFound,
    UpstreamError,
    RateLimited,
    NoRoute,
    ProviderKeyUnavailable,
    /// `ProviderBudgetExhausted(RateLimited)` — a distinct enum kind, but treated as a
    /// `RateLimited` family member in the candles mapping (see module docs).
    ProviderBudgetExhausted,
    CandlesUnsupported,
}

/// An adapter error value, carrying the fields the Python exceptions expose.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdapterError {
    pub kind: AdapterErrorKind,
    pub message: String,
    pub code: String,
    pub retryable: bool,
    pub detail: Option<String>,
    pub provider: Option<String>,
}

impl AdapterError {
    /// `AdapterError(message, detail=...)`: code `ADAPTER_ERROR`, non-retryable default.
    pub fn new(message: &str, detail: Option<&str>) -> Self {
        Self {
            kind: AdapterErrorKind::AdapterError,
            message: message.to_string(),
            code: "ADAPTER_ERROR".to_string(),
            retryable: false,
            detail: detail.map(str::to_string),
            provider: None,
        }
    }

    /// `NotFound`: code `NOT_FOUND`, non-retryable.
    pub fn not_found(message: &str) -> Self {
        Self {
            kind: AdapterErrorKind::NotFound,
            message: message.to_string(),
            code: "NOT_FOUND".to_string(),
            retryable: false,
            detail: None,
            provider: None,
        }
    }

    /// `UpstreamError`: code `UPSTREAM_ERROR`, retryable.
    pub fn upstream_error(message: &str, detail: Option<&str>) -> Self {
        Self {
            kind: AdapterErrorKind::UpstreamError,
            message: message.to_string(),
            code: "UPSTREAM_ERROR".to_string(),
            retryable: true,
            detail: detail.map(str::to_string),
            provider: None,
        }
    }

    /// `RateLimited`: code `RATE_LIMITED`, retryable.
    pub fn rate_limited(message: &str) -> Self {
        Self {
            kind: AdapterErrorKind::RateLimited,
            message: message.to_string(),
            code: "RATE_LIMITED".to_string(),
            retryable: true,
            detail: None,
            provider: None,
        }
    }

    /// `NoRoute`: code `NO_SELL_ROUTE` unless the caller overrides it, non-retryable.
    pub fn no_route(message: &str, code_override: Option<&str>) -> Self {
        Self {
            kind: AdapterErrorKind::NoRoute,
            message: message.to_string(),
            code: code_override.unwrap_or("NO_SELL_ROUTE").to_string(),
            retryable: false,
            detail: None,
            provider: None,
        }
    }

    /// `ProviderKeyUnavailable`: code `PROVIDER_KEY_UNAVAILABLE`, retryability is chosen by
    /// the caller (permanent by default in Python, transient when `retryable=True`).
    pub fn provider_key_unavailable(message: &str, retryable: bool, detail: Option<&str>) -> Self {
        Self {
            kind: AdapterErrorKind::ProviderKeyUnavailable,
            message: message.to_string(),
            code: "PROVIDER_KEY_UNAVAILABLE".to_string(),
            retryable,
            detail: detail.map(str::to_string),
            provider: None,
        }
    }

    /// `ProviderBudgetExhausted(provider, detail=...)`: subclass of `RateLimited`, so it is
    /// retryable; its message is derived in the Python constructor.
    pub fn provider_budget_exhausted(provider: &str, detail: &str) -> Self {
        Self {
            kind: AdapterErrorKind::ProviderBudgetExhausted,
            message: format!("{provider} request budget exhausted"),
            code: "PROVIDER_BUDGET_EXHAUSTED".to_string(),
            retryable: true,
            detail: Some(detail.to_string()),
            provider: Some(provider.to_string()),
        }
    }

    /// `CandlesUnsupported`: code `CANDLES_UNSUPPORTED_CHAIN`, non-retryable.
    pub fn candles_unsupported(message: &str) -> Self {
        Self {
            kind: AdapterErrorKind::CandlesUnsupported,
            message: message.to_string(),
            code: "CANDLES_UNSUPPORTED_CHAIN".to_string(),
            retryable: false,
            detail: None,
            provider: None,
        }
    }
}

/// The HTTP envelope produced by the mapping. `detail` is a JSON object; `headers` carries
/// the optional `Retry-After` pair (empty in every case except the provider-budget one).
#[derive(Debug, Clone, PartialEq)]
pub struct HttpError {
    pub status_code: u16,
    pub detail: JsonValue,
    pub headers: Vec<(String, String)>,
}

fn detail_object(fields: Vec<(&str, JsonValue)>) -> JsonValue {
    JsonValue::Object(
        fields
            .into_iter()
            .map(|(key, value)| (JsonValue::Str(key.to_string()), value))
            .collect(),
    )
}

/// The `{code, message, retryable}` body shared by the plain adapter cases.
fn basic_detail(exc: &AdapterError) -> JsonValue {
    detail_object(vec![
        ("code", JsonValue::Str(exc.code.clone())),
        ("message", JsonValue::Str(exc.message.clone())),
        ("retryable", JsonValue::Bool(exc.retryable)),
    ])
}

/// The source `detail`, present in a body as a string or as JSON `null`.
fn detail_value(exc: &AdapterError) -> JsonValue {
    match &exc.detail {
        Some(value) => JsonValue::Str(value.clone()),
        None => JsonValue::Null,
    }
}

fn is_rate_limited_family(kind: AdapterErrorKind) -> bool {
    matches!(
        kind,
        AdapterErrorKind::RateLimited | AdapterErrorKind::ProviderBudgetExhausted
    )
}

/// `provider_budget_http_error`: a bounded provider queue refused the call. Always 429,
/// retryable, with the provider named and `Retry-After: 1`.
pub fn provider_budget_http_error(exc: &AdapterError) -> HttpError {
    HttpError {
        status_code: 429,
        detail: detail_object(vec![
            ("code", JsonValue::Str(exc.code.clone())),
            ("message", JsonValue::Str(exc.message.clone())),
            ("retryable", JsonValue::Bool(true)),
            (
                "provider",
                JsonValue::Str(exc.provider.clone().unwrap_or_default()),
            ),
        ]),
        headers: vec![("Retry-After".to_string(), "1".to_string())],
    }
}

/// `adapter_http_error`: the reason-code → HTTP status mapping. Unknown adapter errors
/// default to 502; only the provider-budget subclass gets a header and the `provider` field.
pub fn adapter_http_error(exc: &AdapterError) -> HttpError {
    // This test must come before the RateLimited family: the Python original checks
    // isinstance(ProviderBudgetExhausted) first.
    if exc.kind == AdapterErrorKind::ProviderBudgetExhausted {
        return provider_budget_http_error(exc);
    }
    let status_code = match exc.kind {
        AdapterErrorKind::NotFound => 404,
        AdapterErrorKind::RateLimited => 429,
        AdapterErrorKind::NoRoute => 409,
        AdapterErrorKind::ProviderKeyUnavailable => 503,
        AdapterErrorKind::UpstreamError => 502,
        AdapterErrorKind::AdapterError
        | AdapterErrorKind::CandlesUnsupported
        | AdapterErrorKind::ProviderBudgetExhausted => 502,
    };
    HttpError {
        status_code,
        detail: basic_detail(exc),
        headers: vec![],
    }
}

/// `candles_http_error`: candle-specific reason codes. A chain without a provider is not
/// an upstream fault; an exhausted upstream is `UPSTREAM_UNREACHABLE` (retryable) rather than
/// an empty chart. All other error kinds fall back to [`adapter_http_error`].
pub fn candles_http_error(exc: &AdapterError) -> HttpError {
    if exc.kind == AdapterErrorKind::CandlesUnsupported {
        return HttpError {
            status_code: 404,
            detail: basic_detail(exc),
            headers: vec![],
        };
    }
    if exc.kind == AdapterErrorKind::NotFound {
        return HttpError {
            status_code: 404,
            detail: detail_object(vec![
                ("code", JsonValue::Str("CANDLES_NOT_FOUND".to_string())),
                ("message", JsonValue::Str(exc.message.clone())),
                ("retryable", JsonValue::Bool(false)),
            ]),
            headers: vec![],
        };
    }
    if is_rate_limited_family(exc.kind) || exc.kind == AdapterErrorKind::UpstreamError {
        return HttpError {
            status_code: 502,
            detail: detail_object(vec![
                ("code", JsonValue::Str("UPSTREAM_UNREACHABLE".to_string())),
                ("message", JsonValue::Str(exc.message.clone())),
                ("retryable", JsonValue::Bool(true)),
                ("detail", detail_value(exc)),
            ]),
            headers: vec![],
        };
    }
    adapter_http_error(exc)
}

#[cfg(test)]
mod tests {
    use super::{
        adapter_http_error, candles_http_error, provider_budget_http_error, AdapterError,
        AdapterErrorKind, HttpError,
    };
    use crate::json_safe::JsonValue;

    fn s(value: &str) -> JsonValue {
        JsonValue::Str(value.to_string())
    }

    fn body(fields: Vec<(&str, JsonValue)>) -> JsonValue {
        JsonValue::Object(
            fields
                .into_iter()
                .map(|(key, value)| (s(key), value))
                .collect(),
        )
    }

    fn expected(status: u16, detail: JsonValue) -> HttpError {
        HttpError {
            status_code: status,
            detail,
            headers: vec![],
        }
    }

    fn assert_mapping(actual: HttpError, want: HttpError, case: &str) {
        assert_eq!(actual, want, "recorded case {case}");
    }

    // Recorded Python cases: the oracle is ezchain_api.errors.adapter_http_error /
    // candles_http_error / provider_budget_http_error, driven against the worktree source
    // with the project venv (Python 3.11). Each expectation below is that function's output.

    #[test]
    fn adapter_default_is_502_adapter_error() {
        assert_mapping(
            adapter_http_error(&AdapterError::new("generic", Some("d"))),
            expected(
                502,
                body(vec![
                    ("code", s("ADAPTER_ERROR")),
                    ("message", s("generic")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "AdapterError -> 502 default",
        );
    }

    #[test]
    fn adapter_not_found_is_404() {
        assert_mapping(
            adapter_http_error(&AdapterError::not_found("no")),
            expected(
                404,
                body(vec![
                    ("code", s("NOT_FOUND")),
                    ("message", s("no")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "NotFound -> 404",
        );
    }

    #[test]
    fn adapter_upstream_error_is_502_retryable() {
        assert_mapping(
            adapter_http_error(&AdapterError::upstream_error("up", None)),
            expected(
                502,
                body(vec![
                    ("code", s("UPSTREAM_ERROR")),
                    ("message", s("up")),
                    ("retryable", JsonValue::Bool(true)),
                ]),
            ),
            "UpstreamError -> 502",
        );
    }

    #[test]
    fn adapter_rate_limited_is_429_retryable() {
        assert_mapping(
            adapter_http_error(&AdapterError::rate_limited("slow")),
            expected(
                429,
                body(vec![
                    ("code", s("RATE_LIMITED")),
                    ("message", s("slow")),
                    ("retryable", JsonValue::Bool(true)),
                ]),
            ),
            "RateLimited -> 429",
        );
    }

    #[test]
    fn adapter_no_route_is_409_with_default_or_custom_code() {
        assert_mapping(
            adapter_http_error(&AdapterError::no_route("nr", None)),
            expected(
                409,
                body(vec![
                    ("code", s("NO_SELL_ROUTE")),
                    ("message", s("nr")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "NoRoute default code -> 409",
        );
        assert_mapping(
            adapter_http_error(&AdapterError::no_route("nr", Some("EXIT_NO_ROUTE"))),
            expected(
                409,
                body(vec![
                    ("code", s("EXIT_NO_ROUTE")),
                    ("message", s("nr")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "NoRoute custom code -> 409",
        );
    }

    #[test]
    fn adapter_provider_key_unavailable_is_503_with_caller_retryability() {
        for (retryable, case) in [(false, "permanent"), (true, "transient")] {
            assert_mapping(
                adapter_http_error(&AdapterError::provider_key_unavailable(
                    "key", retryable, None,
                )),
                expected(
                    503,
                    body(vec![
                        ("code", s("PROVIDER_KEY_UNAVAILABLE")),
                        ("message", s("key")),
                        ("retryable", JsonValue::Bool(retryable)),
                    ]),
                ),
                case,
            );
        }
    }

    #[test]
    fn adapter_candles_unsupported_falls_back_to_502() {
        // CandlesUnsupported reaches candles_http_error's special case, but through the
        // plain adapter mapping it is an ordinary 502 (its code is still carried).
        assert_mapping(
            adapter_http_error(&AdapterError::candles_unsupported("no candles")),
            expected(
                502,
                body(vec![
                    ("code", s("CANDLES_UNSUPPORTED_CHAIN")),
                    ("message", s("no candles")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "CandlesUnsupported via adapter -> 502",
        );
    }

    fn budget_detail() -> JsonValue {
        body(vec![
            ("code", s("PROVIDER_BUDGET_EXHAUSTED")),
            ("message", s("jupiter request budget exhausted")),
            ("retryable", JsonValue::Bool(true)),
            ("provider", s("jupiter")),
        ])
    }

    #[test]
    fn provider_budget_http_error_is_429_with_retry_after() {
        let exc = AdapterError::provider_budget_exhausted("jupiter", "queue is full");
        assert_eq!(
            provider_budget_http_error(&exc),
            HttpError {
                status_code: 429,
                detail: budget_detail(),
                headers: vec![("Retry-After".to_string(), "1".to_string())],
            }
        );
    }

    #[test]
    fn adapter_provider_budget_is_checked_before_rate_limited() {
        // ProviderBudgetExhausted is a RateLimited subclass, so the dedicated branch wins.
        let exc = AdapterError::provider_budget_exhausted("jupiter", "queue is full");
        assert_eq!(
            adapter_http_error(&exc),
            HttpError {
                status_code: 429,
                detail: budget_detail(),
                headers: vec![("Retry-After".to_string(), "1".to_string())],
            }
        );
    }

    #[test]
    fn candles_unsupported_is_404() {
        assert_mapping(
            candles_http_error(&AdapterError::candles_unsupported("no candles")),
            expected(
                404,
                body(vec![
                    ("code", s("CANDLES_UNSUPPORTED_CHAIN")),
                    ("message", s("no candles")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "CandlesUnsupported -> 404",
        );
    }

    #[test]
    fn candles_not_found_rewrites_the_code() {
        assert_mapping(
            candles_http_error(&AdapterError::not_found("no bars")),
            expected(
                404,
                body(vec![
                    ("code", s("CANDLES_NOT_FOUND")),
                    ("message", s("no bars")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "NotFound -> CANDLES_NOT_FOUND 404",
        );
    }

    fn unreachable(message: &str, detail: JsonValue) -> HttpError {
        expected(
            502,
            body(vec![
                ("code", s("UPSTREAM_UNREACHABLE")),
                ("message", s(message)),
                ("retryable", JsonValue::Bool(true)),
                ("detail", detail),
            ]),
        )
    }

    #[test]
    fn candles_rate_limited_and_upstream_are_502_upstream_unreachable() {
        assert_mapping(
            candles_http_error(&AdapterError::rate_limited("slow")),
            unreachable("slow", JsonValue::Null),
            "RateLimited -> UPSTREAM_UNREACHABLE with null detail",
        );
        assert_mapping(
            candles_http_error(&AdapterError::upstream_error("down", Some("500"))),
            unreachable("down", s("500")),
            "UpstreamError -> UPSTREAM_UNREACHABLE with detail",
        );
        assert_mapping(
            candles_http_error(&AdapterError::upstream_error("down", None)),
            unreachable("down", JsonValue::Null),
            "UpstreamError without detail -> null detail key",
        );
    }

    #[test]
    fn candles_provider_budget_is_502_not_429() {
        // The subclass is caught by the candles RateLimited branch before the adapter
        // fallback, so it never sees the 429 + Retry-After mapping.
        assert_mapping(
            candles_http_error(&AdapterError::provider_budget_exhausted(
                "jupiter",
                "queue is full",
            )),
            unreachable("jupiter request budget exhausted", s("queue is full")),
            "ProviderBudgetExhausted via candles -> 502",
        );
    }

    #[test]
    fn candles_other_kinds_fall_back_to_the_adapter_mapping() {
        assert_mapping(
            candles_http_error(&AdapterError::no_route("nr", None)),
            expected(
                409,
                body(vec![
                    ("code", s("NO_SELL_ROUTE")),
                    ("message", s("nr")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "NoRoute fallback -> 409",
        );
        assert_mapping(
            candles_http_error(&AdapterError::provider_key_unavailable("key", false, None)),
            expected(
                503,
                body(vec![
                    ("code", s("PROVIDER_KEY_UNAVAILABLE")),
                    ("message", s("key")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "ProviderKeyUnavailable fallback -> 503",
        );
        assert_mapping(
            candles_http_error(&AdapterError::new("generic", None)),
            expected(
                502,
                body(vec![
                    ("code", s("ADAPTER_ERROR")),
                    ("message", s("generic")),
                    ("retryable", JsonValue::Bool(false)),
                ]),
            ),
            "AdapterError fallback -> 502",
        );
    }

    #[test]
    fn every_status_code_in_the_mapping_is_exercised() {
        // A compact parity table: (kind, adapter status, candles status).
        let cases: Vec<(AdapterErrorKind, u16, u16)> = vec![
            (AdapterErrorKind::AdapterError, 502, 502),
            (AdapterErrorKind::NotFound, 404, 404),
            (AdapterErrorKind::UpstreamError, 502, 502),
            (AdapterErrorKind::RateLimited, 429, 502),
            (AdapterErrorKind::NoRoute, 409, 409),
            (AdapterErrorKind::ProviderKeyUnavailable, 503, 503),
            (AdapterErrorKind::ProviderBudgetExhausted, 429, 502),
            (AdapterErrorKind::CandlesUnsupported, 502, 404),
        ];
        for (kind, adapter_status, candles_status) in cases {
            let exc = match kind {
                AdapterErrorKind::AdapterError => AdapterError::new("m", None),
                AdapterErrorKind::NotFound => AdapterError::not_found("m"),
                AdapterErrorKind::UpstreamError => AdapterError::upstream_error("m", None),
                AdapterErrorKind::RateLimited => AdapterError::rate_limited("m"),
                AdapterErrorKind::NoRoute => AdapterError::no_route("m", None),
                AdapterErrorKind::ProviderKeyUnavailable => {
                    AdapterError::provider_key_unavailable("m", false, None)
                }
                AdapterErrorKind::ProviderBudgetExhausted => {
                    AdapterError::provider_budget_exhausted("p", "d")
                }
                AdapterErrorKind::CandlesUnsupported => AdapterError::candles_unsupported("m"),
            };
            assert_eq!(
                adapter_http_error(&exc).status_code,
                adapter_status,
                "{kind:?}"
            );
            assert_eq!(
                candles_http_error(&exc).status_code,
                candles_status,
                "{kind:?}"
            );
        }
    }
}
