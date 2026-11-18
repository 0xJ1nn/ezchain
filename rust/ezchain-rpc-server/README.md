# ezchain-rpc-server — node-facing RPC transport

This is the **node transport**: the socket a node exposes to other nodes and to privileged
node clients. It is **not** the product-facing HTTP API. Under ADR-0052 the product's HTTP
API, WebSocket gateway in `apps/api` and third-party integrations remain in **Python**; only
the node-facing RPC/WebSocket transport is Rust (ADR-0050/0052). Do not move the Python API
into this crate.

The crate wires existing pure kernels together — it does not reimplement them:

- `ezchain_rpc::framing` — the six-byte length-prefixed frame codec (version, kind, `u32`
  big-endian length, payload) and its typed `DecodeError`.
- `ezchain_rpc::wire` — the versioned `FieldMap` / `WireValue` records.
- `ezchain_rpc::dispatch` — the method/scope registry and the pure typed handlers.

## Server contract

| Route | Method | Body | Reply |
|---|---|---|---|
| `/v1/healthz` | GET | empty | `200`, `text/plain`, `ezchain.rpc-node/1 healthz ok` — liveness |
| `/v1/readyz` | GET | empty | `200`, `text/plain`, `ezchain.rpc-node/1 readyz ok` — readiness |
| `/v1/rpc` | POST | exactly one `framing::Frame` of kind `Request` | exactly one `Frame` of kind `Response` |

The `/v1/rpc` payload is a `codec` field map in the shape produced by
`ezchain_rpc::dispatch::method_envelope`: `schema = "ezchain.rpc-method/1"`, `version = "1"`
and a string `method` naming one registered `"VERB /path"` method. The response payload is one
of two typed records:

- success — `status = "ok"`, `method`, `family` (`read` / `trade` / `admin`);
- error — `status = "error"`, `code`, `message`, `retryable`, where dispatch errors reuse the
  kernel's `map_dispatch_error` codes and HTTP status (400/401/403/404/429).

Transport faults have their own codes and are never panics: `BODY_TOO_LARGE` (413),
`FRAME_DECODE_ERROR`, `UNEXPECTED_FRAME_KIND` and `MALFORMED_ENVELOPE` (400). A generated frame
that somehow cannot be encoded yields a typed plain-text `ENCODE_ERROR` (500) instead of an
`unwrap`. All decode paths are strict: extra bytes, unknown tags, duplicate fields, non-UTF-8
scalars and lying length prefixes are typed refusals.

### Limits and shutdown

Incoming bodies are bounded by `Config::limits.max_payload_bytes`, which **is** the framing
limit; axum's default 2 MiB body limit is disabled so there is one home for that number
(CONTRACT I7). The body is checked before the frame is decoded, so a lying header cannot force
an allocation. Server-generated frames use a separate, small fixed budget so an oversize
request can still be answered with a typed error frame.

`serve(listener, config, shutdown)` uses axum's graceful shutdown: when the supplied future
resolves the listener stops accepting and in-flight requests drain. The integration test
starts on `127.0.0.1:0`, speaks raw HTTP/1.1 over a tokio TCP stream, asserts the framed reply
and triggers that shutdown signal. No external network access and no HTTP client crate.

## Dependencies — exactly two external crates, pinned

| Crate | Version | Why it is needed |
|---|---|---|
| `tokio` | `=1.54.1` | Async runtime (`rt-multi-thread`, `macros`), TCP listener/stream (`net`), request body I/O (`io-util`), the graceful-shutdown oneshot (`sync`) and the test shutdown timeout (`time`). |
| `axum` | `=0.8.9` | HTTP/1.1 routing for the versioned health/readiness and `/v1/rpc` routes, request-body extraction with a typed length error, and `serve(...).with_graceful_shutdown`. ADR-0052 authorizes networking in Rust. |

Both are standard, widely audited crates. Their own transitive dependencies (hyper, tower,
http, etc.) are **not** direct dependencies of this crate — the `Cargo.toml` direct set is
exactly tokio + axum plus the in-repo `ezchain-rpc` kernel crate. No serde, no auth crate, no
HTTP client crate, no crypto and no money library are introduced here.

## What remains (intentionally not in this slice)

- **Auth resolution.** `Config::granted_scopes` is a static placeholder for a resolved
  principal; no token, key or credential is accepted on the wire. Resolving a real principal
  (PyO3 in-process or gRPC/IPC to the Python API, per ADR-0052) is still to be wired.
- **Real handlers.** Dispatch currently runs the kernel's pure typed ack handlers. Executing
  actual node operations, and the node-facing WebSocket/P2P transport, are separate slices.
- No ledger writes, money arithmetic, venue calls, telemetry or key material live in this
  crate (CONTRACT I1, I4, ADR-0050 rule 4).

## Gate

```bash
export CARGO_HOME="$SCRATCH/cargo" RUSTUP_HOME="$SCRATCH/rustup" PATH="$SCRATCH/cargo/bin:$PATH"
cd rust
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```
