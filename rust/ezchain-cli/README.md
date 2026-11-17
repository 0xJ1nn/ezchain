# ezchain-cli

The shipped operator CLI, owned by Rust under ADR-0050. It is a small static binary with no
external crates, no network and no file I/O: it reads arguments and writes stdout/stderr.

## Commands

| Command | Output |
|---|---|
| `ezchain-cli version` | the CLI version, from the crate manifest |
| `ezchain-cli mode` | `PAPER` — always. There is no flag to request live. |
| `ezchain-cli contracts` | the ADR-0050 language map, one row per layer |
| `ezchain-cli contracts --layer <key>` | one row of that map (`core`, `p2p`, `consensus`, `rpc`, `execution`, `cli`, `indexer`, `ai`, `strategy`, `ui`) |
| `ezchain-cli help` | usage |

## Refusals

Every operator verb that could touch a database, a key, a venue or money is recognised and
**refused** with a typed error naming the category, plus the message that live mode is
owner-blocked (ADR-0035/0050). Refused verbs include the `tools/ops` verbs (`backup`,
`restore`, `verify`, `rehearse`, `drop`, `drill`, `chain`) and the money/key/venue verbs
(`order`, `place-order`, `trade`, `sign`, `key`, `quote`, `live`). Trailing arguments cannot
bypass the refusal. Nothing is connected, opened or signed.

Exit codes follow `tools/ops/cli.py`: `0` on success, `2` on a parse error or a refusal.

## Gate

From `rust/`, with the pinned toolchain:

```bash
cargo fmt --all -- --check
cargo clippy --workspace --all-targets -- -D warnings
cargo test --workspace
cargo run -p ezchain-cli -- version
```

`args.rs` is the oracle: every command and every error branch has a unit test. `run()` in
`lib.rs` is the pure end-to-end seam; `main.rs` only prints and exits.
