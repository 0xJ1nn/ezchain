//! Node-facing RPC transport for ezCHAIN (ADR-0050/0052).
//!
//! This crate is the **node transport**, not the product-facing HTTP API. It owns the socket:
//! it accepts TCP connections, serves a versioned health/readiness surface, reads one framed
//! message, feeds its versioned envelope into the existing pure [`ezchain_rpc::dispatch`]
//! kernel and writes the typed result or error back through the same framing codec. It then
//! closes cleanly on a graceful-shutdown signal.
//!
//! The product-facing HTTP API, its WebSocket gateway and third-party integrations stay in
//! Python in `apps/api` (ADR-0052). This crate contains no auth tokens, keys, money arithmetic,
//! ledger writes or venue calls: the handlers it reaches are the pure typed acks already
//! registered in the dispatch kernel. Resolving a real principal and wiring real handlers are
//! deliberately left for a later slice — see `README.md`.
//!
//! # Wire contract
//!
//! - `GET /v1/healthz`, `GET /v1/readyz`: plain text, prefixed with
//!   [`NODE_TRANSPORT_SCHEMA`]. Liveness/readiness only; no data leaves the node.
//! - `POST /v1/rpc`: the request body is exactly one frame from [`ezchain_rpc::framing`] whose
//!   kind is `Request`. Its payload is a [`codec`] field map in the shape built by
//!   [`ezchain_rpc::dispatch::method_envelope`] (`schema`, `version`, `method`). The response
//!   body is exactly one `Response` frame whose payload is a typed success ack or typed error.
//!
//! Incoming bodies are bounded by [`Config::limits`], which is the existing framing limit; the
//! router's default body limit is disabled so there is one home for that number (CONTRACT I7).
//! Server-generated frames use a small fixed budget [`RESPONSE_FRAME_LIMIT`] so a tiny request
//! budget can never stop an error from being reported.

#![forbid(unsafe_code)]

pub mod codec;

use std::future::Future;

use axum::body::{to_bytes, Body};
use axum::extract::{DefaultBodyLimit, State};
use axum::http::{header, Request, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::Router;
use ezchain_rpc::dispatch::{dispatch, map_dispatch_error, DispatchRequest, MethodRegistry};
use ezchain_rpc::framing;
use ezchain_rpc::json_safe::JsonValue;
use ezchain_rpc::wire::FieldMap;
use tokio::net::TcpListener;

/// Stable schema id carried on the versioned health/readiness responses.
pub const NODE_TRANSPORT_SCHEMA: &str = "ezchain.rpc-node/1";

/// Default payload budget, reused from the framing kernel's own contract style (bytes).
pub const DEFAULT_MAX_PAYLOAD_BYTES: usize = 64 * 1024;

/// Server-generated frames are small; their budget is independent of the caller's request
/// budget so even a rejected oversize request can be answered with a typed error frame.
const RESPONSE_FRAME_LIMIT: framing::Limits = framing::Limits {
    max_payload_bytes: DEFAULT_MAX_PAYLOAD_BYTES,
};

/// Server configuration. `limits` is the single source of truth for the inbound body cap;
/// `granted_scopes` is a temporary placeholder for a principal resolved by the Python API.
#[derive(Debug, Clone)]
pub struct Config {
    pub limits: framing::Limits,
    pub granted_scopes: Vec<String>,
}

impl Default for Config {
    fn default() -> Self {
        Self {
            limits: framing::Limits {
                max_payload_bytes: DEFAULT_MAX_PAYLOAD_BYTES,
            },
            granted_scopes: vec!["read".to_string()],
        }
    }
}

impl Config {
    /// Build a configuration around an explicit framing limit with a read-only placeholder
    /// grant. No token or key is accepted from the wire.
    pub fn new(limits: framing::Limits) -> Self {
        Self {
            limits,
            granted_scopes: vec!["read".to_string()],
        }
    }

    /// Replace the placeholder grant. Real auth resolution is not wired yet (ADR-0052).
    pub fn with_granted_scopes(mut self, scopes: Vec<String>) -> Self {
        self.granted_scopes = scopes;
        self
    }
}

/// Build the node-transport router. Kept public so a host can mount or test it directly.
pub fn router(config: Config) -> Router {
    Router::new()
        .route("/v1/healthz", get(healthz))
        .route("/v1/readyz", get(readyz))
        .route("/v1/rpc", post(rpc))
        // The framing limit is the only body limit; axum's 2 MiB default is disabled so a
        // caller cannot make the server accept more than the codec is willing to decode.
        .layer(DefaultBodyLimit::disable())
        .with_state(config)
}

/// Serve TCP connections until `shutdown` resolves, then drain in-flight requests.
pub async fn serve<F>(listener: TcpListener, config: Config, shutdown: F) -> std::io::Result<()>
where
    F: Future<Output = ()> + Send + 'static,
{
    axum::serve(listener, router(config))
        .with_graceful_shutdown(shutdown)
        .await
}

async fn healthz() -> String {
    format!("{NODE_TRANSPORT_SCHEMA} healthz ok\n")
}

async fn readyz() -> String {
    format!("{NODE_TRANSPORT_SCHEMA} readyz ok\n")
}

async fn rpc(State(config): State<Config>, req: Request<Body>) -> Response {
    let body = match to_bytes(req.into_body(), config.limits.max_payload_bytes).await {
        Ok(body) => body,
        Err(_) => {
            return error_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                "BODY_TOO_LARGE",
                "request body exceeds the configured frame limit",
                false,
            )
        }
    };
    handle_rpc(&config, &body)
}

/// Pure request → framed-response step. No I/O here, so malformed bytes cannot stall or panic
/// the server; every branch returns a typed frame.
fn handle_rpc(config: &Config, body: &[u8]) -> Response {
    let frame = match framing::decode(body, config.limits) {
        Ok(frame) => frame,
        Err(err) => {
            return error_response(
                StatusCode::BAD_REQUEST,
                "FRAME_DECODE_ERROR",
                &format!("{err:?}"),
                false,
            )
        }
    };
    if frame.kind != framing::Kind::Request {
        return error_response(
            StatusCode::BAD_REQUEST,
            "UNEXPECTED_FRAME_KIND",
            "the RPC endpoint accepts only Request frames",
            false,
        );
    }
    let envelope = match codec::decode_field_map(&frame.payload) {
        Ok(map) => map,
        Err(err) => {
            return error_response(
                StatusCode::BAD_REQUEST,
                "MALFORMED_ENVELOPE",
                &format!("{err:?}"),
                false,
            )
        }
    };

    let request = DispatchRequest {
        envelope: &envelope,
        granted_scopes: Some(config.granted_scopes.as_slice()),
    };
    match dispatch(&request, &MethodRegistry::canonical()) {
        Ok(outcome) => framed_response(StatusCode::OK, &codec::outcome_field_map(&outcome)),
        Err(err) => {
            let http = map_dispatch_error(&err);
            let (code, message, retryable) = detail_parts(&http.detail);
            let status = StatusCode::from_u16(http.status_code).unwrap_or(StatusCode::BAD_REQUEST);
            error_response(status, &code, &message, retryable)
        }
    }
}

/// Pull the stable `{code, message, retryable}` fields out of the kernel's HTTP detail object.
/// Unknown shapes keep safe defaults instead of guessing a code.
fn detail_parts(detail: &JsonValue) -> (String, String, bool) {
    let mut code = "ADAPTER_ERROR".to_string();
    let mut message = "dispatch refused the request".to_string();
    let mut retryable = false;
    if let JsonValue::Object(fields) = detail {
        for (key, value) in fields {
            let name = match key {
                JsonValue::Str(name) => name.as_str(),
                _ => continue,
            };
            match name {
                "code" => {
                    if let JsonValue::Str(text) = value {
                        code = text.clone();
                    }
                }
                "message" => {
                    if let JsonValue::Str(text) = value {
                        message = text.clone();
                    }
                }
                "retryable" => {
                    if let JsonValue::Bool(flag) = value {
                        retryable = *flag;
                    }
                }
                _ => {}
            }
        }
    }
    (code, message, retryable)
}

fn error_response(status: StatusCode, code: &str, message: &str, retryable: bool) -> Response {
    framed_response(status, &codec::error_field_map(code, message, retryable))
}

/// Frame a typed field map as a `Response` frame. If our own generated payload cannot be
/// framed, report a plain typed error rather than panicking.
fn framed_response(status: StatusCode, map: &FieldMap) -> Response {
    let payload = match codec::encode_field_map(map) {
        Ok(payload) => payload,
        Err(err) => {
            return plain_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "ENCODE_ERROR",
                &format!("{err:?}"),
            )
        }
    };
    let frame = framing::Frame::new(framing::Kind::Response, payload);
    let bytes = match framing::encode(&frame, RESPONSE_FRAME_LIMIT) {
        Ok(bytes) => bytes,
        Err(err) => {
            return plain_error(
                StatusCode::INTERNAL_SERVER_ERROR,
                "ENCODE_ERROR",
                &format!("{err:?}"),
            )
        }
    };
    (
        status,
        [(header::CONTENT_TYPE, "application/octet-stream")],
        bytes,
    )
        .into_response()
}

fn plain_error(status: StatusCode, code: &str, message: &str) -> Response {
    (
        status,
        [(header::CONTENT_TYPE, "text/plain")],
        format!("{code}: {message}\n"),
    )
        .into_response()
}

#[cfg(test)]
mod tests {
    use super::*;
    use ezchain_rpc::json_safe::JsonValue;

    #[test]
    fn default_config_uses_the_framing_budget_and_a_read_only_placeholder_grant() {
        let config = Config::default();
        assert_eq!(config.limits.max_payload_bytes, DEFAULT_MAX_PAYLOAD_BYTES);
        assert_eq!(config.granted_scopes, vec!["read".to_string()]);
    }

    #[test]
    fn detail_parts_reads_the_kernel_envelope_and_defaults_on_unknown_shapes() {
        let detail = JsonValue::Object(vec![
            (
                JsonValue::Str("code".to_string()),
                JsonValue::Str("FORBIDDEN".to_string()),
            ),
            (
                JsonValue::Str("message".to_string()),
                JsonValue::Str("denied".to_string()),
            ),
            (
                JsonValue::Str("retryable".to_string()),
                JsonValue::Bool(false),
            ),
        ]);
        assert_eq!(
            detail_parts(&detail),
            ("FORBIDDEN".to_string(), "denied".to_string(), false)
        );
        assert_eq!(
            detail_parts(&JsonValue::Null),
            (
                "ADAPTER_ERROR".to_string(),
                "dispatch refused the request".to_string(),
                false
            )
        );
    }
}
