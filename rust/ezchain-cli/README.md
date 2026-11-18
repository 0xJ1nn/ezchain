# ezchain-cli

The shipped operator CLI, owned by Rust under ADR-0050. It is a small static binary with no
external crates, no network and no file I/O: it reads arguments and writes stdout/stderr.

## Commands

| Command | Output |
|---|---|
| `ezchain-cli version` | the CLI version, from the crate manifest |
| `ezchain-cli mode` | `PAPER` — always. There is no flag to request live. |
| `ezchain-cli contracts` | the short ADR-0050 language map, one row per layer (`key -> language`) |
| `ezchain-cli contracts --all` | the full ADR-0050 table, with the layer descriptions |
| `ezchain-cli contracts --layer <key>` | one row of that map (`core`, `p2p`, `consensus`, `rpc`, `execution`, `cli`, `indexer`, `ai`, `strategy`, `ui`) |
| `ezchain-cli policy` | the ADR-0052 ownership view: Rust trust/execution, Python application/intelligence, the RPC/API overlap and the integration contract |
| `ezchain-cli policy --layer <key>` | one section: `rust`, `python`, `overlap` or `integration` |
| `ezchain-cli gate` | the required port gates from the documented policy: `record → diff → gate → bench`, the three Rust gate commands and the local `tools/ci.sh` gate |
| `ezchain-cli refuse <verb>` | demonstrate the typed refusal for a named operator verb (always exits `2`) |
| `ezchain-cli help` | usage |

All of these are pure reads of documented policy. None opens a file, a socket or a database,
and none performs money arithmetic. `contracts`, `policy`, `gate` and `refuse` render strings
from `policy.rs`; `refuse` deliberately reuses the same typed refusal as an operator verb typed
at the top level, so it exits `2` and prints the same owner-blocked message.

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
