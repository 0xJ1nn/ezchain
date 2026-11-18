# ezchain-node — the Rust node binary

One binary that composes the two existing Rust node transports into a single process:

- `ezchain-rpc-server` — the **node-facing** RPC transport (health/readiness + framed `/v1/rpc`).
- `ezchain-p2p-transport` — the minimal tokio TCP transport that feeds validated envelopes into
  the pure `ezchain_p2p::kernel::GossipKernel`.

It owns neither transport's logic and reimplements no kernel. Under ADR-0050/0052 this is the
node-facing transport layer only; the product-facing HTTP API, its WebSocket gateway and
third-party integrations stay in Python (`apps/api`).

## Commands

```
ezchain-node check --config <file>   # validate config + validator set; bind nothing
ezchain-node serve --config <file>   # start both transports concurrently; Enter/EOF stops
ezchain-node help
```

`check` prints a typed summary (`ezchain-node/1 check ok`, config path, validator set version and
count, RPC bind and payload limit, `mode=kernel-only live=owner-blocked`) and exits `0`. A bad
command line, bad config or bad validator set is a typed error on stderr with exit code `2`.

`serve` binds the configured `rpc_bind`, starts the RPC server and the P2P node, prints a typed
startup summary with both **actual** bound addresses, and waits on **one** shutdown signal
(Enter/EOF on stdin). The signal is the RPC server's graceful-shutdown future; when it drains, the
same `RunningNode::shutdown` path closes the P2P node. Library callers pass a `tokio::sync::oneshot`
receiver to `ezchain_node::start` instead, which is what the integration test does on ephemeral
ports.

## Configuration

`config.example.json` and `validator_set.example.json` are the smallest complete example. The
config is a flat JSON object with one nested `p2p` object:

| Field | Meaning |
|---|---|
| `rpc_bind` | Local socket address for the RPC listener (`127.0.0.1:<port>` in this slice). |
| `rpc_max_payload_bytes` | Single home for the RPC request body / frame limit. |
| `validator_set_path` | Path to the operator-provisioned validator-set JSON file. |
| `self_id` | This node's opaque id; must be a member of the validator set. |
| `max_validators` | Ceiling passed to `ValidatorSet::new`; no number is fixed by the node. |
| `p2p.max_envelope_bytes` / `p2p.max_payload_bytes` | Envelope validation limits. |
| `p2p.max_frame_bytes` | Wire frame bound; envelope + 8-byte digest must fit inside it. |
| `p2p.dedup_capacity` / `queue_capacity` / `fanout` | Bounded kernel parameters. |
| `p2p.outbound_queue_capacity` / `accepted_queue_capacity` | Bounded per-connection and accept queues. |

A relative `validator_set_path` is resolved from the process's current working directory, not
from the config file's directory.

The validator-set file is `{ "version": "<label>", "peers": ["<opaque-id>", ...] }`. All fields
are required: an unknown field, a missing field, a wrong type, a zero size or a size that cannot
fit its length prefix is refused (CONTRACT I7 — one home for every number; no silent defaults).
The transport's P2P node binds an **ephemeral `127.0.0.1` port by design**; the node does not invent
a configurable P2P port that the transport does not accept (ADR-0051 is Proposed and fixes no
network policy).

## What the node does NOT do

- **No keys, signatures or authentication.** Peer ids and the envelope digest are opaque labels /
  non-cryptographic content ids, not identities. Node authentication remains a separate decision.
- **No money.** No money arithmetic, wallet address, ledger write, seal, vault key, venue call,
  quote, order or executor path. **Live money and vault keys stay owner-blocked** (ADR-0026,
  ADR-0035, ADR-0050/0051; CONTRACT I1, I4). There is no live-mode flag in the config.
- **No product API.** Auth resolution and real dispatch handlers are future work; the RPC server
  currently runs the kernel's pure typed ack handlers only. This does not move the Python API.
- **No peer discovery, handshake, consensus or persistence.** ADR-0051's validator model, quorum
  and fork choice are still Proposed; the operator supplies peers out of band and the node makes
  no protocol-policy decision.

## Dependencies — why each one

| Dependency | Why |
|---|---|
| `ezchain-rpc-server`, `ezchain-p2p-transport` | The in-repo transports being composed (path deps). |
| `ezchain-rpc`, `ezchain-p2p` | In-repo kernels whose public config types the node must build (path deps). |
| `serde`, `serde_json` (`=1.0.229`, `=1.0.154`) | The only *new* external crates. The repo already uses JSON as its typed wire/config format; a hand-rolled JSON parser would add its own unbounded edge cases. `Deserialize` is hand-written (no `serde_derive` proc-macro), so the parser stays small and every field is validated. |
| `tokio` (`=1.54.1`) | Required to run the two async transports and the single shutdown signal. It is the same exact version both transports already pin, not a new runtime surface. |

No HTTP client, CLI framework, crypto crate, money library or secret store is added.

## Gate

```bash
export CARGO_HOME="$SCRATCH/cargo" RUSTUP_HOME="$SCRATCH/rustup" PATH="$SCRATCH/cargo/bin:$PATH"
cd rust
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
```

All tests bind ephemeral `127.0.0.1` ports and use files in the OS temp directory; no external
network is contacted.
