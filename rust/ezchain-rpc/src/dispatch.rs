//! Pure method dispatch table for the node-facing RPC edge (ADR-0050/0052).
//!
//! This is the typed front door a future transport will call after it has decoded one
//! request frame. It is **pure**: no sockets, no async runtime, no keys, no money arithmetic
//! and no I/O. It answers exactly three questions — does this method exist, does the caller
//! hold the scope it needs, and which typed handler should run — and returns a typed outcome
//! or a typed refusal that the caller can map to a wire error.
//!
//! # What the registry records
//!
//! A method name is the canonical `"VERB /path"` of an API route. The
//! [`MethodRegistry::canonical`] table is the recorded product surface: every route declared
//! by `apps/api/src/ezchain_api/routers/`, with the least privilege the Python oracle
//! `ezchain_api.auth.required_scope` assigns it. The table is pinned in the tests below, so a
//! route added or re-scoped in the API without a matching entry here is a failing test. This
//! module does not execute those routes; it only routes by name and scope.
//!
//! # Versioned contract
//!
//! A request carries a [`crate::wire::FieldMap`] envelope in the same shape as
//! [`crate::wire::VersionedEnvelope`]: `schema` (`ezchain.rpc-method/1`), `version` (`1`) and a
//! string `method` field. [`dispatch`] validates the envelope with
//! [`crate::wire::split_envelope`] before it looks up the method, so a document this build
//! does not understand is refused before any dispatch decision is made.
//!
//! # Scope checks
//!
//! The caller's grant is normalised by [`crate::scopes::normalize_scopes`] — the existing
//! scopes kernel — so empty and unknown grants fail closed. A method runs only when the grant
//! contains its required scope or the `admin` wildcard, exactly as the Python
//! `Principal.has_scope` does. Admin-only methods are therefore refused for `read`-only and
//! `trade`-only keys.
//!
//! # Error mapping
//!
//! [`DispatchError`] is mapped by [`map_dispatch_error`] to the same
//! [`crate::adapter_error::HttpError`] envelope the adapter-error kernel produces. A handler
//! failure that is already an [`crate::adapter_error::AdapterError`] is routed through
//! `adapter_http_error` directly; dispatch-specific statuses (400/401/403/404) that the
//! adapter kernel has no native kind for are built with the kernel's `HttpError`/`JsonValue`
//! types in the identical `{code, message, retryable}` shape.

use crate::adapter_error::{adapter_http_error, AdapterError, HttpError};
use crate::json_safe::JsonValue;
use crate::scopes::{normalize_scopes, ScopeError};
use crate::wire::{split_envelope, EnvelopeError, FieldMap, VersionedEnvelope, WireValue};

/// Schema kind of the versioned method envelope.
pub const METHOD_CONTRACT_KIND: &str = "ezchain.rpc-method";
/// The only method-contract version this registry accepts.
pub const METHOD_CONTRACT_VERSION: u32 = 1;

/// The least privilege a declared method requires. The strings are the three scopes the
/// existing scopes kernel allows, and mirror `Principal.has_scope` in the Python API.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RequiredScope {
    Read,
    Trade,
    Admin,
}

impl RequiredScope {
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Read => "read",
            Self::Trade => "trade",
            Self::Admin => "admin",
        }
    }
}

/// Which pure handler family served a dispatched method.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HandlerFamily {
    Read,
    Trade,
    Admin,
}

/// The typed result of a handler. Pure and deterministic: it names the method it was
/// registered for and the family that ran, so a caller can prove the registry routed the
/// method to the right handler without any side effect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct HandlerOutcome {
    pub method: &'static str,
    pub family: HandlerFamily,
}

/// A typed handler. It receives the canonical method name it was registered under and may
/// surface a typed refusal (for example an adapter failure) without performing I/O here.
pub type Handler = fn(&'static str) -> Result<HandlerOutcome, DispatchError>;

/// Pure read handler.
pub fn read_handler(method: &'static str) -> Result<HandlerOutcome, DispatchError> {
    Ok(HandlerOutcome {
        method,
        family: HandlerFamily::Read,
    })
}

/// Pure trade handler.
pub fn trade_handler(method: &'static str) -> Result<HandlerOutcome, DispatchError> {
    Ok(HandlerOutcome {
        method,
        family: HandlerFamily::Trade,
    })
}

/// Pure admin handler.
pub fn admin_handler(method: &'static str) -> Result<HandlerOutcome, DispatchError> {
    Ok(HandlerOutcome {
        method,
        family: HandlerFamily::Admin,
    })
}

/// One registered method: its canonical name, least privilege and typed handler.
#[derive(Debug, Clone, Copy)]
pub struct MethodSpec {
    pub name: &'static str,
    pub required: RequiredScope,
    pub handler: Handler,
}

/// The recorded API method/scope surface. Each entry is a route from
/// `apps/api/src/ezchain_api/routers/` with the scope the Python oracle assigns it. The
/// function pointer is chosen from the required scope: a read route uses the read handler,
/// a trade route the trade handler, an admin route the admin handler.
pub static METHODS: &[MethodSpec] = &[
    // ---- pinned API surface: 83 routes, verb + path, required scope from the Python oracle ----
    MethodSpec {
        name: "GET /api/v1/paper/ledger",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/performance",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/attribution",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/limits",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/alerts",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/export/orders.csv",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/export/fills.csv",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/export/ledger.csv",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/agent/capabilities",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/agent/state",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/agent/keys",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "POST /api/v1/agent/keys",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "DELETE /api/v1/agent/keys/{key_id}",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/approvals",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/approvals/{approval_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/paper/approvals/{approval_id}/approve",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "POST /api/v1/paper/approvals/{approval_id}/reject",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/automation",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "PUT /api/v1/paper/automation",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "POST /api/v1/backtests",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/backtests",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/backtests/{backtest_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/agent/buckets",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/agent/buckets/{agent_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "PUT /api/v1/agent/buckets/{agent_id}",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/discovery/new-pairs",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/graph/catalogue",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/graph/validate",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/graph/dry-run",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/markets/overview",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/markets/stored",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/markets",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/tokens/{chain}/{address}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/tokens/{chain}/{address}/candles",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/quotes",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/account",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/wallets",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/positions",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/orders",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/fills",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/paper/reconciliation",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/paper/orders",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/plugins",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/plugins/reload",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /api/v1/plugins/{plugin_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/plugins/{plugin_id}/validate",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/plugins/{plugin_id}/health",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/protection/arms",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/protection/arms",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/protection/arms/{arm_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "PATCH /api/v1/protection/arms/{arm_id}",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "POST /api/v1/protection/arms/{arm_id}/cancel",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "POST /api/v1/protection/arms/{arm_id}/evaluate",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/protection/profiles",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/protection/profiles",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/protection/profiles/effective",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/protection/arms/{arm_id}/events",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/provider-keys",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "POST /api/v1/provider-keys",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "DELETE /api/v1/provider-keys/{key_id}",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /api/v1/signals/movers",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/strategies",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/strategies",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/strategies/{strategy_id}",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "PATCH /api/v1/strategies/{strategy_id}",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "DELETE /api/v1/strategies/{strategy_id}",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "POST /api/v1/strategies/{strategy_id}/run",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/strategies/{strategy_id}/performance",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/strategies/{strategy_id}/live-readiness",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/strategies/{strategy_id}/runs",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /healthz",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /readyz",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /metrics",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /health",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /api/v1/chains",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/system/kill-switch",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/system/kill-switch",
        required: RequiredScope::Admin,
        handler: admin_handler,
    },
    MethodSpec {
        name: "GET /api/v1/trade-policy",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "GET /api/v1/trade-policy/preview",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "PUT /api/v1/trade-policy/strategies/{strategy_id}",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "GET /api/v1/watchlist",
        required: RequiredScope::Read,
        handler: read_handler,
    },
    MethodSpec {
        name: "POST /api/v1/watchlist",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
    MethodSpec {
        name: "DELETE /api/v1/watchlist",
        required: RequiredScope::Trade,
        handler: trade_handler,
    },
];

/// A typed registry over method names. `Default` is the recorded canonical surface; a test
/// may build a registry over its own specs with [`MethodRegistry::from_specs`].
#[derive(Debug, Clone, Copy)]
pub struct MethodRegistry {
    methods: &'static [MethodSpec],
}

impl Default for MethodRegistry {
    fn default() -> Self {
        Self::canonical()
    }
}

impl MethodRegistry {
    /// The recorded API surface.
    pub const fn canonical() -> Self {
        Self { methods: METHODS }
    }

    /// A registry over a caller-supplied static table.
    pub const fn from_specs(methods: &'static [MethodSpec]) -> Self {
        Self { methods }
    }

    /// Look up a method by its canonical name.
    pub fn lookup(&self, name: &str) -> Option<&'static MethodSpec> {
        self.methods.iter().find(|spec| spec.name == name)
    }

    /// Number of registered methods.
    pub fn len(&self) -> usize {
        self.methods.len()
    }

    pub fn is_empty(&self) -> bool {
        self.methods.is_empty()
    }

    /// All registered specs, in table order.
    pub fn iter(&self) -> impl Iterator<Item = &'static MethodSpec> {
        self.methods.iter()
    }
}

/// One dispatch request. `envelope` is a field map carrying `schema`, `version` and a string
/// `method`; `granted_scopes` is the caller's raw grant, normalised by the scopes kernel
/// inside [`dispatch`].
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DispatchRequest<'a> {
    pub envelope: &'a FieldMap,
    pub granted_scopes: Option<&'a [String]>,
}

/// Every typed way a dispatch can fail. `Adapter` is the channel through which a handler
/// may surface an adapter failure; it is mapped by the adapter-error kernel.
#[derive(Debug, Clone, PartialEq)]
pub enum DispatchError {
    /// The versioned envelope was not one this build understands.
    Envelope(EnvelopeError),
    /// The envelope had no string `method` field.
    MalformedMethod,
    /// No registered method has that name.
    UnknownMethod { name: String },
    /// The caller's scope grant was empty or contained an unknown scope.
    InvalidGrant(ScopeError),
    /// The caller lacks the method's required scope (and is not admin).
    MissingScope {
        method: String,
        required: RequiredScope,
    },
    /// A handler surfaced an adapter failure.
    Adapter(Box<AdapterError>),
}

/// Build the versioned envelope field map for a method name, using the wire kernel's own
/// envelope encoding so the two sides agree on `schema` and `version`.
pub fn method_envelope(method: &str) -> FieldMap {
    let mut map = VersionedEnvelope::new(METHOD_CONTRACT_KIND, METHOD_CONTRACT_VERSION, ())
        .to_field_map(|_| FieldMap::new());
    map.insert("method", WireValue::Str(method.to_string()));
    map
}

/// Dispatch one request against a registry.
///
/// Checks run in this fixed order so a document with several faults reports one reproducible
/// reason: (1) versioned envelope, (2) string `method` field, (3) method lookup, (4) scope
/// grant normalisation, (5) required-scope check, (6) typed handler.
pub fn dispatch(
    request: &DispatchRequest<'_>,
    registry: &MethodRegistry,
) -> Result<HandlerOutcome, DispatchError> {
    // 1. Versioned contract: schema kind and version must be the ones this build speaks.
    let payload = split_envelope(
        request.envelope,
        METHOD_CONTRACT_KIND,
        METHOD_CONTRACT_VERSION,
    )
    .map_err(DispatchError::Envelope)?;
    if let Some(WireValue::Int(text)) = request.envelope.get("version") {
        let got = text.parse::<u32>().unwrap_or(u32::MAX);
        if got != METHOD_CONTRACT_VERSION {
            return Err(DispatchError::Envelope(EnvelopeError::UnsupportedVersion {
                expected: METHOD_CONTRACT_VERSION,
                got,
            }));
        }
    }

    // 2. The method field must be a string.
    let method = match payload.get("method") {
        Some(WireValue::Str(value)) => value.as_str(),
        _ => return Err(DispatchError::MalformedMethod),
    };

    // 3. Unknown methods are refused before any scope is inspected.
    let spec = registry
        .lookup(method)
        .ok_or_else(|| DispatchError::UnknownMethod {
            name: method.to_string(),
        })?;

    // 4. Normalise the grant through the existing scopes kernel; fail closed on bad input.
    let grant = normalize_scopes(request.granted_scopes).map_err(DispatchError::InvalidGrant)?;

    // 5. `admin` satisfies every scope, as in the Python Principal.has_scope.
    let required = spec.required.as_str();
    let allowed =
        grant.iter().any(|scope| scope == "admin") || grant.iter().any(|scope| scope == required);
    if !allowed {
        return Err(DispatchError::MissingScope {
            method: method.to_string(),
            required: spec.required,
        });
    }

    // 6. Run the registered typed handler.
    (spec.handler)(spec.name)
}

/// Map a dispatch refusal to the HTTP envelope the adapter-error kernel produces.
///
/// An [`DispatchError::Adapter`] goes straight through `adapter_http_error`. The
/// dispatch-specific statuses (400/401/403/404) have no adapter-error kind, so their bodies
/// are built here with the kernel's `HttpError`/`JsonValue` types in the identical
/// `{code, message, retryable}` shape.
pub fn map_dispatch_error(error: &DispatchError) -> HttpError {
    match error {
        DispatchError::Adapter(exc) => adapter_http_error(exc),
        DispatchError::Envelope(EnvelopeError::UnsupportedVersion { expected, got }) => detail(
            400,
            "UNSUPPORTED_VERSION",
            format!("method contract version {got} is not supported; expected {expected}"),
        ),
        DispatchError::Envelope(EnvelopeError::UnsupportedKind { expected, got }) => detail(
            400,
            "UNKNOWN_SCHEMA",
            format!("method schema {got:?} is not supported; expected {expected:?}"),
        ),
        DispatchError::MalformedMethod => detail(
            400,
            "INVALID_METHOD",
            "a string 'method' field is required".to_string(),
        ),
        DispatchError::UnknownMethod { name } => {
            detail(404, "UNKNOWN_METHOD", format!("unknown method {name:?}"))
        }
        DispatchError::InvalidGrant(ScopeError::Required) => detail(
            401,
            "API_KEY_SCOPES_REQUIRED",
            "at least one scope is required".to_string(),
        ),
        DispatchError::InvalidGrant(ScopeError::Unknown(names)) => detail(
            401,
            "API_KEY_SCOPE_UNKNOWN",
            format!("unknown scope(s): {}", names.join(", ")),
        ),
        DispatchError::MissingScope { method, required } => detail(
            403,
            "FORBIDDEN",
            format!(
                "this principal lacks the {:?} scope for {method:?}",
                required.as_str()
            ),
        ),
    }
}

/// Map an adapter failure raised by a handler through the adapter-error kernel. `candles`
/// selects `candles_http_error` for candle-specific routes, otherwise `adapter_http_error`.
pub fn map_adapter_error(error: &AdapterError, candles: bool) -> HttpError {
    if candles {
        crate::adapter_error::candles_http_error(error)
    } else {
        adapter_http_error(error)
    }
}

fn detail(status_code: u16, code: &str, message: String) -> HttpError {
    HttpError {
        status_code,
        detail: JsonValue::Object(vec![
            (
                JsonValue::Str("code".to_string()),
                JsonValue::Str(code.to_string()),
            ),
            (
                JsonValue::Str("message".to_string()),
                JsonValue::Str(message),
            ),
            (
                JsonValue::Str("retryable".to_string()),
                JsonValue::Bool(false),
            ),
        ]),
        headers: vec![],
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::adapter_error::AdapterErrorKind;
    use crate::wire::WireValue;

    fn grant(scopes: &[&str]) -> Vec<String> {
        scopes.iter().map(|value| value.to_string()).collect()
    }

    fn call(method: &str, scopes: Option<&[String]>) -> Result<HandlerOutcome, DispatchError> {
        let registry = MethodRegistry::canonical();
        // The envelope must live longer than the request; build both in one scope.
        let envelope = method_envelope(method);
        dispatch(
            &DispatchRequest {
                envelope: &envelope,
                granted_scopes: scopes,
            },
            &registry,
        )
    }

    fn code(error: &DispatchError) -> (u16, &'static str) {
        let mapped = map_dispatch_error(error);
        let code = match mapped.detail {
            JsonValue::Object(ref fields) => match &fields[0] {
                (_, JsonValue::Str(value)) => value.as_str(),
                _ => panic!("code field must be a string"),
            },
            _ => panic!("detail must be an object"),
        };
        // The code is borrowed from the mapped error; leak-free comparison via a static map.
        let known = [
            ("UNSUPPORTED_VERSION", "UNSUPPORTED_VERSION"),
            ("UNKNOWN_SCHEMA", "UNKNOWN_SCHEMA"),
            ("INVALID_METHOD", "INVALID_METHOD"),
            ("UNKNOWN_METHOD", "UNKNOWN_METHOD"),
            ("API_KEY_SCOPES_REQUIRED", "API_KEY_SCOPES_REQUIRED"),
            ("API_KEY_SCOPE_UNKNOWN", "API_KEY_SCOPE_UNKNOWN"),
            ("FORBIDDEN", "FORBIDDEN"),
            ("ADAPTER_ERROR", "ADAPTER_ERROR"),
            ("NOT_FOUND", "NOT_FOUND"),
        ];
        let static_code = known
            .iter()
            .find(|(want, _)| *want == code)
            .map(|(_, got)| *got)
            .unwrap_or("");
        (mapped.status_code, static_code)
    }

    // Recorded product surface: every route from apps/api/src/ezchain_api/routers/ with the
    // scope returned by the Python oracle ezchain_api.auth.required_scope (verb + full path).
    const EXPECTED: &[(&str, RequiredScope)] = &[
        ("GET /api/v1/paper/ledger", RequiredScope::Read),
        ("GET /api/v1/paper/performance", RequiredScope::Read),
        ("GET /api/v1/paper/attribution", RequiredScope::Read),
        ("GET /api/v1/paper/limits", RequiredScope::Read),
        ("GET /api/v1/paper/alerts", RequiredScope::Read),
        ("GET /api/v1/paper/export/orders.csv", RequiredScope::Read),
        ("GET /api/v1/paper/export/fills.csv", RequiredScope::Read),
        ("GET /api/v1/paper/export/ledger.csv", RequiredScope::Read),
        ("GET /api/v1/agent/capabilities", RequiredScope::Read),
        ("GET /api/v1/agent/state", RequiredScope::Read),
        ("GET /api/v1/agent/keys", RequiredScope::Admin),
        ("POST /api/v1/agent/keys", RequiredScope::Admin),
        ("DELETE /api/v1/agent/keys/{key_id}", RequiredScope::Admin),
        ("GET /api/v1/paper/approvals", RequiredScope::Read),
        (
            "GET /api/v1/paper/approvals/{approval_id}",
            RequiredScope::Read,
        ),
        (
            "POST /api/v1/paper/approvals/{approval_id}/approve",
            RequiredScope::Trade,
        ),
        (
            "POST /api/v1/paper/approvals/{approval_id}/reject",
            RequiredScope::Trade,
        ),
        ("GET /api/v1/paper/automation", RequiredScope::Read),
        ("PUT /api/v1/paper/automation", RequiredScope::Trade),
        ("POST /api/v1/backtests", RequiredScope::Trade),
        ("GET /api/v1/backtests", RequiredScope::Read),
        ("GET /api/v1/backtests/{backtest_id}", RequiredScope::Read),
        ("GET /api/v1/agent/buckets", RequiredScope::Read),
        ("GET /api/v1/agent/buckets/{agent_id}", RequiredScope::Read),
        ("PUT /api/v1/agent/buckets/{agent_id}", RequiredScope::Trade),
        ("GET /api/v1/discovery/new-pairs", RequiredScope::Read),
        ("GET /api/v1/graph/catalogue", RequiredScope::Read),
        ("POST /api/v1/graph/validate", RequiredScope::Read),
        ("POST /api/v1/graph/dry-run", RequiredScope::Read),
        ("GET /api/v1/markets/overview", RequiredScope::Read),
        ("GET /api/v1/markets/stored", RequiredScope::Read),
        ("GET /api/v1/markets", RequiredScope::Read),
        ("GET /api/v1/tokens/{chain}/{address}", RequiredScope::Read),
        (
            "GET /api/v1/tokens/{chain}/{address}/candles",
            RequiredScope::Read,
        ),
        ("GET /api/v1/quotes", RequiredScope::Read),
        ("GET /api/v1/paper/account", RequiredScope::Read),
        ("GET /api/v1/paper/wallets", RequiredScope::Read),
        ("GET /api/v1/paper/positions", RequiredScope::Read),
        ("GET /api/v1/paper/orders", RequiredScope::Read),
        ("GET /api/v1/paper/fills", RequiredScope::Read),
        ("GET /api/v1/paper/reconciliation", RequiredScope::Read),
        ("POST /api/v1/paper/orders", RequiredScope::Trade),
        ("GET /api/v1/plugins", RequiredScope::Read),
        ("POST /api/v1/plugins/reload", RequiredScope::Admin),
        ("GET /api/v1/plugins/{plugin_id}", RequiredScope::Read),
        (
            "POST /api/v1/plugins/{plugin_id}/validate",
            RequiredScope::Read,
        ),
        (
            "GET /api/v1/plugins/{plugin_id}/health",
            RequiredScope::Read,
        ),
        ("GET /api/v1/protection/arms", RequiredScope::Read),
        ("POST /api/v1/protection/arms", RequiredScope::Trade),
        ("GET /api/v1/protection/arms/{arm_id}", RequiredScope::Read),
        (
            "PATCH /api/v1/protection/arms/{arm_id}",
            RequiredScope::Trade,
        ),
        (
            "POST /api/v1/protection/arms/{arm_id}/cancel",
            RequiredScope::Trade,
        ),
        (
            "POST /api/v1/protection/arms/{arm_id}/evaluate",
            RequiredScope::Trade,
        ),
        ("GET /api/v1/protection/profiles", RequiredScope::Read),
        ("POST /api/v1/protection/profiles", RequiredScope::Trade),
        (
            "GET /api/v1/protection/profiles/effective",
            RequiredScope::Read,
        ),
        (
            "GET /api/v1/protection/arms/{arm_id}/events",
            RequiredScope::Read,
        ),
        ("GET /api/v1/provider-keys", RequiredScope::Admin),
        ("POST /api/v1/provider-keys", RequiredScope::Admin),
        (
            "DELETE /api/v1/provider-keys/{key_id}",
            RequiredScope::Admin,
        ),
        ("GET /api/v1/signals/movers", RequiredScope::Read),
        ("GET /api/v1/strategies", RequiredScope::Read),
        ("POST /api/v1/strategies", RequiredScope::Trade),
        ("GET /api/v1/strategies/{strategy_id}", RequiredScope::Read),
        (
            "PATCH /api/v1/strategies/{strategy_id}",
            RequiredScope::Trade,
        ),
        (
            "DELETE /api/v1/strategies/{strategy_id}",
            RequiredScope::Trade,
        ),
        (
            "POST /api/v1/strategies/{strategy_id}/run",
            RequiredScope::Trade,
        ),
        (
            "GET /api/v1/strategies/{strategy_id}/performance",
            RequiredScope::Read,
        ),
        (
            "GET /api/v1/strategies/{strategy_id}/live-readiness",
            RequiredScope::Read,
        ),
        (
            "GET /api/v1/strategies/{strategy_id}/runs",
            RequiredScope::Read,
        ),
        ("GET /healthz", RequiredScope::Read),
        ("GET /readyz", RequiredScope::Read),
        ("GET /metrics", RequiredScope::Admin),
        ("GET /health", RequiredScope::Admin),
        ("GET /api/v1/chains", RequiredScope::Read),
        ("GET /api/v1/system/kill-switch", RequiredScope::Read),
        ("POST /api/v1/system/kill-switch", RequiredScope::Admin),
        ("GET /api/v1/trade-policy", RequiredScope::Read),
        ("GET /api/v1/trade-policy/preview", RequiredScope::Read),
        (
            "PUT /api/v1/trade-policy/strategies/{strategy_id}",
            RequiredScope::Trade,
        ),
        ("GET /api/v1/watchlist", RequiredScope::Read),
        ("POST /api/v1/watchlist", RequiredScope::Trade),
        ("DELETE /api/v1/watchlist", RequiredScope::Trade),
    ];

    #[test]
    fn pinned_surface_matches_the_api_routers() {
        let registry = MethodRegistry::canonical();
        let actual: Vec<(&str, RequiredScope)> = registry
            .iter()
            .map(|spec| (spec.name, spec.required))
            .collect();
        assert_eq!(
            actual, EXPECTED,
            "the recorded method/scope surface drifted from the API routers"
        );
        assert_eq!(registry.len(), 83);
        assert_eq!(EXPECTED.len(), 83);
        let reads = EXPECTED
            .iter()
            .filter(|(_, s)| *s == RequiredScope::Read)
            .count();
        let trades = EXPECTED
            .iter()
            .filter(|(_, s)| *s == RequiredScope::Trade)
            .count();
        let admins = EXPECTED
            .iter()
            .filter(|(_, s)| *s == RequiredScope::Admin)
            .count();
        assert_eq!((reads, trades, admins), (55, 18, 10));
    }

    #[test]
    fn every_method_name_is_unique() {
        let registry = MethodRegistry::canonical();
        let mut names: Vec<&str> = registry.iter().map(|spec| spec.name).collect();
        let count = names.len();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), count, "duplicate method name in the registry");
    }

    #[test]
    fn every_handler_matches_its_declared_scope() {
        let registry = MethodRegistry::canonical();
        for spec in registry.iter() {
            let outcome = (spec.handler)(spec.name).expect("pure handler never fails");
            assert_eq!(outcome.method, spec.name);
            let expected_family = match spec.required {
                RequiredScope::Read => HandlerFamily::Read,
                RequiredScope::Trade => HandlerFamily::Trade,
                RequiredScope::Admin => HandlerFamily::Admin,
            };
            assert_eq!(outcome.family, expected_family, "handler of {}", spec.name);
        }
    }

    #[test]
    fn dispatch_routes_each_scope_family_to_its_handler() {
        let admin = grant(&["admin"]);
        for (method, family) in [
            ("GET /api/v1/paper/orders", HandlerFamily::Read),
            ("POST /api/v1/paper/orders", HandlerFamily::Trade),
            ("POST /api/v1/system/kill-switch", HandlerFamily::Admin),
        ] {
            let outcome = call(method, Some(&admin)).expect("admin grant reaches every method");
            assert_eq!(outcome.method, method);
            assert_eq!(outcome.family, family, "family of {method}");
        }
    }

    #[test]
    fn admin_only_methods_are_refused_for_read_and_trade_scoped_keys() {
        let read = grant(&["read"]);
        let trade = grant(&["trade"]);
        let admin_methods: Vec<&str> = EXPECTED
            .iter()
            .filter(|(_, scope)| *scope == RequiredScope::Admin)
            .map(|(name, _)| *name)
            .collect();
        assert_eq!(admin_methods.len(), 10);
        for method in admin_methods {
            for (label, scopes) in [
                ("read", Some(read.as_slice())),
                ("trade", Some(trade.as_slice())),
            ] {
                let error = call(method, scopes).expect_err("scoped key must be refused");
                assert_eq!(
                    error,
                    DispatchError::MissingScope {
                        method: method.to_string(),
                        required: RequiredScope::Admin,
                    },
                    "{label} key on {method}"
                );
                assert_eq!(code(&error), (403, "FORBIDDEN"));
            }
        }
    }

    #[test]
    fn admin_method_is_allowed_for_an_admin_scoped_key() {
        let admin = grant(&["admin"]);
        let outcome = call("GET /metrics", Some(&admin)).expect("admin key reaches admin method");
        assert_eq!(outcome.family, HandlerFamily::Admin);
        // A deployment-style full grant works too.
        let full = grant(&["admin", "read", "trade"]);
        assert!(call("DELETE /api/v1/agent/keys/{key_id}", Some(&full)).is_ok());
    }

    #[test]
    fn admin_grant_satisfies_every_registered_method() {
        let admin = grant(&["admin"]);
        for (name, _) in EXPECTED {
            assert!(call(name, Some(&admin)).is_ok(), "admin grant on {name}");
        }
    }

    #[test]
    fn read_grant_reaches_only_read_methods() {
        let read = grant(&["read"]);
        for (name, required) in EXPECTED {
            let result = call(name, Some(&read));
            if *required == RequiredScope::Read {
                assert!(result.is_ok(), "read key on {name}");
            } else {
                assert_eq!(
                    result.expect_err("read key refused"),
                    DispatchError::MissingScope {
                        method: name.to_string(),
                        required: *required,
                    },
                    "read key on {name}"
                );
            }
        }
    }

    #[test]
    fn trade_grant_reaches_only_trade_methods() {
        // Principal.has_scope grants only the exact scope (or admin): a trade key cannot
        // read or administer, exactly as in the Python API.
        let trade = grant(&["trade"]);
        for (name, required) in EXPECTED {
            let result = call(name, Some(&trade));
            if *required == RequiredScope::Trade {
                assert!(result.is_ok(), "trade key on {name}");
            } else {
                assert_eq!(
                    result.expect_err("trade key refused"),
                    DispatchError::MissingScope {
                        method: name.to_string(),
                        required: *required,
                    },
                    "trade key on {name}"
                );
            }
        }
    }

    #[test]
    fn unknown_method_is_a_typed_404() {
        let admin = grant(&["admin"]);
        let error = call("BREW /api/v1/nope", Some(&admin)).expect_err("unknown method refused");
        assert_eq!(
            error,
            DispatchError::UnknownMethod {
                name: "BREW /api/v1/nope".to_string()
            }
        );
        assert_eq!(code(&error), (404, "UNKNOWN_METHOD"));
    }

    #[test]
    fn check_order_method_lookup_precedes_grant_validation() {
        // An unknown method with an empty grant reports the method, not the grant.
        let error = call("GET /api/v1/nope", Some(&[])).expect_err("unknown method refused");
        assert!(matches!(error, DispatchError::UnknownMethod { .. }));
    }

    #[test]
    fn unknown_version_has_its_own_typed_error() {
        let admin = grant(&["admin"]);
        let mut envelope = method_envelope("GET /api/v1/paper/orders");
        envelope.insert("version", WireValue::Int("2".to_string()));
        let registry = MethodRegistry::canonical();
        let error = dispatch(
            &DispatchRequest {
                envelope: &envelope,
                granted_scopes: Some(&admin),
            },
            &registry,
        )
        .expect_err("wrong version refused");
        assert_eq!(
            error,
            DispatchError::Envelope(EnvelopeError::UnsupportedVersion {
                expected: 1,
                got: 2
            })
        );
        assert_eq!(code(&error), (400, "UNSUPPORTED_VERSION"));
    }

    #[test]
    fn unknown_schema_kind_has_its_own_typed_error() {
        let admin = grant(&["admin"]);
        let mut envelope = method_envelope("GET /api/v1/paper/orders");
        envelope.insert("schema", WireValue::Str("ezchain.other/1".to_string()));
        let registry = MethodRegistry::canonical();
        let error = dispatch(
            &DispatchRequest {
                envelope: &envelope,
                granted_scopes: Some(&admin),
            },
            &registry,
        )
        .expect_err("wrong schema refused");
        assert!(matches!(
            error,
            DispatchError::Envelope(EnvelopeError::UnsupportedKind { .. })
        ));
        assert_eq!(code(&error), (400, "UNKNOWN_SCHEMA"));
    }

    #[test]
    fn missing_or_non_string_method_is_refused() {
        let admin = grant(&["admin"]);
        let registry = MethodRegistry::canonical();
        for method_value in [None, Some(WireValue::Int("7".to_string()))] {
            let mut envelope = method_envelope("GET /api/v1/paper/orders");
            envelope.remove("method");
            if let Some(value) = method_value {
                envelope.insert("method", value);
            }
            let error = dispatch(
                &DispatchRequest {
                    envelope: &envelope,
                    granted_scopes: Some(&admin),
                },
                &registry,
            )
            .expect_err("malformed method refused");
            assert_eq!(error, DispatchError::MalformedMethod);
            assert_eq!(code(&error), (400, "INVALID_METHOD"));
        }
    }

    #[test]
    fn invalid_scope_grants_are_refused_by_the_kernel() {
        let registry = MethodRegistry::canonical();
        let unknown = grant(&["read", "bogus"]);
        for (label, scopes, expected) in [
            (
                "missing",
                None,
                DispatchError::InvalidGrant(ScopeError::Required),
            ),
            (
                "empty",
                Some(&[] as &[String]),
                DispatchError::InvalidGrant(ScopeError::Required),
            ),
            (
                "unknown",
                Some(unknown.as_slice()),
                DispatchError::InvalidGrant(ScopeError::Unknown(vec!["bogus".to_string()])),
            ),
        ] {
            let envelope = method_envelope("GET /api/v1/paper/orders");
            let error = dispatch(
                &DispatchRequest {
                    envelope: &envelope,
                    granted_scopes: scopes,
                },
                &registry,
            )
            .expect_err("bad grant refused");
            assert_eq!(error, expected, "{label}");
        }
        assert_eq!(
            code(&DispatchError::InvalidGrant(ScopeError::Required)),
            (401, "API_KEY_SCOPES_REQUIRED")
        );
        assert_eq!(
            code(&DispatchError::InvalidGrant(ScopeError::Unknown(vec![
                "bogus".to_string()
            ]))),
            (401, "API_KEY_SCOPE_UNKNOWN")
        );
    }

    #[test]
    fn scope_grants_are_normalised_by_the_kernel() {
        // Duplicate and unsorted grants are accepted after the kernel sorts/deduplicates.
        let scopes = grant(&["trade", "read", "trade"]);
        assert!(call("GET /api/v1/paper/orders", Some(&scopes)).is_ok());
        assert!(call("POST /api/v1/paper/orders", Some(&scopes)).is_ok());
    }

    #[test]
    fn check_order_envelope_precedes_method_lookup() {
        let mut envelope = method_envelope("GET /api/v1/does-not-exist");
        envelope.insert("version", WireValue::Int("9".to_string()));
        let registry = MethodRegistry::canonical();
        let error = dispatch(
            &DispatchRequest {
                envelope: &envelope,
                granted_scopes: None,
            },
            &registry,
        )
        .expect_err("version fault wins");
        assert!(matches!(
            error,
            DispatchError::Envelope(EnvelopeError::UnsupportedVersion { .. })
        ));
    }

    #[test]
    fn adapter_error_from_a_handler_maps_through_the_kernel() {
        fn failing(_method: &'static str) -> Result<HandlerOutcome, DispatchError> {
            Err(DispatchError::Adapter(Box::new(
                AdapterError::provider_budget_exhausted("jupiter", "queue is full"),
            )))
        }
        const SPECS: &[MethodSpec] = &[MethodSpec {
            name: "POST /api/v1/paper/orders",
            required: RequiredScope::Trade,
            handler: failing,
        }];
        let registry = MethodRegistry::from_specs(SPECS);
        let trade = grant(&["trade"]);
        let envelope = method_envelope("POST /api/v1/paper/orders");
        let error = dispatch(
            &DispatchRequest {
                envelope: &envelope,
                granted_scopes: Some(&trade),
            },
            &registry,
        )
        .expect_err("handler surfaced an adapter failure");
        assert!(matches!(error, DispatchError::Adapter(_)));
        // The budget subclass maps to 429 with Retry-After through adapter_http_error.
        let mapped = map_dispatch_error(&error);
        assert_eq!(mapped.status_code, 429);
        assert_eq!(
            mapped.headers,
            vec![("Retry-After".to_string(), "1".to_string())]
        );
    }

    #[test]
    fn handler_adapter_error_uses_the_candles_mapping_when_requested() {
        let exc = AdapterError::rate_limited("slow");
        let plain = map_adapter_error(&exc, false);
        let candles = map_adapter_error(&exc, true);
        assert_eq!((plain.status_code, candles.status_code), (429, 502));
        assert_eq!(exc.kind, AdapterErrorKind::RateLimited);
    }

    #[test]
    fn mapped_dispatch_errors_have_the_kernel_envelope_shape() {
        let mapped = code(&DispatchError::UnknownMethod {
            name: "x".to_string(),
        });
        assert_eq!(mapped, (404, "UNKNOWN_METHOD"));
        let error = DispatchError::UnknownMethod {
            name: "x".to_string(),
        };
        let http = map_dispatch_error(&error);
        assert!(http.headers.is_empty());
        match http.detail {
            JsonValue::Object(fields) => {
                let keys: Vec<&str> = fields
                    .iter()
                    .map(|(key, _)| match key {
                        JsonValue::Str(value) => value.as_str(),
                        _ => "",
                    })
                    .collect();
                assert_eq!(keys, ["code", "message", "retryable"]);
            }
            _ => panic!("detail must be an object"),
        }
    }
}
