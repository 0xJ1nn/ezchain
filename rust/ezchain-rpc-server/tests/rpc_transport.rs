//! Real transport test: bind an ephemeral localhost port, speak HTTP/1.1 and the framing
//! codec over a raw tokio TCP stream, assert the framed reply, then shut the server down.
//! No external network access and no HTTP client crate — only tokio and the crate under test.

use axum::http::StatusCode;
use ezchain_rpc::dispatch::method_envelope;
use ezchain_rpc::framing::{self, Kind};
use ezchain_rpc::wire::{FieldMap, WireValue};
use ezchain_rpc_server::{codec, serve, Config};
use std::net::SocketAddr;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::oneshot;
use tokio::task::JoinHandle;
use tokio::time::{timeout, Duration};

const TEST_LIMIT: framing::Limits = framing::Limits {
    max_payload_bytes: 64 * 1024,
};

struct RunningServer {
    addr: SocketAddr,
    shutdown: Option<oneshot::Sender<()>>,
    task: JoinHandle<std::io::Result<()>>,
}

impl RunningServer {
    async fn start(config: Config) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("bind an ephemeral localhost port");
        let addr = listener.local_addr().expect("local address");
        let (shutdown, receiver) = oneshot::channel();
        let task = tokio::spawn(serve(listener, config, async move {
            let _ = receiver.await;
        }));
        Self {
            addr,
            shutdown: Some(shutdown),
            task,
        }
    }

    async fn stop(self) {
        let shutdown = self.shutdown.expect("shutdown sender still present");
        shutdown
            .send(())
            .expect("server still listening for shutdown");
        let result = timeout(Duration::from_secs(5), self.task)
            .await
            .expect("graceful shutdown within five seconds");
        result
            .expect("server task did not panic")
            .expect("server shut down cleanly");
    }
}

/// Send one HTTP/1.1 request over a raw stream and read the whole response to EOF.
async fn request(addr: SocketAddr, method: &str, path: &str, body: &[u8]) -> (StatusCode, Vec<u8>) {
    let mut stream = TcpStream::connect(addr).await.expect("connect to server");
    let head = format!(
        "{method} {path} HTTP/1.1\r\nHost: {addr}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(head.as_bytes())
        .await
        .expect("write request head");
    stream.write_all(body).await.expect("write request body");
    let mut raw = Vec::new();
    stream
        .read_to_end(&mut raw)
        .await
        .expect("read response to EOF");
    parse_response(&raw)
}

fn parse_response(raw: &[u8]) -> (StatusCode, Vec<u8>) {
    let split = raw
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("HTTP header terminator");
    let headers = std::str::from_utf8(&raw[..split]).expect("ASCII headers");
    let mut lines = headers.lines();
    let status_line = lines.next().expect("status line");
    let mut parts = status_line.split_whitespace();
    let _ = parts.next();
    let code: u16 = parts
        .next()
        .expect("status code")
        .parse()
        .expect("numeric status code");
    let chunked = lines.any(|line| {
        line.split_once(':').is_some_and(|(name, value)| {
            name.eq_ignore_ascii_case("transfer-encoding")
                && value.to_ascii_lowercase().contains("chunked")
        })
    });
    let raw_body = &raw[split + 4..];
    let body = if chunked {
        decode_chunked(raw_body)
    } else {
        raw_body.to_vec()
    };
    (StatusCode::from_u16(code).expect("valid status code"), body)
}

fn decode_chunked(input: &[u8]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut rest = input;
    loop {
        let end = rest
            .windows(2)
            .position(|window| window == b"\r\n")
            .expect("chunk-size line");
        let size_text = std::str::from_utf8(&rest[..end]).expect("hex chunk size");
        let size = usize::from_str_radix(size_text.trim(), 16).expect("chunk size");
        rest = &rest[end + 2..];
        if size == 0 {
            break;
        }
        out.extend_from_slice(&rest[..size]);
        rest = &rest[size + 2..];
    }
    out
}

fn request_frame(method: &str) -> Vec<u8> {
    let envelope = method_envelope(method);
    let payload = codec::encode_field_map(&envelope).expect("encode request payload");
    framing::encode(&framing::Frame::new(Kind::Request, payload), TEST_LIMIT)
        .expect("encode request frame")
}

fn response_map(body: &[u8]) -> FieldMap {
    let frame = framing::decode(body, TEST_LIMIT).expect("response is one valid frame");
    assert_eq!(frame.kind, Kind::Response);
    codec::decode_field_map(&frame.payload).expect("response payload is a typed record")
}

fn status_of(map: &FieldMap) -> &str {
    match map.get("status") {
        Some(WireValue::Str(value)) => value.as_str(),
        other => panic!("status must be a string, got {other:?}"),
    }
}

#[tokio::test]
async fn framed_request_returns_a_typed_ack_and_the_server_shuts_down_cleanly() {
    let config = Config::default().with_granted_scopes(vec!["read".to_string()]);
    let server = RunningServer::start(config).await;

    let frame = request_frame("GET /api/v1/paper/orders");
    let (status, body) = request(server.addr, "POST", "/v1/rpc", &frame).await;
    assert_eq!(status, StatusCode::OK);

    let map = response_map(&body);
    assert_eq!(status_of(&map), "ok");
    assert_eq!(
        map.get("method"),
        Some(&WireValue::Str("GET /api/v1/paper/orders".to_string()))
    );
    assert_eq!(map.get("family"), Some(&WireValue::Str("read".to_string())));

    server.stop().await;
}

#[tokio::test]
async fn health_and_readiness_endpoints_are_versioned() {
    let server = RunningServer::start(Config::default()).await;
    for path in ["/v1/healthz", "/v1/readyz"] {
        let (status, body) = request(server.addr, "GET", path, &[]).await;
        assert_eq!(status, StatusCode::OK, "{path}");
        let text = String::from_utf8(body).expect("UTF-8 health response");
        assert!(text.starts_with("ezchain.rpc-node/1 "), "{text}");
        assert!(text.contains("ok"), "{text}");
    }
    server.stop().await;
}

#[tokio::test]
async fn malformed_bytes_produce_a_typed_error_without_panicking() {
    let server = RunningServer::start(Config::default()).await;
    let (status, body) = request(server.addr, "POST", "/v1/rpc", b"not a frame").await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    let map = response_map(&body);
    assert_eq!(status_of(&map), "error");
    assert_eq!(
        map.get("code"),
        Some(&WireValue::Str("FRAME_DECODE_ERROR".to_string()))
    );
    assert_eq!(map.get("retryable"), Some(&WireValue::Bool(false)));
    server.stop().await;
}

#[tokio::test]
async fn a_dispatch_refusal_is_encoded_as_a_typed_error() {
    // A read-only placeholder grant must not reach an admin method; the denial comes from the
    // existing dispatch kernel and is framed back with its typed code.
    let config = Config::default().with_granted_scopes(vec!["read".to_string()]);
    let server = RunningServer::start(config).await;
    let frame = request_frame("POST /api/v1/system/kill-switch");
    let (status, body) = request(server.addr, "POST", "/v1/rpc", &frame).await;
    assert_eq!(status, StatusCode::FORBIDDEN);
    let map = response_map(&body);
    assert_eq!(status_of(&map), "error");
    assert_eq!(
        map.get("code"),
        Some(&WireValue::Str("FORBIDDEN".to_string()))
    );
    server.stop().await;
}

#[tokio::test]
async fn a_body_over_the_framing_limit_is_rejected_with_a_typed_error() {
    let limits = framing::Limits {
        max_payload_bytes: 8,
    };
    let server = RunningServer::start(Config::new(limits)).await;

    let envelope = method_envelope("GET /api/v1/paper/orders");
    let payload = codec::encode_field_map(&envelope).expect("encode request payload");
    assert!(payload.len() > limits.max_payload_bytes);
    let frame = framing::encode(&framing::Frame::new(Kind::Request, payload), TEST_LIMIT)
        .expect("encode an oversize request frame");

    let (status, body) = request(server.addr, "POST", "/v1/rpc", &frame).await;
    assert_eq!(status, StatusCode::PAYLOAD_TOO_LARGE);
    let map = response_map(&body);
    assert_eq!(status_of(&map), "error");
    assert_eq!(
        map.get("code"),
        Some(&WireValue::Str("BODY_TOO_LARGE".to_string()))
    );
    server.stop().await;
}
