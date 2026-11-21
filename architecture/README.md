# The language map

`language-map.json` is the machine-readable form of the language policy: ADR-0050 (what each
language owns), ADR-0052 (how the boundary is integrated) and ADR-0053 (the deployment target,
the Rust freeze, and the enforcement below). The prose stays in
[`docs/DECISIONS.md`](../docs/DECISIONS.md); this directory is what a tool reads.

The checker is [`tools/check-language-map.py`](../tools/check-language-map.py), and it runs in
`tools/ci.sh` as step 2/9. Contrast on the generated tokens is the separate step 4/9.
`tests/architecture/test_language_map.py` proves it catches a violation, not only that the
tree is clean today.

## What the map declares

| Key | Meaning |
|---|---|
| `languages` | the three languages and the one rule each must not break |
| `python_layers` | every Python package: its path, role, and the workspace layers it may import |
| `python_entry_points` | the only modules allowed to import `ezchain_bridge` |
| `rust_crates.entry_points` | the two shipped entry points: the PyO3 extension and the CLI |
| `rust_crates.shipping` | crates a shipped entry point links, transitively |
| `rust_crates.frozen` | crates that build and test but are not deployed |
| `rust_crates.shipping_unwired` | proven crates that no shipped entry point calls yet, each with the missing wiring |
| `rust_crates.unfreeze_condition` | the one condition that lifts the freeze |
| `rust_crates.binaries` | which crates build an executable, and whether an installer ships it |
| `third_party_seams` | optional dependencies that may be imported only inside named packages |
| `forbidden` | shapes no pull request may add |
| `known_violations` | recorded import inversions, with file paths that may only shrink |
| `money_parity` | the Python oracle and the Rust crates that must match it before a flip |
| `switch_triggers` | the measured condition that would change a decision |

## Adding something

1. A new Python package or Rust crate **must** get an entry. The checker fails without one,
   and that failure is the review point.
2. A new import between layers is either allowed in `may_import` or wrong. Widening the list
   to silence the checker defeats the file; move the shared code down a layer instead.
3. A new bridge client is a fallback waiting to happen. Route through
   `ezchain_api.native` or `ezchain_executor.native`.
4. A fourth language needs an ADR, not a commit.
5. Freezing is not deleting. A frozen crate keeps its tests and loses its consumers; if a
   shipping crate depends on it, the checker fails, because a freeze that the shipped product
   links is a freeze in name only.
6. "Shipping" is a reachability claim, not an intention. The checker walks the crate graph
   from the two entry points; a crate nothing links must be in `shipping_unwired` with the
   wiring it is missing. When that wiring lands, the stale entry fails the gate and moves up.

## Why the freeze exists

The product is a workstation, not a chain. Five crates and most of a sixth implement a
replicated-log topology that no user has asked for and no second machine can join: no Python
caller, no service, no installer, no desktop resource starts them. They are kept because they
are good, tested work and a cheap rehearsal of a topology the product might need; they are
frozen because counting them as migration progress made the Rust programme look twice its
product value while the deployable image could not answer a request.

They unfreeze when a second, independently operated machine must agree on the same ordered
tape — together with an accepted P2P/consensus design. That is a machine that exists, runs,
and must not diverge. An ambition to federate is not that machine.

The same honesty applies one level down. `ezchain-seal`, `ezchain-replay`,
`ezchain-fingerprint`, `ezchain-executor`, `ezchain-protection`, `ezchain-scheduler` and
`ezchain-flow` are good, tested kernels that no shipped entry point links. They are listed as
unwired rather than shipping, and the highest-value one to fix is the seal: the product claims
"replay or it didn't happen" while the Python write path seals with its own implementation.
